import os
import json
import time
import requests
import xmlrpc.client
from datetime import datetime
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass
from pathlib import Path
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from loguru import logger
from dotenv import load_dotenv
import schedule
import argparse

# Cargar variables de entorno
load_dotenv()

class CodeNormalizer:
    """Utilidad estática para normalizar códigos de productos"""

    @staticmethod
    def normalize_code(code: str) -> str:
        """Normalizar código de producto para matching robusto"""
        if not code or pd.isna(code):
            return ""

        # Convertir a string y limpiar
        code_str = str(code).strip()

        # Eliminar espacios extras y normalizar
        code_str = ' '.join(code_str.split())  # Eliminar espacios dobles
        code_str = code_str.upper()  # Convertir a mayúsculas para matching insensible a mayúsculas

        # Eliminar caracteres problemáticos comunes en códigos
        chars_to_remove = ['.', '-', '_', '/', '(', ')', '[', ']', ' ']
        for char in chars_to_remove:
            code_str = code_str.replace(char, '')

        return code_str.strip()

@dataclass
class ScrapingConfig:
    """Configuración del scraper para producción"""

    # URLs del sistema
    base_url: str = "https://www.prautopartes.com.ar/"
    catalog_url: str = "https://www.prautopartes.com.ar/catalogo"
    api_url: str = "https://www.prautopartes.com.ar/api/Articulos/Buscar"

    # Directorios
    output_dir: str = os.getenv("OUTPUT_DIR", "./output")
    logs_dir: str = os.getenv("PM2_LOG_DIR", "./logs")

    # Archivos de entrada (desde variables de entorno)
    odoo_products_file: str = os.getenv("ODOO_PRODUCTS_FILE", "Producto (product.template).xlsx")
    merged_output_file: str = os.getenv("MERGED_OUTPUT_FILE", "productos_merged.csv")

    # Configuración Odoo (desde variables de entorno)
    odoo_url: str = os.getenv("ODOO_URL", "http://localhost:8069")
    odoo_db: str = os.getenv("ODOO_DB", "odoo")
    odoo_user: str = os.getenv("ODOO_USER", "admin")
    odoo_password: str = os.getenv("ODOO_PASSWORD", "admin")
    send_to_odoo: bool = os.getenv("SEND_TO_ODOO", "false").lower() == "true"
    merged_supplier_filter: Optional[str] = os.getenv("MERGED_SUPPLIER_FILTER")  # Filtro por proveedor para CSV merged

    # Configuración de rendimiento
    page_timeout: int = int(os.getenv("PAGE_TIMEOUT", "15"))  
    request_delay: float = float(os.getenv("REQUEST_DELAY", "0.2")) 
    window_size: str = "1920,1080"
    max_workers: int = int(os.getenv("MAX_WORKERS", "1")) 

    # Configuración de logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_retention_days: int = 7

    # Configuración Chrome
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"

    def __post_init__(self):
        """Validación de configuración después de la inicialización"""
        # Validar credenciales obligatorias
        if not os.getenv("PRAUTO_USERNAME") or not os.getenv("PRAUTO_PASSWORD"):
            raise ValueError("❌ PRAUTO_USERNAME y PRAUTO_PASSWORD son obligatorias en .env")

        # Crear directorios necesarios
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)

    def get_output_filename(self) -> str:
        """Generar nombre del archivo con fecha actual y timestamp"""
        today = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%H%M%S")
        return f"articulos_{today}_{timestamp}.csv"

    def get_output_path(self) -> Path:
        """Obtener ruta completa del archivo de salida"""
        return Path(self.output_dir) / self.get_output_filename()

    def get_log_path(self) -> Path:
        """Obtener ruta del archivo de log diario"""
        today = datetime.now().strftime("%Y-%m-%d")
        return Path(self.logs_dir) / f"scraper_{today}.log"

    def get_odoo_products_path(self) -> Path:
        """Obtener ruta completa del archivo de productos Odoo"""
        return Path(self.output_dir) / self.odoo_products_file

    def get_merged_output_path(self) -> Path:
        """Obtener ruta completa del archivo merged de salida"""
        return Path(self.output_dir) / self.merged_output_file

class OdooConnector:
    """Clase para manejar la conexión con Odoo"""

    def __init__(self, config: ScrapingConfig):
        self.url = config.odoo_url
        self.db = config.odoo_db
        self.username = config.odoo_user
        self.password = config.odoo_password
        self.uid = None
        self.models = None
        # Configuración de reintentos para manejar rate limiting
        self.max_retries = 5
        self.initial_retry_delay = 2.0  # segundos

    def _execute_with_retry(self, func, *args, **kwargs):
        """Ejecutar función de Odoo con reintentos y backoff exponencial"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                error_msg = str(e)

                # Verificar si es error de rate limiting (429) o error de conexión
                is_rate_limit = '429' in error_msg or 'Too Many Requests' in error_msg
                is_connection_error = ('Connection' in error_msg or 'timeout' in error_msg.lower() or
                                      'Temporally' in error_msg or 'temporarily' in error_msg.lower())

                if is_rate_limit or is_connection_error:
                    if attempt < self.max_retries - 1:
                        # Backoff exponencial: 2s, 4s, 8s, 16s, 32s
                        delay = self.initial_retry_delay * (2 ** attempt)
                        logger.warning(f"⚠️ Rate limit detectado (intento {attempt + 1}/{self.max_retries}). "
                                     f"Esperando {delay}s antes de reintentar...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"❌ Máximo de reintentos alcanzado para llamada Odoo")

                # Si no es rate limit o es el último intento, propagar el error
                raise

        # Si llegamos aquí, todos los reintentos fallaron
        raise last_error

    def execute_kw(self, db, uid, password, model, method, domain, kwargs_dict=None):
        """Wrapper para execute_kw con reintentos automáticos para rate limiting"""
        if kwargs_dict is None:
            kwargs_dict = {}

        def _do_execute():
            return self.models.execute_kw(db, uid, password, model, method, domain, kwargs_dict)

        return self._execute_with_retry(_do_execute)

    def connect(self) -> bool:
        """Establecer conexión con Odoo"""
        try:
            # Conectarse al common endpoint para autenticación
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = common.authenticate(self.db, self.username, self.password, {})

            if not self.uid:
                logger.error("Falló la autenticación con Odoo")
                return False

            # Conectarse al object endpoint
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
            logger.info(f"✅ Conexión establecida con Odoo (UID: {self.uid})")
            return True

        except Exception as e:
            logger.error(f"Error al conectar con Odoo: {e}")
            return False

    def search_product_by_code(self, product_code: str) -> Optional[int]:
        """Buscar producto por código solo con coincidencias exactas y normalizadas (sin like)"""
        if not self.models:
            return None

        try:
            # 1. Primero buscar coincidencia exacta
            product_ids = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'search_read',
                [[['default_code', '=', product_code]]],
                {'fields': ['id', 'default_code']}
            )

            if product_ids:
                logger.info(f"Producto encontrado (exacto): {product_code} (ID: {product_ids[0]['id']})")
                return product_ids[0]['id']

            # 2. Si no encuentra coincidencia exacta, buscar versión normalizada
            # Normalizar el código de búsqueda
            normalized_code = CodeNormalizer.normalize_code(product_code)

            # Obtener todos los productos con códigos (para matching normalizado)
            all_products = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'search_read',
                [[['default_code', '!=', False]]],
                {'fields': ['id', 'default_code']}
            )

            # Buscar coincidencia normalizada
            for product in all_products:
                odoo_code = str(product.get('default_code', '')).strip()
                if odoo_code:
                    normalized_odoo_code = CodeNormalizer.normalize_code(odoo_code)
                    if normalized_code and normalized_odoo_code and normalized_code == normalized_odoo_code:
                        logger.info(f"Producto encontrado (normalizado): {product_code} -> {odoo_code} (ID: {product['id']})")
                        return product['id']

            logger.info(f"Producto no encontrado: {product_code}")
            return None

        except Exception as e:
            logger.error(f"Error al buscar producto {product_code}: {e}")
            return None

  
    def update_matched_product(self, product_data: Dict) -> Dict:
        """Actualizar producto coincidente con nueva lógica:
        1. Cargar stock en ubicación TODO/Stock/StockSCRAP (siempre, incluso si es 0)
        2. Actualizar info de compra con proveedor 'PR Autopartes (Scraping)'
        3. Establecer regla de reposición en '-35'
        NOTA: No se modifica precioLista (list_price) para mantener precio de venta original
        """
        if not self.models:
            return {"success": False, "error": "No conectado a Odoo"}

        try:
            product_code = product_data.get('codigo', '')
            existing_product_id = self.search_product_by_code(product_code)

            if not existing_product_id:
                return {"success": False, "error": f"Producto {product_code} no encontrado en Odoo"}

            logger.info(f"🔄 Actualizando producto coincidente: {product_code} (ID: {existing_product_id})")

            # 1. Cargar stock en ubicación TODO/Stock/StockSCRAP (siempre, incluso si es 0)
            scraping_stock_result = self._update_scraping_stock(existing_product_id, product_data)

            # 2. Actualizar información de compra - COMENTADO
            # purchase_info_result = self._update_purchase_info(existing_product_id, product_data)

            # 3. Establecer regla de reposición en '-35' - COMENTADO
            # replenishment_result = self._update_replenishment_rule(existing_product_id)

            return {
                "success": True,
                "action": "matched_updated",
                "product_id": existing_product_id,
                "product_code": product_code,
                "stock_updated": scraping_stock_result,
                "purchase_updated": {"success": True, "skipped": True, "note": "Actualización de proveedor deshabilitada"},
                "replenishment_updated": {"success": True, "skipped": True, "note": "Reglas de reposición deshabilitadas"}
            }

        except Exception as e:
            logger.error(f"Error al actualizar producto coincidente: {e}")
            return {"success": False, "error": str(e)}

    def update_matched_product_optimized(self, product_data: Dict, cached_data: Dict) -> Dict:
        """🚀 ACTUALIZAR PRODUCTO USANDO DATOS CACHEADOS - MUCHO MÁS RÁPIDO
        1. Cargar stock en ubicación TODO/Stock/StockSCRAP (usando location_id cacheado)
        2. Actualizar info de compra con proveedor cacheado
        3. Establecer regla de reposición (usando reglas cacheadas)
        """
        if not self.models:
            return {"success": False, "error": "No conectado a Odoo"}

        try:
            product_code = product_data.get('codigo', '')

            # 🔥 Usar información cacheada del producto
            product_info = cached_data.get('product_info', {}).get(product_code)
            if not product_info:
                return {"success": False, "error": f"Producto {product_code} no encontrado en datos cacheados"}

            existing_product_id = product_info['product_id']
            template_id = product_info['template_id']

            logger.info(f"🚀 Actualizando producto {product_code} con datos cacheados (ID: {existing_product_id})")

            # 1. Cargar stock usando location_id cacheado
            scraping_stock_result = self._update_scraping_stock_optimized(
                existing_product_id,
                product_data,
                cached_data['scraping_location_id'],
                cached_data['kits_info']
            )

            # 2. Actualizar información de compra usando supplier_id cacheado - COMENTADO
            # purchase_info_result = self._update_purchase_info_optimized(
            #     existing_product_id,
            #     product_data,
            #     cached_data['supplier_id']
            # )

            # 3. Establecer regla de reposición usando reglas cacheadas - COMENTADO
            # replenishment_result = self._update_replenishment_rule_optimized(
            #     existing_product_id,
            #     template_id,
            #     product_code,
            #     cached_data['scraping_location_id'],
            #     cached_data['existing_rules']
            # )

            return {
                "success": True,
                "action": "matched_updated_optimized",
                "product_id": existing_product_id,
                "product_code": product_code,
                "stock_updated": scraping_stock_result,
                "purchase_updated": {"success": True, "skipped": True, "note": "Actualización de proveedor deshabilitada"},
                "replenishment_updated": {"success": True, "skipped": True, "note": "Reglas de reposición deshabilitadas"},
                "optimization_used": True
            }

        except Exception as e:
            logger.error(f"Error al actualizar producto coincidente optimizado: {e}")
            return {"success": False, "error": str(e)}

    def _update_scraping_stock(self, product_id: int, product_data: Dict) -> Dict:
        """Actualizar stock del producto en ubicación TODO/Stock/StockSCRAP (siempre, incluso si es 0)"""
        try:
            # Buscar ubicación TODO/Stock/StockSCRAP
            todo_stock_scrap_location_id = self._get_depo_scraping_location()
            if not todo_stock_scrap_location_id:
                return {"success": False, "error": "Ubicación TODO/Stock/StockSCRAP no encontrada"}

            # Obtener disponibilidad del producto y aplicar lógica inversa
            disponibilidad = product_data.get('disponibilidad', 0)
            disponibilidad = int(disponibilidad) if disponibilidad else 0
            # Si disponibilidad es 0, cargar 1. Si es 1 o 2, cargar 0
            stock_quantity = 1 if disponibilidad == 0 else 0

            logger.info(f"📦 Actualizando stock en TODO/Stock/StockSCRAP: {product_data.get('codigo')} - {stock_quantity} unidades")

            # Verificar si el producto es un kit antes de intentar actualizar stock
            try:
                product_info = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.product', 'read',
                    [[product_id]],
                    {'fields': ['product_tmpl_id', 'type']}
                )

                if product_info:
                    template_id = product_info[0]['product_tmpl_id'][0]

                    # Verificar si el producto es un kit (tiene boms)
                    boms = self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'mrp.bom', 'search_read',
                        [[['product_tmpl_id', '=', template_id]]],
                        {'fields': ['id', 'type'], 'limit': 1}
                    )

                    if boms:
                        logger.warning(f"⚠️ Producto {product_data.get('codigo')} es un kit. No se puede actualizar stock directamente.")
                        logger.info(f"💡 Para kits, considere actualizar el stock de sus componentes en su lugar.")
                        return {"success": False, "error": "Producto tipo kit - no se puede actualizar stock directamente", "is_kit": True}

            except Exception as check_e:
                logger.warning(f"⚠️ No se pudo verificar si el producto es un kit: {check_e}")

            # Siempre actualizar o crear inventario (incluso si stock_quantity es 0)

            # Buscar si ya existe un registro de inventario para este producto en esta ubicación
            existing_quants = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.quant', 'search_read',
                [[['product_id', '=', product_id], ['location_id', '=', todo_stock_scrap_location_id]]],
                {'fields': ['id', 'quantity']}
            )

            if existing_quants:
                # Actualizar cantidad existente
                quant_id = existing_quants[0]['id']
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.quant', 'write',
                    [[quant_id], {'quantity': stock_quantity}]
                )
                logger.info(f"📦 Stock actualizado en TODO/Stock/StockSCRAP: {product_data.get('codigo')} - {stock_quantity} unidades")
            else:
                # Crear nuevo registro de inventario
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.quant', 'create',
                    [{
                        'product_id': product_id,
                        'location_id': todo_stock_scrap_location_id,
                        'quantity': stock_quantity,
                        'available_quantity': stock_quantity
                    }]
                )
                logger.info(f"📦 Stock creado en TODO/Stock/StockSCRAP: {product_data.get('codigo')} - {stock_quantity} unidades")

            return {"success": True, "quantity": stock_quantity}

        except Exception as e:
            error_msg = str(e)
            if "Debe actualizar la cantidad de componentes" in error_msg:
                logger.warning(f"⚠️ Producto {product_data.get('codigo')} es un kit - no se puede actualizar stock directamente")
                return {"success": False, "error": "Producto tipo kit - debe actualizar stock de componentes", "is_kit": True}

            logger.error(f"Error al actualizar stock en TODO/Stock/StockSCRAP: {e}")
            return {"success": False, "error": str(e)}

    def _update_replenishment_rule(self, product_id: int) -> Dict:
        """Establecer regla de reposición en '-35' para el producto con debugging mejorado"""
        try:
            # Obtener el template_id del producto
            product_info = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'read',
                [[product_id]],
                {'fields': ['product_tmpl_id', 'default_code', 'name']}
            )

            if not product_info:
                return {"success": False, "error": "No se pudo obtener información del producto"}

            template_id = product_info[0]['product_tmpl_id'][0]
            product_code = product_info[0].get('default_code', 'N/A')
            product_name = product_info[0].get('name', 'N/A')

            logger.info(f"🔍 Analizando regla de reposición para producto: {product_code} - {product_name[:30]}...")
            logger.info(f"📋 Template ID: {template_id} | Product ID: {product_id}")

            # Búsqueda más amplia de reglas existentes para debugging
            existing_rules = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse.orderpoint', 'search_read',
                [[['product_tmpl_id', '=', template_id]]],
                {'fields': ['id', 'product_min_qty', 'product_max_qty', 'location_id', 'warehouse_id']}
            )

            # También buscar reglas por product_id (alternativa)
            rules_by_product = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse.orderpoint', 'search_read',
                [[['product_id', '=', product_id]]],
                {'fields': ['id', 'product_min_qty', 'product_max_qty', 'location_id', 'warehouse_id']}
            )

            logger.info(f"🔎 Reglas encontradas por template_id: {len(existing_rules)}")
            logger.info(f"🔎 Reglas encontradas por product_id: {len(rules_by_product)}")

            # Combinar reglas encontradas
            all_rules = existing_rules + rules_by_product

            # Eliminar duplicados por ID
            unique_rules = {}
            for rule in all_rules:
                if rule['id'] not in unique_rules:
                    unique_rules[rule['id']] = rule

            all_unique_rules = list(unique_rules.values())
            logger.info(f"📊 Total reglas únicas encontradas: {len(all_unique_rules)}")

            if all_unique_rules:
                # Actualizar la primera regla encontrada
                rule_id = all_unique_rules[0]['id']
                old_min = all_unique_rules[0].get('product_min_qty', 'N/A')
                old_max = all_unique_rules[0].get('product_max_qty', 'N/A')
                location_id = all_unique_rules[0].get('location_id', ['N/A'])[0]

                logger.info(f"🔄 Actualizando regla existente ID:{rule_id}")
                logger.info(f"📈 Valores anteriores: Min={old_min}, Max={old_max}, Location={location_id}")
                logger.info(f"📈 Nuevos valores: Min=-35, Max=-34")

                update_result = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'write',
                    [[rule_id], {
                        'product_min_qty': -35,
                        'product_max_qty': -34
                    }]
                )

                if update_result:
                    logger.info(f"✅ Regla de reposición actualizada exitosamente: {rule_id}")
                    return {"success": True, "action": "updated", "rule_id": rule_id, "min_qty": -35}
                else:
                    logger.error(f"❌ Error al actualizar regla: {rule_id}")
                    return {"success": False, "error": "Error al actualizar regla existente"}
            else:
                # Crear nueva regla de reposición
                logger.info(f"➕ No se encontraron reglas existentes, creando nueva regla...")

                # Buscar ubicación TODO/Stock/StockSCRAP para asociarla a la regla
                todo_stock_scrap_location_id = self._get_depo_scraping_location()
                if not todo_stock_scrap_location_id:
                    logger.error(f"❌ No se puede crear regla de reposición sin ubicación TODO/Stock/StockSCRAP")
                    return {"success": False, "error": "Ubicación TODO/Stock/StockSCRAP no encontrada - no se puede crear regla de reposición"}

                logger.info(f"🏭 Usando ubicación ID: {todo_stock_scrap_location_id}")

                new_rule_data = {
                    'product_tmpl_id': template_id,
                    'product_id': product_id,
                    'location_id': todo_stock_scrap_location_id,
                    'product_min_qty': -35,
                    'product_max_qty': -34,
                    'qty_multiple': 1,
                    'name': f"Rule {product_code} - VLANTE"
                }

                logger.info(f"📝 Datos de nueva regla: {new_rule_data}")

                rule_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'create',
                    [new_rule_data]
                )

                if rule_id:
                    logger.info(f"✅ Regla de reposición creada exitosamente: {rule_id} - Mínimo: -35")
                    return {"success": True, "action": "created", "rule_id": rule_id, "min_qty": -35}
                else:
                    logger.error(f"❌ Error al crear regla de reposición")
                    return {"success": False, "error": "Error al crear nueva regla"}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Error al actualizar regla de reposición: {error_msg}")
            # Log del stack trace completo para debugging
            import traceback
            logger.error(f"📋 Stack trace: {traceback.format_exc()}")
            return {"success": False, "error": error_msg}

    def _update_purchase_info(self, product_id: int, product_data: Dict) -> Dict:
        """Actualizar información de compra con proveedor 'PR Autopartes (Scraping)'"""
        try:
            # Buscar o crear proveedor 'PR Autopartes (Scraping)'
            supplier_id = self._get_or_create_supplier()
            if not supplier_id:
                return {"success": False, "error": "No se pudo crear/obtener proveedor"}

            # Validar y procesar precio de costo
            try:
                precio_costo = float(product_data.get('precioCosto', 0))
            except (ValueError, TypeError):
                logger.warning(f"⚠️ Precio de costo inválido para producto {product_data.get('codigo')}: {product_data.get('precioCosto')}")
                precio_costo = 0.0

            # Validar y procesar cantidad de disponibilidad (stock)
            try:
                disponibilidad = int(product_data.get('disponibilidad', 0))
                # Asegurar que la cantidad mínima no sea negativa y tenga un valor razonable
                min_qty = max(1, disponibilidad) if disponibilidad > 0 else 1
                logger.info(f"📊 Stock disponible para {product_data.get('codigo')}: {disponibilidad} unidades")
            except (ValueError, TypeError):
                logger.warning(f"⚠️ Disponibilidad inválida para producto {product_data.get('codigo')}: {product_data.get('disponibilidad')}")
                disponibilidad = 0
                min_qty = 1

            # Actualizar precio de costo del producto solo si es válido
            if precio_costo > 0:
                try:
                    self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'product.product', 'write',
                        [[product_id], {'standard_price': precio_costo}]
                    )
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo actualizar precio de costo: {e}")

            # Crear o actualizar información de proveedor (seller)
            # Validar campos obligatorios
            product_code = product_data.get('codigo', '').strip()
            product_name = product_data.get('descripcion', '').strip()

            if not product_code:
                logger.warning(f"⚠️ Producto sin código, omitiendo info de proveedor")
                return {"success": False, "error": "Producto sin código válido"}

            # Usar el product_template_id en lugar de product_id para supplierinfo
            try:
                product_template_data = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.product', 'read',
                    [[product_id]],
                    {'fields': ['product_tmpl_id']}
                )
                template_id = product_template_data[0]['product_tmpl_id'][0]
            except Exception as e:
                logger.error(f"Error obteniendo template_id: {e}")
                return {"success": False, "error": f"Error obteniendo template_id: {str(e)}"}

            seller_info = {
                'partner_id': supplier_id,  # Corregido: 'name' -> 'partner_id'
                'product_tmpl_id': template_id,  # Usar template_id en lugar de product_id
                'price': precio_costo,
                'min_qty': min_qty,  # Usar la cantidad real de stock disponible
                'delay': 1,  # 1 día de entrega
                'product_code': product_code,
                'product_name': product_name[:100] if product_name else '',  # Limitar longitud
            }

            # Buscar si ya existe un seller para este producto y proveedor
            existing_sellers = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.supplierinfo', 'search_read',
                [[['product_tmpl_id', '=', template_id], ['partner_id', '=', supplier_id]]],
                {'fields': ['id']}
            )

            if existing_sellers:
                # Actualizar seller existente
                seller_id = existing_sellers[0]['id']
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.supplierinfo', 'write',
                    [[seller_id], seller_info]
                )
                logger.info(f"🛒 Info de compra actualizada: {product_code} - Precio: ${precio_costo} - Cantidad mínima: {min_qty} (Stock: {disponibilidad})")
            else:
                # Crear nuevo seller
                seller_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.supplierinfo', 'create',
                    [seller_info]
                )
                logger.info(f"🛒 Info de compra creada: {product_code} - Precio: ${precio_costo} - Cantidad mínima: {min_qty} (Stock: {disponibilidad})")

            return {"success": True, "supplier_id": supplier_id, "price": precio_costo, "template_id": template_id}

        except Exception as e:
            logger.error(f"Error al actualizar info de compra: {e}")
            return {"success": False, "error": str(e)}

    def _get_scraping_location(self) -> Optional[int]:
        """Obtener ID de la ubicación 'Scraping' dentro del almacén VLANTE 2 - FUNCIÓN OBSOLETA, usar _get_depo_scraping_location en su lugar"""
        try:
            # Primero buscar el almacén VLANTE 2 por su nombre corto VLANT
            vlante_warehouses = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse', 'search_read',
                [[['code', '=', 'VLANT']]],
                {'fields': ['id', 'name', 'code']}
            )

            if not vlante_warehouses:
                logger.error("❌ Almacén VLANTE 2 (código VLANT) no encontrado")
                return None

            vlante_warehouse = vlante_warehouses[0]
            logger.info(f"✅ Almacén VLANTE 2 encontrado: {vlante_warehouse['name']} (ID: {vlante_warehouse['id']})")

            # Buscar la ubicación VLANT/Scraping (location_id del almacén)
            # En Odoo, las ubicaciones internas del almacén usualmente siguen el patrón: Warehouse Name/Location Name
            scraping_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'Scraping'], ['usage', '=', 'internal'],
                  ['warehouse_id', '=', vlante_warehouse['id']]]],
                {'fields': ['id', 'name', 'complete_name', 'warehouse_id']}
            )

            if scraping_locations:
                location = scraping_locations[0]
                logger.info(f"✅ Ubicación VLANT/Scraping encontrada: {location['complete_name']} (ID: {location['id']})")
                return location['id']

            # Si no encuentra la ubicación Scraping específica, FALLAR con error claro
            logger.error("❌ UBICACIÓN OBLIGATORIA 'Scraping' NO ENCONTRADA en el almacén VLANTE 2")
            logger.error("❌ Debe crear la ubicación 'Scraping' dentro del almacén VLANTE 2 manualmente en Odoo")
            logger.error(f"❌ Almacén VLANTE 2 encontrado: {vlante_warehouse['name']} (ID: {vlante_warehouse['id']})")
            logger.error("❌ La ubicación debe ser: VLANT/Scraping con uso 'Internal'")
            return None

        except Exception as e:
            logger.error(f"❌ Error al buscar ubicación VLANT/Scraping: {e}")
            return None

    def _get_depo_real_location(self) -> Optional[int]:
        """Obtener ID de la ubicación 'DEPO existencias' dentro de TODO/Stock"""
        try:
            # Primero buscar el almacén padre 'TODO'
            todo_warehouses = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse', 'search_read',
                [[['name', '=', 'TODO']]],
                {'fields': ['id', 'name']}
            )

            if not todo_warehouses:
                logger.error("❌ Almacén 'TODO' no encontrado")
                return None

            todo_warehouse = todo_warehouses[0]
            logger.info(f"✅ Almacén TODO encontrado: {todo_warehouse['name']} (ID: {todo_warehouse['id']})")

            # Buscar la ubicación 'Stock' dentro de TODO
            stock_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'Stock'], ['usage', '=', 'internal'],
                  ['warehouse_id', '=', todo_warehouse['id']]]],
                {'fields': ['id', 'name', 'complete_name']}
            )

            if not stock_locations:
                logger.error("❌ Ubicación 'Stock' no encontrada dentro del almacén TODO")
                return None

            stock_location = stock_locations[0]
            logger.info(f"✅ Ubicación Stock encontrada: {stock_location['complete_name']} (ID: {stock_location['id']})")

            # Buscar la ubicación 'DEPO existencias' dentro de Stock
            depo_existencias_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'DEPO existencias'], ['usage', '=', 'internal'],
                  ['location_id', '=', stock_location['id']]]],
                {'fields': ['id', 'name', 'complete_name', 'location_id']}
            )

            if depo_existencias_locations:
                location = depo_existencias_locations[0]
                logger.info(f"✅ Ubicación TODO/Stock/DEPO existencias encontrada: {location['complete_name']} (ID: {location['id']})")
                return location['id']

            logger.error("❌ Ubicación 'DEPO existencias' no encontrada dentro de TODO/Stock")
            return None

        except Exception as e:
            logger.error(f"❌ Error al buscar ubicación TODO/Stock/DEPO existencias: {e}")
            return None

    def _get_depo_scraping_location(self) -> Optional[int]:
        """Obtener ID de la ubicación 'StockSCRAP' dentro de TODO/Stock"""
        try:
            # Primero buscar el almacén padre 'TODO'
            todo_warehouses = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse', 'search_read',
                [[['name', '=', 'TODO']]],
                {'fields': ['id', 'name']}
            )

            if not todo_warehouses:
                logger.error("❌ Almacén 'TODO' no encontrado")
                return None

            todo_warehouse = todo_warehouses[0]
            logger.info(f"✅ Almacén TODO encontrado: {todo_warehouse['name']} (ID: {todo_warehouse['id']})")

            # Buscar la ubicación 'Stock' dentro de TODO
            stock_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'Stock'], ['usage', '=', 'internal'],
                  ['warehouse_id', '=', todo_warehouse['id']]]],
                {'fields': ['id', 'name', 'complete_name']}
            )

            if not stock_locations:
                logger.error("❌ Ubicación 'Stock' no encontrada dentro del almacén TODO")
                return None

            stock_location = stock_locations[0]
            logger.info(f"✅ Ubicación Stock encontrada: {stock_location['complete_name']} (ID: {stock_location['id']})")

            # Buscar la ubicación 'StockSCRAP' dentro de Stock
            stock_scrap_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'StockSCRAP'], ['usage', '=', 'internal'],
                  ['location_id', '=', stock_location['id']]]],
                {'fields': ['id', 'name', 'complete_name', 'location_id']}
            )

            if stock_scrap_locations:
                location = stock_scrap_locations[0]
                logger.info(f"✅ Ubicación TODO/Stock/StockSCRAP encontrada: {location['complete_name']} (ID: {location['id']})")
                return location['id']

            logger.error("❌ Ubicación 'StockSCRAP' no encontrada dentro de TODO/Stock")
            return None

        except Exception as e:
            logger.error(f"❌ Error al buscar ubicación TODO/Stock/StockSCRAP: {e}")
            return None

    def _get_scraping_location_by_name(self, location_name: str) -> Optional[int]:
        """Obtener ID de una ubicación de scraping dentro de TODO/Stock por nombre exacto
        
        Args:
            location_name: Nombre exacto de la ubicación (ej: 'SV - Scraping')
        
        Returns:
            ID de la ubicación o None si no existe
        """
        try:
            if not self.models:
                return None

            # Buscar almacén TODO
            warehouses = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse', 'search_read',
                [[['name', '=', 'TODO']]],
                {'fields': ['id', 'name', 'lot_stock_id']}
            )

            if not warehouses:
                logger.error("❌ Almacén 'TODO' no encontrado")
                return None

            warehouse = warehouses[0]

            # Buscar ubicación 'Stock' dentro de TODO
            stock_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'Stock'], ['usage', '=', 'internal']]],
                {'fields': ['id', 'name', 'complete_name']}
            )

            if not stock_locations:
                logger.error("❌ Ubicación 'Stock' no encontrada")
                return None

            stock_location = stock_locations[0]

            # Buscar la ubicación específica dentro de Stock
            target_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', location_name], ['usage', '=', 'internal'],
                  ['location_id', '=', stock_location['id']]]],
                {'fields': ['id', 'name', 'complete_name', 'location_id']}
            )

            if target_locations:
                location = target_locations[0]
                logger.info(f"✅ Ubicación encontrada: {location['complete_name']} (ID: {location['id']})")
                return location['id']

            logger.error(f"❌ Ubicación '{location_name}' no encontrada dentro de TODO/Stock")
            return None

        except Exception as e:
            logger.error(f"❌ Error al buscar ubicación TODO/Stock/{location_name}: {e}")
            return None

    def _get_or_create_supplier(self) -> Optional[int]:
        """Obtener o crear proveedor 'PR Autopartes (Scraping)'"""
        try:
            # Buscar proveedor existente
            suppliers = self.models.execute_kw(
                self.db, self.uid, self.password,
                'res.partner', 'search_read',
                [[['name', '=', 'PR Autopartes (Scraping)'], ['supplier_rank', '>', 0]]],
                {'fields': ['id', 'name']}
            )

            if suppliers:
                return suppliers[0]['id']

            # Crear nuevo proveedor
            logger.info("Creando proveedor 'PR Autopartes (Scraping)'...")
            supplier_id = self.models.execute_kw(
                self.db, self.uid, self.password,
                'res.partner', 'create',
                [{
                    'name': 'PR Autopartes (Scraping)',
                    'company_type': 'company',
                    'supplier_rank': 1,
                    'customer_rank': 0,
                    'is_company': True,
                    'street': 'Fray Luis Beltrán 2121',
                    'city': 'Bs As (B1714HSK) Ituzaingó',
                    'country_id': 10,  # Argentina (ajustar según configuración)
                    'email': 'prautopartes@gmail.com',
                    'phone': '54 11 2076-7025',
                    'comment': 'Proveedor automático generado por sistema de scraping - PR Autopartes'
                }]
            )
            logger.info(f"✅ Proveedor 'PR Autopartes (Scraping)' creado con ID: {supplier_id}")
            return supplier_id

        except Exception as e:
            logger.error(f"Error al crear/obtener proveedor: {e}")
            return None

    def _get_supplier_id_by_name(self, supplier_name: str) -> Optional[int]:
        """Obtener el ID de un proveedor por su nombre"""
        try:
            if not self.models:
                logger.error("No conectado a Odoo")
                return None

            suppliers = self.models.execute_kw(
                self.db, self.uid, self.password,
                'res.partner', 'search_read',
                [[['name', '=', supplier_name], ['supplier_rank', '>', 0]]],
                {'fields': ['id', 'name']}
            )

            if suppliers:
                logger.info(f"✅ Proveedor encontrado: {supplier_name} (ID: {suppliers[0]['id']})")
                return suppliers[0]['id']

            logger.warning(f"⚠️ Proveedor no encontrado: {supplier_name}")
            return None

        except Exception as e:
            logger.error(f"Error al buscar proveedor por nombre: {e}")
            return None

    def _get_product_ids_by_supplier(self, supplier_name: str) -> set:
        """Obtener los IDs de productos que tienen a un proveedor específico como PRIMER proveedor (principal)"""
        try:
            if not self.models:
                logger.error("No conectado a Odoo")
                return set()

            # Primero obtener el ID del proveedor
            supplier_id = self._get_supplier_id_by_name(supplier_name)
            if not supplier_id:
                return set()

            # ESTRATEGIA OPTIMIZADA (2 consultas eficientes):
            # 1. Obtener los templates que tienen a este proveedor
            # 2. Obtener TODOS los sellerinfo de SOLO esos templates
            #    para poder determinar cuál es el proveedor principal de cada uno

            # Paso 1: Buscar templates que tienen a este proveedor asociado
            supplier_sellerinfos = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.supplierinfo', 'search_read',
                [[['partner_id', '=', supplier_id]]],
                {'fields': ['product_tmpl_id', 'product_id', 'sequence']}
            )

            if not supplier_sellerinfos:
                return set()

            # Extraer los template_ids únicos
            template_ids = list(set(
                s['product_tmpl_id'][0] for s in supplier_sellerinfos
                if s.get('product_tmpl_id')
            ))

            logger.info(f"🔍 {len(template_ids)} templates tienen al proveedor '{supplier_name}'")

            # Paso 2: Obtener TODOS los sellerinfo de SOLO esos templates
            # Esto nos permite comparar y ver quién es el proveedor principal
            all_sellers_of_templates = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.supplierinfo', 'search_read',
                [[['product_tmpl_id', 'in', template_ids]]],
                {'fields': ['product_tmpl_id', 'partner_id', 'sequence'], 'order': 'sequence ASC'}
            )

            # Procesar en memoria: para cada template, encontrar el proveedor principal
            template_main_supplier = {}
            for seller in all_sellers_of_templates:
                tmpl = seller.get('product_tmpl_id')
                if not tmpl:
                    continue
                template_id = tmpl[0]

                # La primera vez que vemos un template (están ordenados por sequence),
                # ese proveedor es el principal
                if template_id not in template_main_supplier:
                    template_main_supplier[template_id] = seller['partner_id'][0]

            # Filtrar: solo templates donde este proveedor es el PRINCIPAL
            templates_where_is_main = {
                tid for tid, main_supp in template_main_supplier.items()
                if main_supp == supplier_id
            }

            logger.info(f"🔍 De {len(template_ids)} templates del proveedor, {len(templates_where_is_main)} lo tienen como PRINCIPAL")

            # Paso 3: Recolectar product_ids y templates que necesitan buscar variantes
            product_ids = set()
            templates_needing_variants = []

            for seller in supplier_sellerinfos:
                template_id = seller.get('product_tmpl_id', [None])[0] if seller.get('product_tmpl_id') else None

                # Solo procesar si el template tiene este proveedor como principal
                if template_id not in templates_where_is_main:
                    continue

                if 'product_id' in seller and seller['product_id']:
                    # product.product variant específica
                    product_ids.add(seller['product_id'][0])
                elif template_id:
                    # Recopilar template para búsqueda masiva de variantes
                    templates_needing_variants.append(template_id)

            # Paso 4: BÚSQUEDA MASIVA de variantes (1 sola consulta en lugar de N)
            if templates_needing_variants:
                all_variants = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.product', 'search_read',
                    [[['product_tmpl_id', 'in', templates_needing_variants]]],
                    {'fields': ['id', 'product_tmpl_id']}
                )
                for variant in all_variants:
                    product_ids.add(variant['id'])

            logger.info(f"📊 Se encontraron {len(product_ids)} productos con '{supplier_name}' como proveedor PRINCIPAL")
            return product_ids

        except Exception as e:
            logger.error(f"Error al obtener productos por proveedor: {e}")
            return set()

    # 🔥 MÉTODOS OPTIMIZADOS CACHEADOS
    def _update_scraping_stock_optimized(self, product_id: int, product_data: Dict, location_id: int, kits_info: set) -> Dict:
        """Actualizar stock usando location_id cacheado y verificación KIT cacheada"""
        try:
            product_code = product_data.get('codigo', '')

            # 🔥 Usar información cacheada de KITs
            product_info = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'read',
                [[product_id]],
                {'fields': ['product_tmpl_id']}
            )

            if product_info:
                template_id = product_info[0]['product_tmpl_id'][0]
                if template_id in kits_info:
                    logger.warning(f"⚠️ Producto {product_code} es un kit (cacheado). No se puede actualizar stock directamente.")
                    return {"success": False, "error": "Producto tipo kit - no se puede actualizar stock directamente", "is_kit": True}

            # Obtener disponibilidad y aplicar lógica inversa
            disponibilidad = product_data.get('disponibilidad', 0)
            disponibilidad = int(disponibilidad) if disponibilidad else 0
            # Si disponibilidad es 0, cargar 1. Si es 1 o 2, cargar 0
            stock_quantity = 1 if disponibilidad == 0 else 0

            logger.info(f"📦 Actualizando stock cacheado: {product_code} - {stock_quantity} unidades")

            # Buscar si ya existe registro de inventario (sin buscar location)
            existing_quants = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.quant', 'search_read',
                [[['product_id', '=', product_id], ['location_id', '=', location_id]]],
                {'fields': ['id', 'quantity']}
            )

            if existing_quants:
                # Actualizar cantidad existente
                quant_id = existing_quants[0]['id']
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.quant', 'write',
                    [[quant_id], {'quantity': stock_quantity}]
                )
                logger.info(f"📦 Stock cacheado actualizado: {product_code} - {stock_quantity} unidades")
            else:
                # Crear nuevo registro de inventario
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.quant', 'create',
                    [{
                        'product_id': product_id,
                        'location_id': location_id,
                        'quantity': stock_quantity,
                        'available_quantity': stock_quantity
                    }]
                )
                logger.info(f"📦 Stock cacheado creado: {product_code} - {stock_quantity} unidades")

            return {"success": True, "quantity": stock_quantity}

        except Exception as e:
            logger.error(f"Error al actualizar stock cacheado: {e}")
            return {"success": False, "error": str(e)}

    def _update_purchase_info_optimized(self, product_id: int, product_data: Dict, supplier_id: int) -> Dict:
        """Actualizar información de compra usando supplier_id cacheado"""
        try:
            product_code = product_data.get('codigo', '')
            precio_costo = product_data.get('precioCosto', 0)

            # Obtener template_id
            product_info = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'read',
                [[product_id]],
                {'fields': ['product_tmpl_id']}
            )

            if not product_info:
                return {"success": False, "error": "No se pudo obtener información del producto"}

            template_id = product_info[0]['product_tmpl_id'][0]

            # Buscar info de proveedor existente para este producto
            existing_seller = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.supplierinfo', 'search_read',
                [[['product_tmpl_id', '=', template_id], ['partner_id', '=', supplier_id]]],
                {'fields': ['id', 'price']}
            )

            if existing_seller:
                # Actualizar precio si es diferente
                seller_id = existing_seller[0]['id']
                if float(existing_seller[0]['price']) != float(precio_costo):
                    self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'product.supplierinfo', 'write',
                        [[seller_id], {'price': float(precio_costo)}]
                    )
                    logger.info(f"💰 Precio de compra actualizado cacheado: {product_code} - ${precio_costo}")
                else:
                    logger.info(f"💰 Precio de compra sin cambios: {product_code} - ${precio_costo}")
            else:
                # Crear nueva información de proveedor
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.supplierinfo', 'create',
                    [{
                        'product_tmpl_id': template_id,
                        'partner_id': supplier_id,
                        'price': float(precio_costo),
                        'min_qty': 1,
                        'delay': 3
                    }]
                )
                logger.info(f"💰 Info de compra creada cacheado: {product_code} - ${precio_costo}")

            return {"success": True, "price": precio_costo}

        except Exception as e:
            logger.error(f"Error al actualizar info de compra cacheada: {e}")
            return {"success": False, "error": str(e)}

    def _update_replenishment_rule_optimized(self, product_id: int, template_id: int, product_code: str, location_id: int, existing_rules: Dict) -> Dict:
        """Actualizar regla de reposición usando datos cacheados"""
        try:
            logger.info(f"🔍 Actualizando regla de reposición cacheada: {product_code}")

            # 🔥 Usar reglas cacheadas
            template_rules = existing_rules.get(template_id, [])

            if template_rules:
                # Actualizar la primera regla existente
                rule_id = template_rules[0]['id']
                old_min = template_rules[0].get('product_min_qty', 'N/A')
                old_max = template_rules[0].get('product_max_qty', 'N/A')

                logger.info(f"🔄 Actualizando regla cacheada ID:{rule_id}")
                logger.info(f"📈 Valores anteriores: Min={old_min}, Max={old_max}")
                logger.info(f"📈 Nuevos valores: Min=-35, Max=-34")

                update_result = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'write',
                    [[rule_id], {
                        'product_min_qty': -35,
                        'product_max_qty': -34
                    }]
                )

                if update_result:
                    logger.info(f"✅ Regla cacheada actualizada exitosamente: {rule_id}")
                    return {"success": True, "action": "updated", "rule_id": rule_id, "min_qty": -35}
                else:
                    logger.error(f"❌ Error al actualizar regla cacheada: {rule_id}")
                    return {"success": False, "error": "Error al actualizar regla existente"}
            else:
                # Crear nueva regla usando location_id cacheado
                logger.info(f"➕ Creando nueva regla cacheada para: {product_code}")

                new_rule_data = {
                    'product_tmpl_id': template_id,
                    'product_id': product_id,
                    'location_id': location_id,
                    'product_min_qty': -35,
                    'product_max_qty': -34,
                    'qty_multiple': 1,
                    'name': f"Rule {product_code} - VLANTE"
                }

                rule_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'create',
                    [new_rule_data]
                )

                if rule_id:
                    logger.info(f"✅ Regla cacheada creada exitosamente: {rule_id} - Mínimo: -35")
                    return {"success": True, "action": "created", "rule_id": rule_id, "min_qty": -35}
                else:
                    logger.error(f"❌ Error al crear regla cacheada")
                    return {"success": False, "error": "Error al crear nueva regla"}

        except Exception as e:
            logger.error(f"❌ Error al actualizar regla cacheada: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # 🚀 MÉTODOS BATCH PARA ACTUALIZACIONES MASIVAS - MÁXIMO RENDIMIENTO
    # ========================================================================

    def _preload_existing_quants(self, product_ids: List[int], location_id: int) -> Dict[int, Dict]:
        """Pre-cargar quants existentes para todos los productos en una sola consulta"""
        try:
            if not self.models or not product_ids:
                return {}

            quants = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.quant', 'search_read',
                [[['product_id', 'in', product_ids], ['location_id', '=', location_id]]],
                {'fields': ['id', 'product_id', 'quantity']}
            )

            # Mapear product_id -> quant info
            quants_by_product = {}
            for quant in quants:
                product_id = quant['product_id'][0]
                quants_by_product[product_id] = quant

            return quants_by_product

        except Exception as e:
            logger.error(f"Error al pre-cargar quants: {e}")
            return {}

    def _batch_update_stock_quants(self, products_data: List[Tuple[str, Dict]], cached_data: Dict) -> Dict[str, List]:
        """Actualizar stock de múltiples productos en batch
        Returns: Dict con listas de IDs actualizados/creados y productos con errores
        """
        results = {
            "updated": [],
            "created": [],
            "kits_skipped": [],
            "non_storable_skipped": [],
            "errors": []
        }

        try:
            location_id = cached_data['scraping_location_id']
            kits_info = cached_data['kits_info']
            product_info = cached_data['product_info']

            # Extraer product_ids y preparar mapeo
            product_ids = []
            product_id_to_data = {}
            product_id_to_scraping_code = {}

            for scraping_code, data in products_data:
                info = product_info.get(scraping_code)
                if not info:
                    results["errors"].append({"code": scraping_code, "error": "Producto no encontrado en cache"})
                    continue

                product_id = info['product_id']
                template_id = info['template_id']

                # Verificar si es KIT
                if template_id in kits_info:
                    results["kits_skipped"].append(scraping_code)
                    logger.warning(f"⚠️ Producto {scraping_code} es un kit (cacheado). Saltando actualización de stock.")
                    continue

                # Verificar si es storable (tiene rastreo de inventario activado)
                if not info.get('is_storable', False):
                    results["non_storable_skipped"].append(scraping_code)
                    logger.warning(f"⚠️ Producto {scraping_code} no es storable (sin rastreo de inventario). Saltando actualización de stock.")
                    continue

                product_ids.append(product_id)
                product_id_to_data[product_id] = data
                product_id_to_scraping_code[product_id] = scraping_code

            if not product_ids:
                logger.warning("⚠️ No hay productos válidos para actualizar stock")
                return results

            # Pre-cargar quants existentes en una sola consulta
            logger.info(f"📦 Pre-cargando quants existentes para {len(product_ids)} productos...")
            existing_quants = self._preload_existing_quants(product_ids, location_id)

            # Preparar operaciones batch
            quants_to_update = []  # List of (quant_id, quantity, code)
            quants_to_create = []  # List of (product_id, quantity, code)

            for product_id in product_ids:
                scraping_code = product_id_to_scraping_code[product_id]
                data = product_id_to_data[product_id]

                disponibilidad = data.get('disponibilidad', 0)
                disponibilidad = int(disponibilidad) if disponibilidad else 0
                # Lógica inversa: si disponibilidad es 0, cargar 1. Si es 1 o 2, cargar 0
                stock_quantity = 1 if disponibilidad == 0 else 0

                if product_id in existing_quants:
                    # Actualizar quant existente
                    quant_id = existing_quants[product_id]['id']
                    quants_to_update.append((quant_id, stock_quantity, scraping_code))
                else:
                    # Crear nuevo quant
                    quants_to_create.append((product_id, stock_quantity, scraping_code))

            # Ejecutar actualizaciones en batch
            if quants_to_update:
                logger.info(f"🔄 Actualizando {len(quants_to_update)} quants existentes en batch...")
                for quant_id, quantity, code in quants_to_update:
                    try:
                        self.models.execute_kw(
                            self.db, self.uid, self.password,
                            'stock.quant', 'write',
                            [[quant_id], {'quantity': quantity}]
                        )
                        results["updated"].append(code)
                    except Exception as e:
                        results["errors"].append({"code": code, "error": str(e)})

            # Crear nuevos quants en batch
            if quants_to_create:
                logger.info(f"➕ Creando {len(quants_to_create)} nuevos quants en batch...")
                quant_records = []
                for product_id, quantity, code in quants_to_create:
                    quant_records.append({
                        'product_id': product_id,
                        'location_id': location_id,
                        'quantity': quantity,
                        'available_quantity': quantity
                    })

                try:
                    # Crear todos los quants en una sola llamada
                    created_ids = self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'stock.quant', 'create',
                        [quant_records]
                    )
                    results["created"].extend([code for _, _, code in quants_to_create])
                    logger.info(f"✅ {len(created_ids)} quants creados exitosamente")
                except Exception as e:
                    logger.error(f"❌ Error al crear quants en batch: {e}")
                    # Fallback: crear individualmente
                    for product_id, quantity, code in quants_to_create:
                        try:
                            self.models.execute_kw(
                                self.db, self.uid, self.password,
                                'stock.quant', 'create',
                                [{
                                    'product_id': product_id,
                                    'location_id': location_id,
                                    'quantity': quantity,
                                    'available_quantity': quantity
                                }]
                            )
                            results["created"].append(code)
                        except Exception as e2:
                            results["errors"].append({"code": code, "error": str(e2)})

            logger.info(f"📦 Resumen batch stock: {len(results['updated'])} actualizados, {len(results['created'])} creados, {len(results['kits_skipped'])} kits saltados, {len(results['non_storable_skipped'])} no storable saltados, {len(results['errors'])} errores")
            return results

        except Exception as e:
            logger.error(f"❌ Error en batch update de stock: {e}")
            results["errors"].append({"code": "batch", "error": str(e)})
            return results

    def _preload_existing_sellers(self, template_ids: List[int], supplier_id: int) -> Dict[int, Dict]:
        """Pre-cargar sellers existentes para todos los templates en una sola consulta"""
        try:
            if not self.models or not template_ids:
                return {}

            sellers = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.supplierinfo', 'search_read',
                [[['product_tmpl_id', 'in', template_ids], ['partner_id', '=', supplier_id]]],
                {'fields': ['id', 'product_tmpl_id', 'price']}
            )

            # Mapear template_id -> seller info
            sellers_by_template = {}
            for seller in sellers:
                template_id = seller['product_tmpl_id'][0]
                sellers_by_template[template_id] = seller

            return sellers_by_template

        except Exception as e:
            logger.error(f"Error al pre-cargar sellers: {e}")
            return {}

    def _batch_update_supplierinfo(self, products_data: List[Tuple[str, Dict]], cached_data: Dict) -> Dict[str, List]:
        """Actualizar información de compra de múltiples productos en batch
        Returns: Dict con listas de IDs actualizados/creados y productos con errores
        """
        results = {
            "updated": [],
            "created": [],
            "errors": []
        }

        try:
            supplier_id = cached_data['supplier_id']
            product_info = cached_data['product_info']

            # Extraer template_ids y preparar mapeo
            template_ids = []
            template_id_to_data = {}
            template_id_to_scraping_code = {}

            for scraping_code, data in products_data:
                info = product_info.get(scraping_code)
                if not info:
                    results["errors"].append({"code": scraping_code, "error": "Producto no encontrado en cache"})
                    continue

                template_id = info['template_id']
                template_ids.append(template_id)
                template_id_to_data[template_id] = (data, scraping_code)
                template_id_to_scraping_code[template_id] = scraping_code

            if not template_ids:
                logger.warning("⚠️ No hay productos válidos para actualizar supplierinfo")
                return results

            # Pre-cargar sellers existentes en una sola consulta
            logger.info(f"🛒 Pre-cargando sellers existentes para {len(template_ids)} templates...")
            existing_sellers = self._preload_existing_sellers(template_ids, supplier_id)

            # Preparar operaciones batch
            sellers_to_update = []  # List of (seller_id, price, code)
            sellers_to_create = []  # List of (template_id, price, code, product_code, product_name)

            for template_id in template_ids:
                data, scraping_code = template_id_to_data[template_id]

                precio_costo = float(data.get('precioCosto', 0))
                product_code = data.get('codigo', '').strip()
                product_name = data.get('descripcion', '').strip()[:100]

                if template_id in existing_sellers:
                    # Verificar si necesita actualización
                    seller = existing_sellers[template_id]
                    if float(seller['price']) != precio_costo:
                        sellers_to_update.append((seller['id'], precio_costo, scraping_code))
                else:
                    sellers_to_create.append((template_id, precio_costo, scraping_code, product_code, product_name))

            # Ejecutar actualizaciones en batch
            if sellers_to_update:
                logger.info(f"🔄 Actualizando {len(sellers_to_update)} sellers existentes en batch...")
                # Odoo no soporta batch write con diferentes valores en una sola llamada
                # Pero podemos agrupar por precio
                price_to_ids = {}
                for seller_id, price, code in sellers_to_update:
                    if price not in price_to_ids:
                        price_to_ids[price] = []
                    price_to_ids[price].append((seller_id, code))

                for price, items in price_to_ids.items():
                    ids_to_update = [item[0] for item in items]
                    codes = [item[1] for item in items]
                    try:
                        self.models.execute_kw(
                            self.db, self.uid, self.password,
                            'product.supplierinfo', 'write',
                            [ids_to_update, {'price': price}]
                        )
                        results["updated"].extend(codes)
                    except Exception as e:
                        for code in codes:
                            results["errors"].append({"code": code, "error": str(e)})

            # Crear nuevos sellers en batch
            if sellers_to_create:
                logger.info(f"➕ Creando {len(sellers_to_create)} nuevos sellers en batch...")
                seller_records = []
                for template_id, price, code, product_code, product_name in sellers_to_create:
                    seller_records.append({
                        'product_tmpl_id': template_id,
                        'partner_id': supplier_id,
                        'price': price,
                        'min_qty': 1,
                        'delay': 3
                    })

                try:
                    # Crear todos los sellers en una sola llamada
                    created_ids = self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'product.supplierinfo', 'create',
                        [seller_records]
                    )
                    results["created"].extend([code for _, _, code, _, _ in sellers_to_create])
                    logger.info(f"✅ {len(created_ids)} sellers creados exitosamente")
                except Exception as e:
                    logger.error(f"❌ Error al crear sellers en batch: {e}")
                    # Fallback: crear individualmente
                    for template_id, price, code, product_code, product_name in sellers_to_create:
                        try:
                            self.models.execute_kw(
                                self.db, self.uid, self.password,
                                'product.supplierinfo', 'create',
                                [{
                                    'product_tmpl_id': template_id,
                                    'partner_id': supplier_id,
                                    'price': price,
                                    'min_qty': 1,
                                    'delay': 3
                                }]
                            )
                            results["created"].append(code)
                        except Exception as e2:
                            results["errors"].append({"code": code, "error": str(e2)})

            logger.info(f"🛒 Resumen batch supplierinfo: {len(results['updated'])} actualizados, {len(results['created'])} creados, {len(results['errors'])} errores")
            return results

        except Exception as e:
            logger.error(f"❌ Error en batch update de supplierinfo: {e}")
            results["errors"].append({"code": "batch", "error": str(e)})
            return results

    def _batch_update_replenishment_rules(self, products_data: List[Tuple[str, Dict]], cached_data: Dict) -> Dict[str, List]:
        """Actualizar reglas de reposición de múltiples productos en batch
        Returns: Dict con listas de IDs actualizados/creados y productos con errores
        """
        results = {
            "updated": [],
            "created": [],
            "errors": []
        }

        try:
            location_id = cached_data['scraping_location_id']
            existing_rules = cached_data['existing_rules']
            product_info = cached_data['product_info']

            # Preparar operaciones batch
            rules_to_update = []  # List of (rule_id, code)
            rules_to_create = []  # List of (template_id, product_id, code, product_code)

            for scraping_code, data in products_data:
                info = product_info.get(scraping_code)
                if not info:
                    results["errors"].append({"code": scraping_code, "error": "Producto no encontrado en cache"})
                    continue

                template_id = info['template_id']
                product_id = info['product_id']
                product_code = scraping_code

                template_rules = existing_rules.get(template_id, [])

                if template_rules:
                    # Actualizar regla existente
                    rule_id = template_rules[0]['id']
                    rules_to_update.append((rule_id, scraping_code))
                else:
                    # Crear nueva regla
                    rules_to_create.append((template_id, product_id, scraping_code, product_code))

            # Ejecutar actualizaciones en batch
            if rules_to_update:
                logger.info(f"🔄 Actualizando {len(rules_to_update)} reglas de reposición existentes en batch...")
                # Agrupar actualizaciones
                rule_ids = [rule_id for rule_id, _ in rules_to_update]
                codes = [code for _, code in rules_to_update]

                try:
                    # Actualizar todas las reglas con los mismos valores en batch
                    self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'stock.warehouse.orderpoint', 'write',
                        [rule_ids, {
                            'product_min_qty': -35,
                            'product_max_qty': -34
                        }]
                    )
                    results["updated"].extend(codes)
                    logger.info(f"✅ {len(rule_ids)} reglas actualizadas exitosamente")
                except Exception as e:
                    logger.error(f"❌ Error al actualizar reglas en batch: {e}")
                    # Fallback: actualizar individualmente
                    for rule_id, code in rules_to_update:
                        try:
                            self.models.execute_kw(
                                self.db, self.uid, self.password,
                                'stock.warehouse.orderpoint', 'write',
                                [[rule_id], {
                                    'product_min_qty': -35,
                                    'product_max_qty': -34
                                }]
                            )
                            results["updated"].append(code)
                        except Exception as e2:
                            results["errors"].append({"code": code, "error": str(e2)})

            # Crear nuevas reglas en batch
            if rules_to_create:
                logger.info(f"➕ Creando {len(rules_to_create)} nuevas reglas de reposición en batch...")
                rule_records = []
                for template_id, product_id, code, product_code in rules_to_create:
                    rule_records.append({
                        'product_tmpl_id': template_id,
                        'product_id': product_id,
                        'location_id': location_id,
                        'product_min_qty': -35,
                        'product_max_qty': -34,
                        'qty_multiple': 1,
                        'name': f"Rule {product_code} - VLANTE"
                    })

                try:
                    # Crear todas las reglas en una sola llamada
                    created_ids = self.models.execute_kw(
                        self.db, self.uid, self.password,
                        'stock.warehouse.orderpoint', 'create',
                        [rule_records]
                    )
                    results["created"].extend([code for _, _, code, _ in rules_to_create])
                    logger.info(f"✅ {len(created_ids)} reglas creadas exitosamente")
                except Exception as e:
                    logger.error(f"❌ Error al crear reglas en batch: {e}")
                    # Fallback: crear individualmente
                    for template_id, product_id, code, product_code in rules_to_create:
                        try:
                            self.models.execute_kw(
                                self.db, self.uid, self.password,
                                'stock.warehouse.orderpoint', 'create',
                                [{
                                    'product_tmpl_id': template_id,
                                    'product_id': product_id,
                                    'location_id': location_id,
                                    'product_min_qty': -35,
                                    'product_max_qty': -34,
                                    'qty_multiple': 1,
                                    'name': f"Rule {product_code} - VLANTE"
                                }]
                            )
                            results["created"].append(code)
                        except Exception as e2:
                            results["errors"].append({"code": code, "error": str(e2)})

            logger.info(f"📋 Resumen batch replenishment: {len(results['updated'])} actualizados, {len(results['created'])} creados, {len(results['errors'])} errores")
            return results

        except Exception as e:
            logger.error(f"❌ Error en batch update de replenishment rules: {e}")
            results["errors"].append({"code": "batch", "error": str(e)})
            return results

    def update_matched_products_batch(self, products_data: List[Tuple[str, Dict]], cached_data: Dict) -> Dict:
        """🚀 ACTUALIZAR MÚLTIPLES PRODUCTOS EN BATCH - MÁXIMA OPTIMIZACIÓN
        Args:
            products_data: List of tuples (scraping_code, product_data)
            cached_data: Dict con datos cacheados (location_id, supplier_id, product_info, kits_info, existing_rules)

        Realiza todas las actualizaciones en batch:
        1. Stock (stock.quant) para todos los productos
        2. Info de compra (product.supplierinfo) para todos los productos
        3. Reglas de reposición (stock.warehouse.orderpoint) para todos los productos
        """
        if not self.models:
            return {"success": False, "error": "No conectado a Odoo"}

        logger.info(f"🚀 Iniciando actualización BATCH de {len(products_data)} productos...")

        start_time = datetime.now()
        overall_results = {
            "success": True,
            "total_products": len(products_data),
            "stock": {},
            "supplierinfo": {},
            "replenishment": {},
            "errors": []
        }

        try:
            # 1. Batch update de stock
            logger.info("📦 Fase 1/3: Actualizando stock en batch...")
            stock_results = self._batch_update_stock_quants(products_data, cached_data)
            overall_results["stock"] = stock_results

            # 2. Batch update de supplierinfo - COMENTADO
            # logger.info("🛒 Fase 2/3: Actualizando info de compra en batch...")
            # supplier_results = self._batch_update_supplierinfo(products_data, cached_data)
            # overall_results["supplierinfo"] = supplier_results

            # 3. Batch update de reglas de reposición - COMENTADO
            # logger.info("📋 Fase 3/3: Actualizando reglas de reposición en batch...")
            # replenishment_results = self._batch_update_replenishment_rules(products_data, cached_data)
            # overall_results["replenishment"] = replenishment_results

            # Calcular estadísticas finales
            duration = datetime.now() - start_time

            successful_products = len(set(
                stock_results.get("updated", []) + stock_results.get("created", [])  # +
                # supplier_results.get("updated", []) + supplier_results.get("created", []) +
                # replenishment_results.get("updated", []) + replenishment_results.get("created", [])
            ))

            total_errors = (
                len(stock_results.get("errors", []))  # +
                # len(supplier_results.get("errors", [])) +
                # len(replenishment_results.get("errors", []))
            )

            logger.info("✅ Actualización BATCH completada!")
            logger.info(f"   📊 Productos procesados: {len(products_data)}")
            logger.info(f"   📦 Stock: {len(stock_results.get('updated', []))} actualizados, {len(stock_results.get('created', []))} creados, {len(stock_results.get('kits_skipped', []))} kits saltados")
            # logger.info(f"   🛒 Supplierinfo: {len(supplier_results.get('updated', []))} actualizados, {len(supplier_results.get('created', []))} creados")  # COMENTADO
            # logger.info(f"   📋 Replenishment: {len(replenishment_results.get('updated', []))} actualizados, {len(replenishment_results.get('created', []))} creados")  # COMENTADO
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   🚀 Velocidad: {len(products_data)/duration.total_seconds():.2f} productos/segundo")
            logger.info(f"   🔥 AHORRO: ~{len(products_data) * 6} llamadas XML-RPC individuales evitadas")

            if total_errors > 0:
                overall_results["errors"] = stock_results.get("errors", [])  # +
                # supplier_results.get("errors", []) +
                # replenishment_results.get("errors", [])
                logger.warning(f"   ⚠️ Errores totales: {total_errors}")

            return overall_results

        except Exception as e:
            logger.error(f"❌ Error crítico en actualización BATCH: {e}")
            return {"success": False, "error": str(e)}


class PrAutoParteScraper:
    """Scraper profesional para PrAutoParte"""
    
    def __init__(self, config: ScrapingConfig):
        self.config = config
        self.driver: Optional[webdriver.Chrome] = None
        self.session = requests.Session()

        # Configurar logging
        self._setup_logging()

        # Obtener credenciales de variables de entorno
        self.username = os.getenv("PRAUTO_USERNAME")
        self.password = os.getenv("PRAUTO_PASSWORD")

        if not self.username or not self.password:
            logger.error("Credenciales no encontradas en variables de entorno")
            raise ValueError("Definir PRAUTO_USERNAME y PRAUTO_PASSWORD en archivo .env")

        # Inicializar conector Odoo
        self.odoo_connector = OdooConnector(config)

        # Mapeo: código scraping → código Odoo (para usar código Odoo al actualizar)
        self.scraping_to_odoo_code: Dict[str, str] = {}
        # Códigos coincidentes - se cargarán con el filtro correspondiente
        self.matched_codes: set = set()
    
    def _setup_logging(self) -> None:
        """Configurar sistema de logging profesional (solo consola, sin archivo)"""
        # Configurar nivel de log desde variable de entorno
        log_level = self.config.log_level.upper()

        # Log a consola para PM2 (en producción) - SIN archivo
        logger.remove()  # Remover handler por defecto
        logger.add(
            lambda msg: print(msg, end="", flush=True),
            level=log_level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            colorize=True
        )

        logger.info(f"🔧 Logging configurado - Nivel: {log_level} - Solo consola (sin archivo)")

  
    def _load_matched_codes(self, df_scraped: pd.DataFrame = None, supplier_filter: Optional[str] = None) -> set:
        """Cargar datasets existentes y calcular códigos coincidentes SIN descargar

        Args:
            df_scraped: DataFrame con datos del scraping (opcional)
            supplier_filter: Nombre del proveedor para filtrar productos (opcional)
        """
        try:
            logger.info("🔍 Analizando coincidencias...")

            # 1. Cargar dataset de productos Odoo desde CSV/Excel existente
            df_productos = self._load_odoo_products_from_backup()

            # 2. Filtrar productos por proveedor si se especifica
            if supplier_filter and df_productos is not None:
                logger.info(f"🔍 Filtrando productos por proveedor: '{supplier_filter}'")
                product_ids_by_supplier = self.odoo_connector._get_product_ids_by_supplier(supplier_filter)

                if product_ids_by_supplier:
                    original_count = len(df_productos)
                    df_productos = df_productos[df_productos['id'].isin(product_ids_by_supplier)]
                    filtered_count = len(df_productos)
                    logger.info(f"✅ Productos filtrados por proveedor: {original_count} → {filtered_count}")
                else:
                    logger.warning(f"⚠️ No se encontraron productos para el proveedor '{supplier_filter}'")

            # 3. Usar DataFrame del scraping proporcionado o cargar desde CSV
            if df_scraped is not None:
                df_articulos = df_scraped
                logger.info("📊 Usando datos del scraping en memoria")
            else:
                df_articulos = self._get_latest_scraping_results()
                if df_articulos is None:
                    return set()

            if df_productos is None:
                logger.error("❌ No se pudo cargar el dataset de productos Odoo")
                return set()

            logger.info(f"📊 Dataset Productos (Odoo): {len(df_productos)} registros")
            logger.info(f"📊 Dataset Artículos (Scraping): {len(df_articulos)} registros")

            # Obtener códigos de productos (Referencia interna/default_code)
            codigos_productos = set()
            codigos_productos_norm = {}  # Cambiado a dict para mapear normalized -> original

            if 'default_code' in df_productos.columns:
                df_productos_clean = df_productos.dropna(subset=['default_code'])
                for code in df_productos_clean['default_code']:
                    original_code = str(code).strip()
                    normalized_code = CodeNormalizer.normalize_code(code)
                    if normalized_code:  # Solo agregar si no está vacío después de normalizar
                        codigos_productos.add(original_code)
                        codigos_productos_norm[normalized_code] = original_code
            elif 'Referencia interna' in df_productos.columns:
                df_productos_clean = df_productos.dropna(subset=['Referencia interna'])
                for code in df_productos_clean['Referencia interna']:
                    original_code = str(code).strip()
                    normalized_code = CodeNormalizer.normalize_code(code)
                    if normalized_code:
                        codigos_productos.add(original_code)
                        codigos_productos_norm[normalized_code] = original_code

            # Obtener códigos de artículos con normalización
            codigos_articulos = set()
            codigos_articulos_norm = {}

            if 'codigo' in df_articulos.columns:
                df_articulos_clean = df_articulos.dropna(subset=['codigo'])
                for code in df_articulos_clean['codigo']:
                    original_code = str(code).strip()
                    normalized_code = CodeNormalizer.normalize_code(code)
                    if normalized_code and original_code:
                        codigos_articulos.add(original_code)
                        codigos_articulos_norm[normalized_code] = original_code

            # Encontrar coincidencias exactas (códigos originales)
            matched_codes_exact = codigos_productos.intersection(codigos_articulos)

            # Crear mapeo para coincidencias exactas ( scraping_code == odoo_code )
            for code in matched_codes_exact:
                self.scraping_to_odoo_code[code] = code

            # Encontrar coincidencias normalizadas (matching robusto)
            matched_codes_normalized = set()
            for norm_codigo in codigos_productos_norm:
                if norm_codigo in codigos_articulos_norm:
                    # Guardar el código del scraping (para buscar en scraped_data)
                    scraping_code = codigos_articulos_norm[norm_codigo]
                    odoo_code = codigos_productos_norm[norm_codigo]
                    matched_codes_normalized.add(scraping_code)
                    # Crear mapeo para usar código Odoo al actualizar
                    self.scraping_to_odoo_code[scraping_code] = odoo_code

            # Combinar ambos sets de coincidencias
            matched_codes = matched_codes_exact.union(matched_codes_normalized)

            logger.info(f"✅ Códigos coincidentes exactos: {len(matched_codes_exact)}")
            logger.info(f"🔍 Códigos coincidentes normalizados: {len(matched_codes_normalized)}")
            logger.info(f"🎯 Total códigos coincidentes: {len(matched_codes)}")

            if len(codigos_articulos) > 0:
                logger.info(f"📈 Porcentaje de coincidencia: {len(matched_codes)/len(codigos_articulos)*100:.1f}%")

            return matched_codes

        except Exception as e:
            logger.error(f"❌ Error al cargar códigos coincidentes: {e}")
            return set()

    def _load_odoo_products_from_backup(self) -> Optional[pd.DataFrame]:
        """Cargar productos Odoo desde backup existente SIN descargar"""
        try:
            # Buscar archivo Excel de productos usando variable de entorno
            productos_path = self.config.get_odoo_products_path()

            if productos_path.exists():
                logger.info(f"📁 Cargando productos Odoo desde backup: {productos_path.name}")
                df = pd.read_excel(productos_path)
                logger.info(f"✅ Productos Odoo cargados: {len(df)} registros")
                return df
            else:
                return None

        except Exception as e:
            logger.error(f"❌ Error al cargar backup de productos Odoo: {e}")
            return None

    def _generate_odoo_products_dataset(self) -> Optional[pd.DataFrame]:
        """Extraer productos desde Odoo y guardar como Excel"""
        try:
            logger.info("📥 Extrayendo productos desde Odoo...")

            # Conectar a Odoo
            if not self.odoo_connector.connect():
                logger.error("❌ No se pudo conectar a Odoo para extraer productos")
                return None

            # Obtener ubicación DEPO existencias para extraer stock
            depo_existencia_location_id = self.odoo_connector._get_depo_real_location()
            if not depo_existencia_location_id:
                logger.error("❌ No se encontró ubicación TODO/Stock/DEPO existencias")
                return None

            # Extraer todos los productos sin stock primero
            products_data = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db,
                self.odoo_connector.uid,
                self.odoo_connector.password,
                'product.product', 'search_read',
                [[['sale_ok', '=', True]]],  # Solo productos que se pueden vender
                {
                    'fields': [
                        'id', 'default_code', 'name', 'list_price', 'standard_price',
                        'type', 'sale_ok', 'purchase_ok'
                    ]
                }
            )

            if not products_data:
                logger.warning("⚠️ No se encontraron productos en Odoo")
                return pd.DataFrame()

            # Obtener stock para cada producto desde DEPO existencias
            logger.info("📊 Obteniendo stock desde TODO/Stock/DEPO existencias...")
            product_ids = [p['id'] for p in products_data]

            # Buscar stock quants para todos los productos en DEPO existencias
            stock_quants = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db,
                self.odoo_connector.uid,
                self.odoo_connector.password,
                'stock.quant', 'search_read',
                [[['product_id', 'in', product_ids], ['location_id', '=', depo_existencia_location_id]]],
                {'fields': ['product_id', 'quantity']}
            )

            # Crear diccionario de stock por producto
            stock_by_product = {sq['product_id'][0]: sq['quantity'] for sq in stock_quants}

            # Agregar stock a cada producto
            for product in products_data:
                product_id = product['id']
                product['qty_available'] = stock_by_product.get(product_id, 0)
                product['virtual_available'] = stock_by_product.get(product_id, 0)  # Mismo valor para virtual

            # Convertir a DataFrame
            df = pd.DataFrame(products_data)

            # Mapear campos para consistencia
            df = df.rename(columns={
                'default_code': 'Referencia interna',
                'name': 'Nombre',
                'list_price': 'Precio de venta',
                'standard_price': 'Coste',
                'qty_available': 'Cantidad a la mano'
            })

            # Agregar campos adicionales vacíos para consistencia
            campos_adicionales = [
                'Cantidad pronosticada', 'Decoración de la actividad de excepción',
                'Etiquetas', 'Favorito', 'Marca', 'Precio de venta con impuestos',
                'Precio Tarifa', 'Unidad de medida', 'Código de ARBA', 'Código de barras',
                'Código NCM', 'Código SA', 'Código de producto del proveedor'
            ]

            for campo in campos_adicionales:
                if campo not in df.columns:
                    df[campo] = None

            # Guardar como Excel usando variable de entorno
            productos_path = self.config.get_odoo_products_path()

            # Hacer backup si existe
            if productos_path.exists():
                backup_path = productos_path.with_suffix('.backup.xlsx')
                import shutil
                shutil.copy2(productos_path, backup_path)
                logger.info(f"📄 Backup de productos Odoo creado: {backup_path.name}")

            df.to_excel(productos_path, index=False)
            logger.info(f"✅ Dataset de productos Odoo guardado: {productos_path.name} ({len(df)} productos)")

            return df

        except Exception as e:
            logger.error(f"❌ Error al generar dataset de productos Odoo: {e}")
            return None

    def _get_latest_scraping_results(self) -> Optional[pd.DataFrame]:
        """Obtener resultados más recientes del scraping"""
        try:
            logger.info("📄 Buscando resultados más recientes del scraping...")

            # Buscar archivos CSV de artículos más recientes
            articulos_files = list(Path(self.config.output_dir).glob("articulos_*.csv"))

            if not articulos_files:
                return None

            # Usar el archivo más reciente
            articulos_file = max(articulos_files, key=lambda x: x.stat().st_mtime)
            df = pd.read_csv(articulos_file)

            logger.info(f"✅ Dataset de artículos cargado: {articulos_file.name} ({len(df)} artículos)")

            return df

        except Exception as e:
            logger.error(f"❌ Error al cargar resultados del scraping: {e}")
            return None

    def _get_latest_scraping_results_as_dict(self) -> Dict:
        """Convertir CSV más reciente a formato dict para process_matched_products_optimized"""
        try:
            df = self._get_latest_scraping_results()
            if df is None:
                return {"success": False, "error": "No se encontraron datos de scraping"}

            # Convertir DataFrame a lista de diccionarios como items
            items = df.to_dict('records')

            return {
                "success": True,
                "items": items,
                "total_items": len(items)
            }

        except Exception as e:
            logger.error(f"❌ Error al convertir scraping results a dict: {e}")
            return {"success": False, "error": str(e)}

    def _is_matched_product(self, product_code: str) -> bool:
        """Verificar si un producto tiene coincidencia exacta"""
        return product_code in self.matched_codes
    
    def _get_chrome_driver(self) -> webdriver.Chrome:
        """Crear instancia del driver Chrome/Chromium con configuración optimizada para producción"""
        chrome_options = Options()

        # Configuración básica de rendimiento y estabilidad
        chrome_options.add_argument(f"--window-size={self.config.window_size}")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-popup-blocking")

        # Optimizaciones más conservadoras para evitar conflictos
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")

        # Headless configuration
        if self.config.headless:
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--disable-logging")
            chrome_options.add_argument("--log-level=3")  # Solo errores críticos
        else:
            # Configuración mínima para modo con interfaz
            chrome_options.add_argument("--disable-infobars")
            chrome_options.add_argument("--disable-restore-session-state")

        # Configuración específica para Linux/Chromium en producción
        if os.name == 'posix':  # Linux/Unix
            # Buscar Chromium en múltiples rutas
            chromium_paths = [
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/snap/bin/chromium",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/google-chrome",
                "/opt/google/chrome/chrome",
                "/usr/local/bin/chromium"
            ]

            browser_found = False
            for path in chromium_paths:
                if os.path.exists(path):
                    chrome_options.binary_location = path
                    logger.info(f"✅ Browser encontrado: {path}")
                    browser_found = True
                    break

            if not browser_found:
                logger.warning("⚠️ Chrome/Chromium no encontrado en rutas estándar")

        # Configurar user agent móvil mejorado
        mobile_user_agent = (
            "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
        )
        chrome_options.add_argument(f"--user-agent={mobile_user_agent}")

        # Configuración de timeouts
        chrome_options.page_load_timeout = self.config.page_timeout

        # Configuración de Chrome experimental para evitar conflictos de versión
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # Intentar inicializar ChromeDriver con múltiples métodos
        driver = self._initialize_chromedriver(chrome_options)

        # Esperar a que el navegador esté completamente inicializado
        try:
            driver.execute_script("return document.readyState")
            logger.info("✅ Navegador completamente inicializado")
        except Exception as e:
            logger.warning(f"⚠️ Problema al verificar estado del navegador: {e}")

        logger.info("✅ ChromeDriver inicializado exitosamente")
        return driver

    def _initialize_chromedriver(self, chrome_options: Options) -> webdriver.Chrome:
        """Inicializar ChromeDriver con múltiples métodos de respaldo"""
        methods_tried = []

        # Método 1: ChromeDriver del PATH (más estable)
        try:
            logger.info("🔧 Intentando ChromeDriver del PATH...")
            driver = webdriver.Chrome(options=chrome_options)
            logger.info("✅ ChromeDriver del PATH exitoso")
            return driver

        except Exception as e:
            methods_tried.append(f"PATH ChromeDriver: {str(e)}")
            logger.warning(f"⚠️ ChromeDriver del PATH falló: {e}")

        # Método 2: ChromeDriver instalado via apt (Ubuntu/Debian)
        apt_paths = ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver", "/snap/bin/chromedriver"]
        for apt_path in apt_paths:
            try:
                if os.path.exists(apt_path):
                    logger.info(f"🔧 Intentando ChromeDriver en: {apt_path}")
                    service = Service(apt_path)
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info(f"✅ ChromeDriver en {apt_path} exitoso")
                    return driver
            except Exception as e:
                methods_tried.append(f"Apt ChromeDriver ({apt_path}): {str(e)}")

        # Método 3: webdriver-manager (como última opción por el error de formato)
        try:
            logger.info("🔧 Intentando webdriver-manager...")
            driver_path = ChromeDriverManager().install()
            # Intentar con opciones simplificadas si WebDriver Manager funciona
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info(f"✅ WebDriver Manager exitoso: {driver_path}")
            return driver

        except Exception as e:
            methods_tried.append(f"WebDriver Manager: {str(e)}")
            logger.warning(f"⚠️ WebDriver Manager falló: {e}")

        # Si todos los métodos fallaron, proporcionar error detallado
        error_details = """
        ❌ Error crítico: No se pudo inicializar ChromeDriver

        Métodos intentados:
        {methods}

        🛠️ SOLUCIONES:

        OPCIÓN 1 - Instalación automática (recomendada):
            chmod +x setup_linux.sh && ./setup_linux.sh

        OPCIÓN 2 - Instalación manual Ubuntu/Debian:
            sudo apt update
            sudo apt install -y chromium-browser chromium-chromedriver xvfb

        OPCIÓN 3 - Instalación con Snap:
            sudo snap install chromium
            sudo apt install -y chromium-chromedriver

        OPCIÓN 4 - Verificar versión compatible:
            google-chrome-stable --version
            sudo apt install --only-upgrade chromedriver

        OPCIÓN 5 - Docker (mejor para producción):
            docker-compose up -d

        📚 Para más ayuda, consultar README.md sección Troubleshooting
        """

        logger.error(error_details.format(methods="\n        ".join(methods_tried)))
        raise RuntimeError("No se pudo inicializar ChromeDriver. Revisar logs para soluciones.")
    
    def _wait_and_find_element(self, by: By, selector: str, timeout: int = None) -> object:
        """Buscar elemento con espera explícita"""
        timeout = timeout or self.config.page_timeout
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
            return element
        except TimeoutException:
            logger.error(f"Elemento no encontrado: {selector}")
            raise
    
    def _safe_click(self, by: By, selector: str, timeout: int = None) -> bool:
        """Hacer click de forma segura en un elemento"""
        try:
            element = self._wait_and_find_element(by, selector, timeout)
            element.click()
            return True
        except Exception as e:
            logger.error(f"Error al hacer click en {selector}: {e}")
            return False
    
    def _scroll_to_bottom(self) -> None:
        """Hacer scroll hasta abajo de la página"""
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
    
    def login_and_get_session_data(self) -> Tuple[int, str]:
        """Realizar login y obtener datos de sesión"""
        logger.info("Iniciando proceso de login...")
        
        try:
            self.driver = self._get_chrome_driver()
            self.driver.get(self.config.base_url)
            
            # Login
            self._safe_click(By.XPATH, "//a[@title='Login']")
            time.sleep(1)
            
            username_field = self._wait_and_find_element(By.XPATH, "//input[@name='user']")
            username_field.send_keys(self.username)
            
            password_field = self._wait_and_find_element(By.XPATH, "//input[@name='password']")
            password_field.send_keys(self.password)
            
            time.sleep(1)
            self._safe_click(By.XPATH, "//button[@type='submit' and normalize-space(text())='Ingresar']")
            time.sleep(3)
            
            # Ir al catálogo
            logger.info(f"Navegando al catálogo: {self.config.catalog_url}")
            self.driver.get(self.config.catalog_url)
            time.sleep(5)  # Más tiempo para cargar
            self._scroll_to_bottom()
            
            # Obtener número de páginas - intentar varios selectores
            last_page_button = None
            selectors = [
                "(//button[@class='page-link cursor-hand'])[last()]",
                "(//button[contains(@class, 'page-link')])[last()]",
                "//li[contains(@class, 'page-item')][last()]/button",
                "//ul[contains(@class, 'pagination')]//button[last()]",
            ]
            
            for selector in selectors:
                try:
                    last_page_button = self._wait_and_find_element(By.XPATH, selector, timeout=5)
                    if last_page_button:
                        logger.info(f"Selector encontrado: {selector}")
                        break
                except:
                    logger.debug(f"Selector no encontrado: {selector}")
                    continue
            
            if not last_page_button:
                raise ValueError("No se pudo encontrar el botón de paginación con ningún selector")
            last_page_button.click()
            time.sleep(3)
            self._scroll_to_bottom()
            
            # Obtener token de sesión
            session_json = self.driver.execute_script("return localStorage.getItem('session');")
            if not session_json:
                raise ValueError("No se encontró la sesión en localStorage")
            
            session_data = json.loads(session_json)
            bearer_token = session_data.get("token")
            
            if not bearer_token:
                raise ValueError("Token de autorización no encontrado")
            
            # Obtener número total de páginas (reusar el selector que funcionó)
            num_pages = int(last_page_button.text) + 1
            
            logger.info(f"Login exitoso. Páginas encontradas: {num_pages}")
            logger.info(f"Token obtenido: {bearer_token[:20]}...")
            
            return num_pages, bearer_token
            
        except Exception as e:
            logger.error(f"Error durante el login: {e}")
            raise
        finally:
            if self.driver:
                self.driver.quit()
    
    def _get_request_headers(self, bearer_token: str) -> Dict[str, str]:
        """Generar headers para las peticiones API"""
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Authorization': f'Bearer {bearer_token}',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Origin': 'https://www.prautopartes.com.ar',
            'Referer': 'https://www.prautopartes.com.ar/catalogo',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/139.0.0.0 Mobile Safari/537.36'
        }
    
    def _create_payload(self, page: int) -> str:
        """Crear payload para la petición API"""
        return json.dumps({
            "idMarcas": 0,
            "idRubros": 0,
            "busqueda": "",
            "pagina": page,
            "isNovedades": False,
            "isOfertas": False,
            "equivalencia": ""
        })
    
    def _extract_item_data(self, item: Dict) -> Dict[str, str]:
        """Extraer datos relevantes de un item"""
        return {
            "id": item.get("id"),
            "codigo": item.get("codigo"),
            "marca": item.get("marca"),
            "descripcion": item.get("descripciones", [{}])[0].get("descripcion", ""),
            "precioLista": item.get("precioLista"),
            "precioCosto": item.get("precioCosto"),
            "precioVenta": item.get("precioVenta"),
            "descuentos": item.get("descuentos"),
            "disponibilidad": item.get("disponibilidad"),
            "origen": item.get("origen"),
            "fotos": ", ".join(item.get("fotos", []))
        }

    def _send_to_odoo(self, product_data: Dict) -> bool:
        """Enviar datos de producto coincidente a Odoo"""
        try:
            product_code = product_data.get('codigo', '')
            logger.info(f"🔄 Actualizando producto coincidente: {product_code}")

            result = self.odoo_connector.update_matched_product(product_data)

            if result.get("success"):
                logger.info(f"✅ Producto {product_code} actualizado en Odoo")
                return True
            else:
                logger.error(f"❌ Error al enviar producto {product_code} a Odoo: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"❌ Error inesperado al enviar producto {product_data.get('codigo')} a Odoo: {e}")
            return False

    def _send_to_odoo_optimized(self, product_data: Dict, cached_data: Dict) -> bool:
        """🔥 ENVIAR A ODOO USANDO DATOS CACHEADOS - MÁS RÁPIDO"""
        try:
            product_code = product_data.get('codigo', '')
            logger.info(f"🚀 Actualizando producto {product_code} con datos cacheados...")

            result = self.odoo_connector.update_matched_product_optimized(product_data, cached_data)

            if result.get("success"):
                logger.info(f"✅ Producto {product_code} actualizado en Odoo")
                return True
            else:
                logger.error(f"❌ Error al enviar producto {product_code} a Odoo: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"❌ Error inesperado al enviar producto {product_data.get('codigo')} a Odoo: {e}")
            return False

    def _process_matched_product_from_data(self, product_code: str, scraped_data: Dict) -> Dict:
        """Procesar un producto coincidente usando datos ya scrapeados (sin nueva petición)"""
        try:
            # Buscar el producto en los datos ya scrapeados
            found_product = None
            for item in scraped_data.get("items", []):
                if item.get("codigo") == product_code:
                    found_product = item
                    break

            if not found_product:
                return {"success": False, "error": f"Producto {product_code} no encontrado en datos scrapeados", "code": product_code}

            # Extraer datos del producto encontrado
            extracted_data = self._extract_item_data(found_product)

            return {
                "success": True,
                "data": extracted_data,
                "code": product_code,
                "description": extracted_data.get('descripcion', '')[:50]
            }

        except Exception as e:
            return {"success": False, "error": f"Error procesando producto {product_code}: {e}", "code": product_code}

    def _preload_product_information(self, matched_codes_list: List[str]) -> Dict:
        """Pre-cargar información de productos para evitar búsquedas individuales"""
        logger.info(f"🔍 Pre-cargando información de {len(matched_codes_list)} productos...")

        product_info = {}
        try:
            if not self.odoo_connector.models:
                return product_info

            # Convertir códigos del scraping a códigos de Odoo usando el mapeo
            odoo_codes_list = []
            scraping_to_odoo_local = {}  # Mapeo local: scraping_code → odoo_code
            for scraping_code in matched_codes_list:
                odoo_code = self.scraping_to_odoo_code.get(scraping_code, scraping_code)
                odoo_codes_list.append(odoo_code)
                scraping_to_odoo_local[scraping_code] = odoo_code

            # Buscar productos por códigos de Odoo en batch (con reintentos automáticos)
            products = self.odoo_connector.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'product.product', 'search_read',
                [[['default_code', 'in', odoo_codes_list]]],
                {'fields': ['id', 'default_code', 'product_tmpl_id', 'type', 'is_storable']}
            )

            # Mapear códigos del scraping a información (usando código de Odoo para lookup)
            odoo_code_to_info = {}
            for product in products:
                odoo_code = str(product.get('default_code', '')).strip()
                odoo_code_to_info[odoo_code] = {
                    'product_id': product['id'],
                    'template_id': product.get('product_tmpl_id', [None])[0],
                    'type': product.get('type', 'product'),
                    'is_storable': product.get('is_storable', False),
                    'odoo_code': odoo_code
                }

            # Crear product_info indexado por código del scraping
            for scraping_code in matched_codes_list:
                odoo_code = scraping_to_odoo_local.get(scraping_code, scraping_code)
                if odoo_code in odoo_code_to_info:
                    product_info[scraping_code] = odoo_code_to_info[odoo_code]

            logger.info(f"✅ Información de {len(product_info)} productos precargada")
            return product_info

        except Exception as e:
            logger.error(f"❌ Error al pre-cargar información de productos: {e}")
            return {}

    def _preload_kits_information(self, product_info: Dict) -> set:
        """Pre-cargar información de KITs en una sola consulta"""
        logger.info("🧩 Pre-cargando información de KITs...")

        kits_templates = set()
        try:
            if not self.odoo_connector.models or not product_info:
                return kits_templates

            # Extraer template_ids únicos
            template_ids = list(set(info['template_id'] for info in product_info.values() if info['template_id']))

            if not template_ids:
                return kits_templates

            # Buscar BOMs en batch (con reintentos automáticos)
            boms = self.odoo_connector.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'mrp.bom', 'search_read',
                [[['product_tmpl_id', 'in', template_ids]]],
                {'fields': ['product_tmpl_id', 'type']}
            )

            # Marcar templates que son KITs
            for bom in boms:
                kits_templates.add(bom['product_tmpl_id'][0])

            logger.info(f"✅ Identificados {len(kits_templates)} productos KIT")
            return kits_templates

        except Exception as e:
            logger.error(f"❌ Error al pre-cargar información de KITs: {e}")
            return set()

    def _preload_replenishment_rules(self, product_info: Dict) -> Dict:
        """Pre-cargar reglas de reposición existentes en batch"""
        logger.info("📋 Pre-cargando reglas de reposición existentes...")

        existing_rules = {}
        try:
            if not self.odoo_connector.models or not product_info:
                return existing_rules

            # Extraer template_ids y product_ids únicos
            template_ids = list(set(info['template_id'] for info in product_info.values() if info['template_id']))
            product_ids = [info['product_id'] for info in product_info.values()]

            # Buscar reglas por template_ids (con reintentos automáticos)
            rules_by_template = []
            if template_ids:
                rules_by_template = self.odoo_connector.execute_kw(
                    self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                    'stock.warehouse.orderpoint', 'search_read',
                    [[['product_tmpl_id', 'in', template_ids]]],
                    {'fields': ['id', 'product_tmpl_id', 'product_min_qty', 'product_max_qty', 'location_id', 'warehouse_id']}
                )

            # Buscar reglas por product_ids (con reintentos automáticos)
            rules_by_product = []
            if product_ids:
                rules_by_product = self.odoo_connector.execute_kw(
                    self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                    'stock.warehouse.orderpoint', 'search_read',
                    [[['product_id', 'in', product_ids]]],
                    {'fields': ['id', 'product_tmpl_id', 'product_id', 'product_min_qty', 'product_max_qty', 'location_id', 'warehouse_id']}
                )

            # Combinar y mapear reglas
            all_rules = rules_by_template + rules_by_product
            for rule in all_rules:
                template_id = rule.get('product_tmpl_id', [None])[0] if isinstance(rule.get('product_tmpl_id'), list) else rule.get('product_tmpl_id')
                if template_id:
                    if template_id not in existing_rules:
                        existing_rules[template_id] = []
                    existing_rules[template_id].append(rule)

            logger.info(f"✅ Pre-cargadas {len(all_rules)} reglas de reposición para {len(existing_rules)} templates")
            return existing_rules

        except Exception as e:
            logger.error(f"❌ Error al pre-cargar reglas de reposición: {e}")
            return {}

    def process_matched_products_optimized(self, scraped_products_data: Dict) -> None:
        """Procesar productos coincidentes usando datos ya scrapeados CON OPTIMIZACIONES DE CACHE"""
        logger.info(f"🚀 Procesando {len(self.matched_codes)} productos coincidentes SIN nuevo scraping...")

        # Configuración inicial
        total_items = 0
        successful_products = 0
        failed_products = 0
        start_time = datetime.now()

        # Conectar a Odoo si se va a usar
        odoo_connected = False
        if self.config.send_to_odoo:
            logger.info("🔌 Verificando conexión con Odoo...")
            odoo_connected = self.odoo_connector.connect()
            if not odoo_connected:
                logger.warning("⚠️ No se pudo conectar a Odoo. Continuando solo con análisis.")
                self.config.send_to_odoo = False

        try:
            logger.info(f"⚙️  Configuración optimizada:")
            logger.info(f"   🎯 Objetivo: {len(self.matched_codes)} productos coincidentes")
            logger.info(f"   📊 Datos scrapeados: {len(scraped_products_data.get('items', []))} productos")
            logger.info(f"   🚀 SIN nuevas peticiones a PR Autopartes")
            logger.info(f"   🌐 Integración Odoo: {'✅ Activa' if odoo_connected else '❌ Inactiva'}")

            # Convertir códigos coincidentes a lista
            matched_codes_list = list(self.matched_codes)

            # 🔥 OPTIMIZACIÓN: Pre-cargar datos estáticos una sola vez si Odoo está conectado
            cached_data = {}
            if self.config.send_to_odoo and odoo_connected:
                logger.info("🚀 Precargando datos estáticos para optimizar rendimiento...")
                cache_start = datetime.now()

                # 1. Cachear ubicación TODO/Stock/StockSCRAP
                cached_data['scraping_location_id'] = self.odoo_connector._get_depo_scraping_location()
                if not cached_data['scraping_location_id']:
                    logger.error("❌ No se encontró ubicación TODO/Stock/StockSCRAP. Abortando proceso.")
                    return

                # 2. Cachear proveedor PR Autopartes (Scraping) - COMENTADO
                # cached_data['supplier_id'] = self.odoo_connector._get_or_create_supplier()
                # if not cached_data['supplier_id']:
                #     logger.error("❌ No se encontró/creó proveedor PR Autopartes. Abortando proceso.")
                #     return

                # 3. Pre-cargar información de productos para búsquedas batch
                product_info = self._preload_product_information(matched_codes_list)
                cached_data['product_info'] = product_info

                # 4. Pre-cargar información de KITs en una sola consulta
                kits_info = self._preload_kits_information(product_info)
                cached_data['kits_info'] = kits_info

                # 5. Pre-cargar reglas de reposición existentes - COMENTADO
                # existing_rules = self._preload_replenishment_rules(product_info)
                # cached_data['existing_rules'] = existing_rules

                cache_time = datetime.now() - cache_start
                estimated_savings = len(matched_codes_list) * 2  # Ajustado sin supplier/replenishment
                logger.info(f"✅ Datos precargados en {cache_time} - Ahorrando ~{estimated_savings} consultas individuales")

            # 🔥 OPTIMIZACIÓN: Procesamiento BATCH con datos cacheados
            if self.config.send_to_odoo and odoo_connected:
                # 🚀 NUEVA IMPLEMENTACIÓN: Procesar todo en BATCH en lugar de uno por uno
                logger.info("🚀 Preparando productos para actualización BATCH...")

                # Recolectar todos los datos de productos en formato batch
                products_data_batch = []
                for code in matched_codes_list:
                    result = self._process_matched_product_from_data(code, scraped_products_data)
                    if result["success"]:
                        products_data_batch.append((code, result["data"]))
                        total_items += 1
                    else:
                        failed_products += 1
                        logger.error(f"❌ {result['error']}")

                if products_data_batch:
                    logger.info(f"📦 Enviando {len(products_data_batch)} productos a Odoo en BATCH...")
                    # 🚀 ACTUALIZACIÓN BATCH - Una sola llamada para todos los productos
                    batch_result = self.odoo_connector.update_matched_products_batch(products_data_batch, cached_data)

                    if batch_result.get("success"):
                        successful_products = len(products_data_batch) - len(batch_result.get("errors", []))

                        # Log detallado de resultados
                        stock_results = batch_result.get("stock", {})
                        # supplier_results = batch_result.get("supplierinfo", {})  # COMENTADO
                        # replenishment_results = batch_result.get("replenishment", {})  # COMENTADO

                        logger.info("📊 Resumen de actualización BATCH:")
                        logger.info(f"   📦 Stock: {len(stock_results.get('updated', []))} actualizados, {len(stock_results.get('created', []))} creados")
                        # logger.info(f"   🛒 Supplierinfo: {len(supplier_results.get('updated', []))} actualizados, {len(supplier_results.get('created', []))} creados")  # COMENTADO
                        # logger.info(f"   📋 Replenishment: {len(replenishment_results.get('updated', []))} actualizados, {len(replenishment_results.get('created', []))} creados")  # COMENTADO

                        # Mostrar errores si hubo
                        all_errors = batch_result.get("errors", [])
                        if all_errors:
                            logger.warning(f"⚠️ {len(all_errors)} productos con errores:")
                            for error in all_errors[:10]:  # Mostrar solo los primeros 10 errores
                                logger.warning(f"   - {error.get('code', 'unknown')}: {error.get('error', 'unknown')}")
                            if len(all_errors) > 10:
                                logger.warning(f"   ... y {len(all_errors) - 10} errores más")
                    else:
                        logger.error(f"❌ Error en actualización BATCH: {batch_result.get('error')}")
                        failed_products = len(products_data_batch)
                else:
                    logger.warning("⚠️ No hay productos válidos para enviar a Odoo en BATCH")
            else:
                # Procesamiento normal sin Odoo ( ThreadPoolExecutor para extracción de datos )
                with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                    # Preparar futuros para productos coincidentes
                    future_to_code = {
                        executor.submit(self._process_matched_product_from_data, code, scraped_products_data): code
                        for code in matched_codes_list
                    }

                    # Procesar resultados a medida que se completan
                    for future in as_completed(future_to_code):
                        result = future.result()

                        if result["success"]:
                            total_items += 1
                            successful_products += 1
                            logger.info(f"✅ Producto procesado: {result['code']} - {result['description']}...")
                        else:
                            failed_products += 1
                            logger.error(f"❌ {result['error']}")

                        # Pequeña pausa
                        time.sleep(0.05)  # Reducido porque no hay llamadas a Odoo

            # Estadísticas finales
            end_time = datetime.now()
            duration = end_time - start_time
            success_rate = (successful_products / len(self.matched_codes)) * 100 if self.matched_codes else 0

            logger.info("🎉 Procesamiento optimizado completado!")
            logger.info(f"   🎯 Productos coincidentes: {len(self.matched_codes)}")
            logger.info(f"   ✅ Productos procesados: {successful_products}")
            logger.info(f"   ❌ Productos fallidos: {failed_products}")
            logger.info(f"   📈 Tasa éxito: {success_rate:.1f}%")
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   🚀 Velocidad: {successful_products/duration.total_seconds():.2f} productos/segundo")
            logger.info(f"   🔥 AHORRO: {len(self.matched_codes)} peticiones HTTP evitadas")

            if self.config.send_to_odoo and odoo_connected:
                logger.info(f"   🌐 Datos enviados a Odoo con nueva lógica (stock + compra + reposición)")
            else:
                logger.info(f"   🔌 Odoo: {'No disponible' if not odoo_connected else 'Deshabilitado'}")

        except Exception as e:
            logger.error(f"❌ Error crítico durante el proceso: {e}")
            raise

    def scrape_products_and_collect_data(self, num_pages: int, bearer_token: str) -> Dict:
        """Realizar scraping completo de productos y retornar datos para procesamiento de coincidencias (SIN guardar CSV)"""
        logger.info(f"📡 Iniciando scraping completo de {num_pages} páginas...")

        # Configuración inicial
        headers = self._get_request_headers(bearer_token)
        total_items = 0
        successful_pages = 0
        failed_pages = 0
        start_time = datetime.now()

        # Recolector de datos para coincidencias
        all_scraped_items = []
        all_extracted_data = []  # Para crear DataFrame en memoria

        try:
            logger.info(f"⚙️  Configuración scraping completo:")
            logger.info(f"   📄 Páginas totales: {num_pages-1}")
            logger.info(f"   ⏱️  Retraso entre peticiones: {self.config.request_delay}s")
            logger.info(f"   ⌛ Timeout de página: {self.config.page_timeout}s")
            logger.info(f"   💾 Datos en memoria (sin guardar CSV)")

            # Procesamiento de páginas
            for page in range(1, num_pages):
                page_start_time = datetime.now()
                try:
                    logger.info(f"📄 Procesando página {page}/{num_pages-1}...")

                    # Crear payload y enviar petición
                    payload = self._create_payload(page)

                    response = self.session.post(
                        self.config.api_url,
                        headers=headers,
                        data=payload,
                        timeout=self.config.page_timeout
                    )
                    response.raise_for_status()

                    data = response.json()
                    items = data.get("items", [])

                    if not items:
                        logger.warning(f"⚠️ Página {page} no contiene items")
                        continue

                    # Procesar items de la página (recolectar en memoria)
                    page_items_processed = 0
                    for item in items:
                        try:
                            extracted_data = self._extract_item_data(item)

                            # Validar datos extraídos
                            if not extracted_data.get('codigo'):
                                logger.warning(f"⚠️ Item sin código omitido: {extracted_data.get('id', 'N/A')}")
                                continue

                            # Recolectar datos extraídos en memoria
                            all_extracted_data.append(extracted_data)
                            total_items += 1
                            page_items_processed += 1

                            # Recolectar item original para procesamiento de coincidencias
                            all_scraped_items.append(item)

                        except Exception as e:
                            logger.error(f"❌ Error procesando item en página {page}: {e}")
                            continue

                    # Estadísticas de la página
                    page_end_time = datetime.now()
                    page_duration = page_end_time - page_start_time
                    successful_pages += 1

                    logger.info(f"✅ Página {page} completada - Items: {page_items_processed}/{len(items)} - Tiempo: {page_duration.total_seconds():.1f}s")

                    # Pausa controlada entre peticiones
                    if page < num_pages - 1:  # No pausar en la última página
                        sleep_time = self.config.request_delay
                        time.sleep(sleep_time)

                except requests.exceptions.Timeout as e:
                    failed_pages += 1
                    logger.error(f"❌ Timeout en página {page}: {e}")
                    continue
                except requests.exceptions.ConnectionError as e:
                    failed_pages += 1
                    logger.error(f"❌ Error de conexión en página {page}: {e}")
                    time.sleep(5)  # Espera más larga para errores de conexión
                    continue
                except Exception as e:
                    failed_pages += 1
                    logger.error(f"❌ Error inesperado en página {page}: {e}")
                    continue

            # Estadísticas finales
            end_time = datetime.now()
            duration = end_time - start_time
            success_rate = (successful_pages / (num_pages - 1)) * 100 if num_pages > 1 else 0

            logger.info("🎉 Scraping completo finalizado!")
            logger.info(f"   📊 Items procesados: {total_items}")
            logger.info(f"   📄 Páginas exitosas: {successful_pages}/{num_pages-1} ({success_rate:.1f}%)")
            logger.info(f"   ❌ Páginas fallidas: {failed_pages}")
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   📈 Velocidad: {total_items/duration.total_seconds():.2f} items/segundo")
            logger.info(f"   💾 Datos en memoria (CSV no guardado)")
            logger.info(f"   📦 Items recolectados: {len(all_scraped_items)}")

            # Crear DataFrame en memoria para usar en _load_matched_codes
            df_scraped = pd.DataFrame(all_extracted_data)

            # Retornar datos recolectados para procesamiento de coincidencias
            return {
                "success": True,
                "items": all_scraped_items,
                "total_items": total_items,
                "df_scraped": df_scraped,  # DataFrame en memoria
                "processing_time": duration
            }

        except Exception as e:
            logger.error(f"❌ Error crítico durante el proceso: {e}")
            return {"success": False, "error": str(e)}

    def run(self, create_merged_csv: bool = True) -> None:
        """Ejecutar el proceso completo de scraping optimizado"""
        try:
            logger.info("🚀 Iniciando PrAutoParte Scraper Optimizado v2.0...")

            # 1. Obtener token de sesión (siempre se necesita para scraping)
            logger.info("🔑 Obteniendo credenciales de sesión...")
            num_pages, bearer_token = self.login_and_get_session_data()

            # 2. Generar dataset de productos Odoo (una sola vez)
            logger.info("📊 Generando dataset de productos Odoo...")
            df_productos = self._generate_odoo_products_dataset()
            if df_productos is None:
                logger.error("❌ No se pudo generar dataset de productos Odoo")
                return

            # 3. Ejecutar scraping completo Y recolectar datos para coincidencias
            logger.info("📡 Ejecutando scraping completo y recolectando datos...")
            scraping_result = self.scrape_products_and_collect_data(num_pages, bearer_token)

            # Verificar que el scraping fue exitoso
            if not scraping_result.get("success"):
                logger.error(f"❌ El scraping falló: {scraping_result.get('error')}")
                return

            # 4. Cargar coincidencias usando datos del scraping en memoria
            logger.info("🔍 Analizando coincidencias desde datos del scraping...")
            df_scraped = scraping_result.get("df_scraped")
            self.matched_codes = self._load_matched_codes(df_scraped, self.config.merged_supplier_filter)

            # 5. Verificar que hay productos coincidentes
            if not self.matched_codes:
                logger.warning("⚠️ No se encontraron productos coincidentes. No hay nada que procesar.")
                logger.info("💡 El scraping se completó en memoria, pero no hubo coincidencias con Odoo")
                return

            logger.info(f"🎯 Se procesarán {len(self.matched_codes)} productos coincidentes")

            # 6. Opcional: Crear CSV merged para análisis
            if create_merged_csv:
                logger.info("📄 Creando CSV merged con datos combinados...")
                self._create_merged_csv(df_productos, scraping_result, self.config.merged_supplier_filter, self.matched_codes)

            # 7. Procesar coincidencias usando datos YA SCRAPEADOS (SIN nuevo scraping)
            self.process_matched_products_optimized(scraping_result)

            logger.info("✅ Proceso optimizado completado exitosamente")
            logger.info("📁 Archivos generados:")
            logger.info(f"   📊 Productos Odoo: Producto (product.template).xlsx")
            logger.info(f"   💾 Datos scraping en memoria (CSV no guardado)")
            if create_merged_csv:
                logger.info(f"   🔗 Dataset merged: productos_merged.csv")

        except Exception as e:
            logger.error(f"❌ Error en el proceso principal: {e}")
            raise

    def _create_merged_csv(self, df_productos: pd.DataFrame, scraping_result: Dict, supplier_filter: Optional[str] = None, matched_codes: set = None) -> None:
        """Crear CSV merged combinando datos de Odoo y scraping

        Args:
            df_productos: DataFrame con productos de Odoo
            scraping_result: Dict con resultados del scraping
            supplier_filter: Nombre del proveedor de Odoo para filtrar productos (opcional)
                            Ejemplo: "PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ"
            matched_codes: Set de códigos coincidentes (opcional, usa self.matched_codes si no se proporciona)
        """
        try:
            logger.info("📄 Creando dataset merged para análisis...")

            # Filtrar productos por proveedor si se especifica
            if supplier_filter:
                logger.info(f"🔍 Filtrando productos por proveedor: '{supplier_filter}'")
                product_ids_by_supplier = self.odoo_connector._get_product_ids_by_supplier(supplier_filter)

                if not product_ids_by_supplier:
                    logger.warning(f"⚠️ No se encontraron productos para el proveedor '{supplier_filter}'. Se usarán todos los productos.")
                else:
                    # Filtrar df_productos para incluir solo productos con ese proveedor
                    original_count = len(df_productos)
                    df_productos = df_productos[df_productos['id'].isin(product_ids_by_supplier)]
                    filtered_count = len(df_productos)
                    logger.info(f"✅ Productos filtrados: {original_count} → {filtered_count}")

            # Convertir scraped items a DataFrame
            scraped_items = scraping_result.get("items", [])
            if not scraped_items:
                logger.warning("⚠️ No hay datos scraped para crear merged CSV")
                return

            # Extraer datos de scraped items
            scraped_data = []
            for item in scraped_items:
                scraped_data.append(self._extract_item_data(item))

            df_scraped = pd.DataFrame(scraped_data)

            # Usar los códigos coincidentes proporcionados o los de self.matched_codes
            codes_to_use = matched_codes if matched_codes is not None else self.matched_codes

            if not codes_to_use:
                logger.warning("⚠️ No hay códigos coincidentes para crear merged CSV")
                return

            # Preparar DataFrames para merge
            # Productos Odoo: usar 'default_code' o 'Referencia interna' como clave
            odoo_key_col = 'default_code' if 'default_code' in df_productos.columns else 'Referencia interna'

            # Scrapeados: usar 'codigo' como clave
            scraped_key_col = 'codigo'

            # Renombrar columnas clave para consistencia
            df_productos_merge = df_productos.copy()
            df_scraped_merge = df_scraped.copy()

            df_productos_merge = df_productos_merge.rename(columns={odoo_key_col: 'codigo_merged'})
            df_scraped_merge = df_scraped_merge.rename(columns={scraped_key_col: 'codigo_merged'})

            # 🎯 FILTRAR POR CÓDIGOS COINCIDENTES (consistencia con actualización)
            # Usar el mapeo scraping_to_odoo_code para obtener los códigos de Odoo
            odoo_codes_to_match = set()
            for scraping_code in codes_to_use:
                if scraping_code in self.scraping_to_odoo_code:
                    odoo_codes_to_match.add(self.scraping_to_odoo_code[scraping_code])
                else:
                    # Si no hay mapeo, usar el código tal cual (coincidencia exacta)
                    odoo_codes_to_match.add(scraping_code)

            # Filtrar ambos DataFrames por los códigos coincidentes
            df_productos_filtered = df_productos_merge[df_productos_merge['codigo_merged'].isin(odoo_codes_to_match)]
            df_scraped_filtered = df_scraped_merge[df_scraped_merge['codigo_merged'].isin(codes_to_use)]

            # Merge por código exacto (ya filtrados por matched_codes)
            merged_df = pd.merge(
                df_productos_filtered,
                df_scraped_filtered,
                on='codigo_merged',
                how='inner',
                suffixes=('_odoo', '_scraped')
            )

            # Reorganizar columnas para mejor visualización
            column_order = [
                'codigo_merged', 'name_odoo', 'marca_scraped', 'descripcion_scraped',
                'list_price_odoo', 'precioLista_scraped', 'precioCosto_scraped',
                'Cantidad a la mano_odoo', 'disponibilidad_scraped',
                'id_scraped', 'id_odoo'
            ]

            # Agregar columnas que existan en el orden deseado
            final_columns = []
            for col in column_order:
                if col in merged_df.columns:
                    final_columns.append(col)

            # Agregar resto de columnas
            for col in merged_df.columns:
                if col not in final_columns:
                    final_columns.append(col)

            merged_df = merged_df[final_columns]

            # Guardar merged CSV usando variable de entorno
            merged_path = self.config.get_merged_output_path()
            merged_df.to_csv(merged_path, index=False, encoding='utf-8')

            logger.info(f"✅ Dataset merged creado: {merged_path.name}")
            logger.info(f"📊 Registros combinados: {len(merged_df)} productos coincidentes")
            logger.info(f"📁 Guardado en: {merged_path.absolute()}")

            # Estadísticas del merge
            if len(df_productos) > 0 and len(df_scraped) > 0:
                match_rate = len(merged_df) / min(len(df_productos), len(df_scraped)) * 100
                logger.info(f"📈 Tasa de coincidencia real: {match_rate:.1f}%")

        except Exception as e:
            logger.error(f"❌ Error al crear CSV merged: {e}")

def main():
    """Función principal"""
    logger.info("Iniciando PrAutoParte Scraper...")
    config = ScrapingConfig()
    scraper = PrAutoParteScraper(config)
    scraper.run()

def run_scheduler():
    """Ejecutar el scraper cada 24 horas a las 9 AM"""

    logger.info("Iniciando scheduler - ejecutará todos los días a las 9:00 AM")

    # Programar ejecución diaria a las 9 AM
    schedule.every().day.at("09:00").do(main)

    # Ejecutar inmediatamente al inicio
    logger.info("Ejecutando primera vez...")
    main()

    # Loop principal del scheduler
    while True:
        schedule.run_pending()
        time.sleep(600)  # Verificar cada 10 minutos

def run_matched_only():
    """Ejecutar solo procesamiento de coincidencias desde datasets existentes"""
    try:
        logger.info("🔍 Modo solo coincidencias - SIN scraping nuevo")
        config = ScrapingConfig()
        scraper = PrAutoParteScraper(config)

        # Cargar coincidencias desde datasets existentes
        logger.info("🔍 Cargando coincidencias desde datasets existentes...")
        scraper.matched_codes = scraper._load_matched_codes(supplier_filter=scraper.config.merged_supplier_filter)

        # Cargar dataset de productos para merged CSV
        df_productos = scraper._load_odoo_products_from_backup()

        # Cargar scraped data más reciente
        df_articulos = scraper._get_latest_scraping_results()
        if df_articulos is None:
            logger.error("❌ No se encuentran datos de scraping. Ejecuta scraping completo primero.")
            return

        # Convertir a formato esperado por process_matched_products_optimized
        scraped_data = scraper._get_latest_scraping_results_as_dict()

        if not scraper.matched_codes:
            logger.warning("⚠️ No se encontraron productos coincidentes")
            return

        logger.info(f"🎯 Procesando {len(scraper.matched_codes)} coincidencias SIN nuevas descargas...")

        # Opcional: Crear merged CSV
        if df_productos is not None and scraped_data.get("success"):
            scraper._create_merged_csv(df_productos, scraped_data, scraper.config.merged_supplier_filter, scraper.matched_codes)

        # Procesar coincidencias
        scraper.process_matched_products_optimized(scraped_data)

        logger.info("✅ Procesamiento de coincidencias completado")

    except Exception as e:
        logger.error(f"❌ Error en modo solo coincidencias: {e}")

def main_cli():
    """Función para manejar argumentos de línea de comandos"""

    parser = argparse.ArgumentParser(description='PrAutoParte Scraper')
    parser.add_argument('--once', action='store_true',
                       help='Ejecutar una sola vez en lugar del scheduler')
    parser.add_argument('--schedule', action='store_true',
                       help='Ejecutar con scheduler diario a las 9 AM (por defecto)')
    parser.add_argument('--matched-only', action='store_true',
                       help='Procesar solo coincidencias desde datasets existentes (sin scraping nuevo)')

    args = parser.parse_args()

    if args.matched_only:
        logger.info("Modo solo coincidencias")
        run_matched_only()
    elif args.once:
        logger.info("Modo ejecución única")
        main()
    else:
        logger.info("Modo scheduler (diario a las 9 AM)")
        run_scheduler()

if __name__ == "__main__":
    main_cli()