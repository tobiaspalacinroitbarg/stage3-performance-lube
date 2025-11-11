import os
import json
import csv
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

@dataclass
class ScrapingConfig:
    """Configuración del scraper profesional para producción"""

    # URLs del sistema
    base_url: str = "https://www.prautopartes.com.ar/"
    catalog_url: str = "https://www.prautopartes.com.ar/catalogo"
    api_url: str = "https://www.prautopartes.com.ar/api/Articulos/Buscar"

    # Directorios
    output_dir: str = os.getenv("OUTPUT_DIR", "./output")
    logs_dir: str = os.getenv("PM2_LOG_DIR", "./logs")

    # Configuración Odoo (desde variables de entorno)
    odoo_url: str = os.getenv("ODOO_URL", "http://localhost:8069")
    odoo_db: str = os.getenv("ODOO_DB", "odoo")
    odoo_user: str = os.getenv("ODOO_USER", "admin")
    odoo_password: str = os.getenv("ODOO_PASSWORD", "admin")
    send_to_odoo: bool = os.getenv("SEND_TO_ODOO", "false").lower() == "true"

    # Configuración de rendimiento
    page_timeout: int = int(os.getenv("PAGE_TIMEOUT", "15"))  # Timeout más generoso para evitar timeouts
    request_delay: float = float(os.getenv("REQUEST_DELAY", "0.2"))  # Reducido de 0.5 a 0.2 para mayor velocidad
    window_size: str = "1920,1080"
    batch_size: int = int(os.getenv("BATCH_SIZE", "20"))  # Incrementado para procesar más productos en lote
    max_workers: int = int(os.getenv("MAX_WORKERS", "3"))  # Para procesamiento paralelo

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

class OdooConnector:
    """Clase para manejar la conexión con Odoo"""

    def __init__(self, config: ScrapingConfig):
        self.url = config.odoo_url
        self.db = config.odoo_db
        self.username = config.odoo_user
        self.password = config.odoo_password
        self.uid = None
        self.models = None

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
        """Buscar producto por código con matching mejorado (exacto y normalizado)"""
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
            scraper = PrAutoParteScraper(ScrapingConfig())
            normalized_code = scraper._normalize_code(product_code)

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
                    normalized_odoo_code = scraper._normalize_code(odoo_code)
                    if normalized_code and normalized_odoo_code and normalized_code == normalized_odoo_code:
                        logger.info(f"Producto encontrado (normalizado): {product_code} -> {odoo_code} (ID: {product['id']})")
                        return product['id']

            # 3. Búsqueda con like como fallback
            product_ids_like = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'search_read',
                [[['default_code', 'like', f'%{product_code}%']]],
                {'fields': ['id', 'default_code'], 'limit': 5}
            )

            if product_ids_like:
                # Si hay coincidencias parciales, mostrarlas pero no usarlas automáticamente
                logger.warning(f"⚠️ Coincidencias parciales encontradas para {product_code}:")
                for product in product_ids_like:
                    logger.warning(f"   - {product.get('default_code')} (ID: {product['id']})")

            logger.info(f"Producto no encontrado: {product_code}")
            return None

        except Exception as e:
            logger.error(f"Error al buscar producto {product_code}: {e}")
            return None

    def create_or_update_product(self, product_data: Dict) -> Dict:
        """Crear o actualizar producto en Odoo"""
        if not self.models:
            return {"success": False, "error": "No conectado a Odoo"}

        try:
            # Mapear campos del scraper a Odoo
            odoo_product = {
                'default_code': product_data.get('codigo', ''),
                'name': product_data.get('descripcion', ''),
                'list_price': float(product_data.get('precioLista', 0)),
                'standard_price': float(product_data.get('precioCosto', 0)),
                'type': 'product',
                'sale_ok': True,
                'purchase_ok': True,
            }

            # Buscar categoría por marca
            if product_data.get('marca'):
                category_id = self._get_or_create_category(product_data['marca'])
                if category_id:
                    odoo_product['categ_id'] = category_id

            # Buscar si el producto ya existe
            existing_product_id = self.search_product_by_code(product_data.get('codigo', ''))

            if existing_product_id:
                # Actualizar producto existente
                logger.info(f"Actualizando producto: {product_data.get('codigo')}")
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.product', 'write',
                    [[existing_product_id], odoo_product]
                )
                return {
                    "success": True,
                    "action": "updated",
                    "product_id": existing_product_id,
                    "product_code": product_data.get('codigo')
                }
            else:
                # Crear nuevo producto
                logger.info(f"Creando nuevo producto: {product_data.get('codigo')}")
                product_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.product', 'create',
                    [odoo_product]
                )
                return {
                    "success": True,
                    "action": "created",
                    "product_id": product_id,
                    "product_code": product_data.get('codigo')
                }

        except Exception as e:
            logger.error(f"Error al crear/actualizar producto: {e}")
            return {"success": False, "error": str(e)}

    def update_matched_product(self, product_data: Dict) -> Dict:
        """Actualizar producto coincidente con nueva lógica:
        1. Cargar stock en almacén 'Scraping' (siempre, incluso si es 0)
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

            # 1. Cargar stock en almacén 'Scraping' (siempre, incluso si es 0)
            scraping_stock_result = self._update_scraping_stock(existing_product_id, product_data)

            # 2. Actualizar información de compra
            purchase_info_result = self._update_purchase_info(existing_product_id, product_data)

            # 3. Establecer regla de reposición en '-35'
            replenishment_result = self._update_replenishment_rule(existing_product_id)

            return {
                "success": True,
                "action": "matched_updated",
                "product_id": existing_product_id,
                "product_code": product_code,
                "stock_updated": scraping_stock_result,
                "purchase_updated": purchase_info_result,
                "replenishment_updated": replenishment_result
            }

        except Exception as e:
            logger.error(f"Error al actualizar producto coincidente: {e}")
            return {"success": False, "error": str(e)}

    def _update_scraping_stock(self, product_id: int, product_data: Dict) -> Dict:
        """Actualizar stock del producto en almacén 'Scraping' (siempre, incluso si es 0)"""
        try:
            # Buscar almacén 'Scraping'
            scraping_location_id = self._get_scraping_location()
            if not scraping_location_id:
                return {"success": False, "error": "Almacén 'Scraping' no encontrado"}

            # Obtener disponibilidad del producto (ahora siempre procesamos el valor)
            disponibilidad = product_data.get('disponibilidad', 0)
            stock_quantity = int(disponibilidad) if disponibilidad else 0

            logger.info(f"📦 Actualizando stock en almacén Scraping: {product_data.get('codigo')} - {stock_quantity} unidades")

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

            # Buscar si ya existe un registro de inventario para este producto en este almacén
            existing_quants = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.quant', 'search_read',
                [[['product_id', '=', product_id], ['location_id', '=', scraping_location_id]]],
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
                logger.info(f"📦 Stock actualizado en almacén Scraping: {product_data.get('codigo')} - {stock_quantity} unidades")
            else:
                # Crear nuevo registro de inventario
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.quant', 'create',
                    [{
                        'product_id': product_id,
                        'location_id': scraping_location_id,
                        'quantity': stock_quantity,
                        'available_quantity': stock_quantity
                    }]
                )
                logger.info(f"📦 Stock creado en almacén Scraping: {product_data.get('codigo')} - {stock_quantity} unidades")

            return {"success": True, "quantity": stock_quantity}

        except Exception as e:
            error_msg = str(e)
            if "Debe actualizar la cantidad de componentes" in error_msg:
                logger.warning(f"⚠️ Producto {product_data.get('codigo')} es un kit - no se puede actualizar stock directamente")
                return {"success": False, "error": "Producto tipo kit - debe actualizar stock de componentes", "is_kit": True}

            logger.error(f"Error al actualizar stock en Scraping: {e}")
            return {"success": False, "error": str(e)}

    def _update_replenishment_rule(self, product_id: int) -> Dict:
        """Establecer regla de reposición en '-35' para el producto"""
        try:
            # Obtener el template_id del producto
            product_info = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'read',
                [[product_id]],
                {'fields': ['product_tmpl_id']}
            )

            if not product_info:
                return {"success": False, "error": "No se pudo obtener información del producto"}

            template_id = product_info[0]['product_tmpl_id'][0]

            # Buscar reglas de reabastecimiento existentes para este producto
            existing_rules = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.warehouse.orderpoint', 'search_read',
                [[['product_tmpl_id', '=', template_id]]],
                {'fields': ['id', 'product_min_qty', 'product_max_qty']}
            )

            if existing_rules:
                # Actualizar regla existente
                rule_id = existing_rules[0]['id']
                self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'write',
                    [[rule_id], {
                        'product_min_qty': -35,
                        'product_max_qty': -34  # Un poco más alto que el mínimo
                    }]
                )
                logger.info(f"📊 Regla de reposición actualizada: {rule_id} - Mínimo: -35")
            else:
                # Crear nueva regla de reposición
                # Buscar almacén Scraping para asociarlo a la regla
                scraping_location_id = self._get_scraping_location()
                if not scraping_location_id:
                    return {"success": False, "error": "No se puede crear regla sin almacén Scraping"}

                rule_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.warehouse.orderpoint', 'create',
                    [{
                        'product_tmpl_id': template_id,
                        'product_id': product_id,  # Agregar campo product_id obligatorio
                        'location_id': scraping_location_id,
                        'product_min_qty': -35,
                        'product_max_qty': -34,
                        'qty_multiple': 1,
                        'name': f"Rule {template_id} - Scraping"
                    }]
                )
                logger.info(f"📊 Regla de reposición creada: {rule_id} - Mínimo: -35")

            return {"success": True, "rule_value": -35}

        except Exception as e:
            logger.error(f"Error al actualizar regla de reposición: {e}")
            return {"success": False, "error": str(e)}

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
                'min_qty': 1,
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
                logger.info(f"🛒 Info de compra actualizada: {product_code} - Precio: ${precio_costo}")
            else:
                # Crear nuevo seller
                seller_id = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'product.supplierinfo', 'create',
                    [seller_info]
                )
                logger.info(f"🛒 Info de compra creada: {product_code} - Precio: ${precio_costo}")

            return {"success": True, "supplier_id": supplier_id, "price": precio_costo, "template_id": template_id}

        except Exception as e:
            logger.error(f"Error al actualizar info de compra: {e}")
            return {"success": False, "error": str(e)}

    def _get_scraping_location(self) -> Optional[int]:
        """Obtener ID del almacén 'Scraping'"""
        try:
            # Buscar ubicación 'Scraping'
            locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', '=', 'Scraping'], ['usage', '=', 'internal']]],
                {'fields': ['id', 'name']}
            )

            if locations:
                logger.info(f"✅ Almacén 'Scraping' encontrado con ID: {locations[0]['id']}")
                return locations[0]['id']

            # Si no existe, crearla
            logger.info("Creando almacén 'Scraping'...")

            # Buscar la ubicación padre (Stock General o similar)
            parent_locations = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search_read',
                [[['name', 'in', ['WH', 'Stock', 'Stock General', 'Internal Usage']], ['usage', '=', 'internal']]],
                {'fields': ['id', 'name'], 'limit': 1}
            )

            parent_id = parent_locations[0]['id'] if parent_locations else 8  # Default WH/Stock

            # Verificar que no exista conflicto de unicidad antes de crear
            duplicate_check = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'search',
                [[['name', '=', 'Scraping'], ['location_id', '=', parent_id]]]
            )

            if duplicate_check:
                logger.warning("⚠️ Ya existe un almacén 'Scraping' en esta ubicación")
                return duplicate_check[0]

            # Crear nueva ubicación
            location_id = self.models.execute_kw(
                self.db, self.uid, self.password,
                'stock.location', 'create',
                [{
                    'name': 'Scraping',
                    'location_id': parent_id,
                    'usage': 'internal',
                    'scrap_location': False,
                    'comment': 'Almacén para productos obtenidos por scraping de PR Autopartes'
                }]
            )
            logger.info(f"✅ Almacén 'Scraping' creado con ID: {location_id}")
            return location_id

        except Exception as e:
            # Si hay error al crear, intentar buscar de nuevo por si lo creó otro proceso
            try:
                logger.warning(f"Error al crear almacén, intentando buscar existente: {e}")
                locations = self.models.execute_kw(
                    self.db, self.uid, self.password,
                    'stock.location', 'search_read',
                    [[['name', '=', 'Scraping'], ['usage', '=', 'internal']]],
                    {'fields': ['id', 'name']}
                )
                if locations:
                    logger.info(f"✅ Almacén 'Scraping' encontrado en búsqueda fallback: {locations[0]['id']}")
                    return locations[0]['id']
            except Exception as fallback_e:
                logger.error(f"Error en búsqueda fallback: {fallback_e}")

            logger.error(f"Error al obtener/crear almacén Scraping: {e}")
            return None

    def _get_or_create_supplier(self) -> Optional[int]:
        """Obener o crear proveedor 'PR Autopartes (Scraping)'"""
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
                    'street': 'Obtenido por scraping web',
                    'city': 'Web',
                    'country_id': 10,  # Argentina (ajustar según configuración)
                    'email': 'scraping@prautopartes.com',
                    'phone': 'N/A',
                    'comment': 'Proveedor automático generado por sistema de scraping - PR Autopartes'
                }]
            )
            logger.info(f"✅ Proveedor 'PR Autopartes (Scraping)' creado con ID: {supplier_id}")
            return supplier_id

        except Exception as e:
            logger.error(f"Error al crear/obtener proveedor: {e}")
            return None

    def _get_or_create_category(self, marca: str) -> Optional[int]:
        """Obtener o crear categoría de producto por marca"""
        try:
            # Buscar categoría existente
            category_ids = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.category', 'search',
                [[['name', '=', marca]]]
            )

            if category_ids:
                return category_ids[0]

            # Crear nueva categoría
            logger.info(f"Creando nueva categoría: {marca}")
            category_id = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.category', 'create',
                [{
                    'name': marca,
                    'parent_id': 1,  # Categoría raíz
                }]
            )
            return category_id

        except Exception as e:
            logger.error(f"Error al crear categoría {marca}: {e}")
            return None

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

        # Cargar códigos coincidentes del dataset de productos
        self.matched_codes = self._load_matched_codes()
    
    def _setup_logging(self) -> None:
        """Configurar sistema de logging profesional"""
        log_dir = Path(self.config.logs_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Configurar nivel de log desde variable de entorno
        log_level = self.config.log_level.upper()

        # Log a archivo con rotación
        logger.add(
            self.config.get_log_path(),
            rotation="1 day",
            retention=f"{self.config.log_retention_days} days",
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{function}:{line} | {message}",
            encoding="utf-8",
            compression="zip"  # Comprimir logs antiguos
        )

        # Log a consola para PM2 (en producción)
        logger.add(
            lambda msg: print(msg, end="", flush=True),
            level=log_level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            colorize=True
        )

        logger.info(f"🔧 Logging configurado - Nivel: {log_level} - Directorio: {log_dir}")
        logger.info(f"📄 Log file: {self.config.get_log_path()}")

    def _normalize_code(self, code: str) -> str:
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

    def _load_matched_codes(self) -> set:
        """Generar datasets automáticamente y cargar códigos coincidentes con matching mejorado"""
        try:
            logger.info("🔍 Generando datasets automáticamente para análisis de coincidencias...")

            # 1. Generar dataset de productos desde Odoo
            df_productos = self._generate_odoo_products_dataset()

            # 2. Obtener dataset de artículos desde scraping más reciente
            df_articulos = self._get_latest_scraping_results()

            if df_productos is None or df_articulos is None:
                logger.error("❌ No se pudieron generar los datasets necesarios")
                return set()

            logger.info(f"📊 Dataset Productos (Odoo): {len(df_productos)} registros")
            logger.info(f"📊 Dataset Artículos (Scraping): {len(df_articulos)} registros")

            # Obtener códigos de productos (Referencia interna/default_code)
            codigos_productos = set()
            codigos_productos_norm = set()

            if 'default_code' in df_productos.columns:
                df_productos_clean = df_productos.dropna(subset=['default_code'])
                for code in df_productos_clean['default_code']:
                    normalized_code = self._normalize_code(code)
                    if normalized_code:  # Solo agregar si no está vacío después de normalizar
                        codigos_productos.add(str(code).strip())
                        codigos_productos_norm.add(normalized_code)
            elif 'Referencia interna' in df_productos.columns:
                df_productos_clean = df_productos.dropna(subset=['Referencia interna'])
                for code in df_productos_clean['Referencia interna']:
                    normalized_code = self._normalize_code(code)
                    if normalized_code:
                        codigos_productos.add(str(code).strip())
                        codigos_productos_norm.add(normalized_code)

            # Obtener códigos de artículos con normalización
            codigos_articulos = set()
            codigos_articulos_norm = {}

            if 'codigo' in df_articulos.columns:
                df_articulos_clean = df_articulos.dropna(subset=['codigo'])
                for code in df_articulos_clean['codigo']:
                    original_code = str(code).strip()
                    normalized_code = self._normalize_code(code)
                    if normalized_code and original_code:
                        codigos_articulos.add(original_code)
                        codigos_articulos_norm[normalized_code] = original_code

            # Encontrar coincidencias exactas (códigos originales)
            matched_codes_exact = codigos_productos.intersection(codigos_articulos)

            # Encontrar coincidencias normalizadas (matching robusto)
            matched_codes_normalized = set()
            for norm_codigo in codigos_productos_norm:
                if norm_codigo in codigos_articulos_norm:
                    matched_codes_normalized.add(codigos_articulos_norm[norm_codigo])

            # Combinar ambos sets de coincidencias
            matched_codes = matched_codes_exact.union(matched_codes_normalized)

            logger.info(f"✅ Códigos coincidentes exactos: {len(matched_codes_exact)}")
            logger.info(f"🔍 Códigos coincidentes normalizados: {len(matched_codes_normalized)}")
            logger.info(f"🎯 Total códigos coincidentes: {len(matched_codes)}")

            if len(codigos_articulos) > 0:
                logger.info(f"📈 Porcentaje de coincidencia: {len(matched_codes)/len(codigos_articulos)*100:.1f}%")

            # Mostrar algunos ejemplos de códigos coincidentes
            if matched_codes:
                sample_codes = list(matched_codes)[:5]
                logger.info(f"📝 Ejemplos de códigos coincidentes: {sample_codes}")

            return matched_codes

        except Exception as e:
            logger.error(f"❌ Error al generar/cargar códigos coincidentes: {e}")
            return set()

    def _generate_odoo_products_dataset(self) -> Optional[pd.DataFrame]:
        """Extraer productos desde Odoo y guardar como Excel"""
        try:
            logger.info("📥 Extrayendo productos desde Odoo...")

            # Conectar a Odoo
            if not self.odoo_connector.connect():
                logger.error("❌ No se pudo conectar a Odoo para extraer productos")
                return None

            # Extraer todos los productos
            products_data = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db,
                self.odoo_connector.uid,
                self.odoo_connector.password,
                'product.product', 'search_read',
                [[['sale_ok', '=', True]]],  # Solo productos que se pueden vender
                {
                    'fields': [
                        'id', 'default_code', 'name', 'list_price', 'standard_price',
                        'qty_available', 'virtual_available', 'type', 'sale_ok', 'purchase_ok'
                    ]
                }
            )

            if not products_data:
                logger.warning("⚠️ No se encontraron productos en Odoo")
                return pd.DataFrame()

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

            # Guardar como Excel
            productos_path = Path(self.config.output_dir) / "Producto (product.template).xlsx"

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
                logger.warning("⚠️ No se encuentran archivos de scraping CSV")
                logger.info("💡 Se generarán coincidencias solo cuando tengas resultados de scraping")
                return None

            # Usar el archivo más reciente
            articulos_file = max(articulos_files, key=lambda x: x.stat().st_mtime)
            df = pd.read_csv(articulos_file)

            logger.info(f"✅ Dataset de artículos cargado: {articulos_file.name} ({len(df)} artículos)")

            return df

        except Exception as e:
            logger.error(f"❌ Error al cargar resultados del scraping: {e}")
            return None

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
            self.driver.get(self.config.catalog_url)
            time.sleep(3)
            self._scroll_to_bottom()
            
            # Obtener número de páginas
            last_page_button = self._wait_and_find_element(
                By.XPATH, "(//button[@class='page-link cursor-hand'])[last()]"
            )
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
            
            # Obtener número total de páginas
            last_page_element = self._wait_and_find_element(
                By.XPATH, "(//button[@class='page-link cursor-hand'])[last()]"
            )
            num_pages = int(last_page_element.text) + 1
            
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
        """Enviar datos de producto directamente a Odoo"""
        try:
            product_code = product_data.get('codigo', '')

            # Verificar si es un producto coincidente
            if self._is_matched_product(product_code):
                logger.info(f"🔄 Producto coincidente detectado: {product_code}")
                result = self.odoo_connector.update_matched_product(product_data)
            else:
                # Producto normal, crear o actualizar normalmente
                result = self.odoo_connector.create_or_update_product(product_data)

            if result.get("success"):
                action = result.get("action", "processed")
                logger.info(f"✅ Producto {product_code} {action} en Odoo")
                return True
            else:
                logger.error(f"❌ Error al enviar producto {product_code} a Odoo: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"❌ Error inesperado al enviar producto {product_data.get('codigo')} a Odoo: {e}")
            return False

    def _send_batch_to_odoo(self, products: List[Dict]) -> Dict:
        """Enviar lote de productos directamente a Odoo con manejo robusto de errores"""
        if not products:
            logger.warning("⚠️ Intentando enviar lote vacío a Odoo")
            return {"success": False, "error": "Lote vacío", "processed": 0, "successful": 0, "failed": 0}

        try:
            results = []
            successful_products = 0
            failed_products = 0

            logger.info(f"📦 Procesando lote de {len(products)} productos en Odoo")

            for i, product in enumerate(products, 1):
                try:
                    # Validar producto antes de enviar
                    if not product.get('codigo'):
                        logger.warning(f"⚠️ Producto {i} sin código, omitiendo")
                        failed_products += 1
                        results.append({"success": False, "error": "Producto sin código", "product": product})
                        continue

                    result = self.odoo_connector.create_or_update_product(product)
                    results.append(result)

                    if result.get("success"):
                        successful_products += 1
                        action = result.get("action", "procesado")
                        logger.debug(f"✅ Producto {i} {action}: {product.get('codigo')}")
                    else:
                        failed_products += 1
                        logger.warning(f"❌ Producto {i} fallido: {product.get('codigo')} - {result.get('error')}")

                    # Pequeña pausa para no sobrecargar Odoo
                    time.sleep(0.1)

                except Exception as e:
                    failed_products += 1
                    error_msg = f"Error procesando producto {i}: {str(e)}"
                    logger.error(f"❌ {error_msg}")
                    results.append({"success": False, "error": error_msg, "product": product})

            # Resumen del procesamiento del lote
            success_rate = (successful_products / len(products)) * 100 if products else 0
            logger.info(f"📊 Lote procesado - Total: {len(products)} | ✅ Exitosos: {successful_products} | ❌ Fallidos: {failed_products} | 📈 Tasa éxito: {success_rate:.1f}%")

            return {
                "success": successful_products > 0,
                "processed": len(products),
                "successful": successful_products,
                "failed": failed_products,
                "success_rate": success_rate,
                "details": results
            }

        except Exception as e:
            logger.error(f"❌ Error crítico al enviar lote a Odoo: {e}")
            return {"success": False, "error": str(e), "processed": len(products), "successful": 0, "failed": len(products)}

    def _connect_to_odoo(self) -> bool:
        """Conectar a Odoo si aún no está conectado"""
        if not self.odoo_connector.models:
            logger.info("Conectando a Odoo...")
            return self.odoo_connector.connect()
        return True
    
    def _process_single_product_parallel(self, product_code: str, headers: Dict) -> Dict:
        """Procesar un solo producto en paralelo"""
        try:
            # Crear payload específico para buscar por código
            payload = json.dumps({
                "idMarcas": 0,
                "idRubros": 0,
                "busqueda": product_code,
                "pagina": 1,
                "isNovedades": False,
                "isOfertas": False,
                "equivalencia": ""
            })

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
                return {"success": False, "error": f"Producto {product_code} no encontrado en API", "code": product_code}

            # Buscar el producto exacto por código
            found_product = None
            for item in items:
                if item.get("codigo") == product_code:
                    found_product = item
                    break

            if not found_product:
                return {"success": False, "error": f"Producto {product_code} no coincide exactamente", "code": product_code}

            # Extraer datos del producto encontrado
            extracted_data = self._extract_item_data(found_product)

            return {
                "success": True,
                "data": extracted_data,
                "code": product_code,
                "description": extracted_data.get('descripcion', '')[:50]
            }

        except requests.exceptions.Timeout as e:
            return {"success": False, "error": f"Timeout buscando producto {product_code}: {e}", "code": product_code}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Error de conexión buscando producto {product_code}: {e}", "code": product_code}
        except Exception as e:
            return {"success": False, "error": f"Error inesperado procesando producto {product_code}: {e}", "code": product_code}

    def scrape_matched_products(self, bearer_token: str) -> None:
        """Realizar scraping optimizado y paralelo solo de productos coincidentes"""
        logger.info(f"🚀 Iniciando scraping optimizado y paralelo de {len(self.matched_codes)} productos coincidentes...")

        # Configuración inicial
        headers = self._get_request_headers(bearer_token)
        total_items = 0
        successful_products = 0
        failed_products = 0
        start_time = datetime.now()

        # Conectar a Odoo si se va a usar
        odoo_connected = False
        if self.config.send_to_odoo:
            logger.info("🔌 Verificando conexión con Odoo...")
            odoo_connected = self._connect_to_odoo()
            if not odoo_connected:
                logger.warning("⚠️ No se pudo conectar a Odoo. Continuando solo con CSV.")
                self.config.send_to_odoo = False

        # Preparar CSV (siempre se crea)
        fields = [
            "id", "codigo", "marca", "descripcion", "precioLista", "precioCosto",
            "precioVenta", "descuentos", "disponibilidad", "origen", "fotos"
        ]
        output_path = self.config.get_output_path()

        # Thread lock para escritura CSV segura
        csv_lock = threading.Lock()

        try:
            # Verificar y manejar archivo existente
            if output_path.exists():
                backup_path = output_path.with_suffix('.backup.csv')
                import shutil
                shutil.copy2(output_path, backup_path)
                logger.info(f"📄 Archivo existente respaldado como: {backup_path.name}")

            # Abrir archivo CSV
            f = open(output_path, "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()

            logger.info(f"📄 Datos guardando en: {output_path.absolute()}")

            logger.info(f"⚙️  Configuración optimizada:")
            logger.info(f"   🎯 Objetivo: {len(self.matched_codes)} productos coincidentes")
            logger.info(f"   ⏱️  Retraso entre peticiones: {self.config.request_delay}s")
            logger.info(f"   ⌛ Timeout de página: {self.config.page_timeout}s")
            logger.info(f"   🔢 Workers paralelos: {self.config.max_workers}")
            logger.info(f"   🌐 Integración Odoo: {'✅ Activa' if odoo_connected else '❌ Inactiva'}")

            # Convertir códigos a lista para procesamiento
            matched_codes_list = list(self.matched_codes)

            # Procesamiento por lotes con ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                # Enviar todas las búsquedas en paralelo
                future_to_code = {
                    executor.submit(self._process_single_product_parallel, code, headers): code
                    for code in matched_codes_list
                }

                # Procesar resultados a medida que se completan
                for future in as_completed(future_to_code):
                    result = future.result()

                    if result["success"]:
                        # Escribir en CSV de forma thread-safe
                        with csv_lock:
                            writer.writerow(result["data"])
                            f.flush()  # Forzar escritura inmediata
                            total_items += 1
                            successful_products += 1

                        logger.info(f"✅ Producto encontrado: {result['code']} - {result['description']}...")

                        # Enviar a Odoo inmediatamente si está conectado
                        if self.config.send_to_odoo and odoo_connected:
                            odoo_result = self._send_to_odoo(result["data"])
                            if odoo_result:
                                logger.info(f"🌐 Producto {result['code']} actualizado en Odoo")
                            else:
                                logger.error(f"❌ Error al enviar {result['code']} a Odoo")
                    else:
                        failed_products += 1
                        logger.error(f"❌ {result['error']}")

                    # Pequeña pausa para no sobrecargar el servidor
                    time.sleep(self.config.request_delay / self.config.max_workers)

            # Estadísticas finales
            end_time = datetime.now()
            duration = end_time - start_time
            success_rate = (successful_products / len(self.matched_codes)) * 100 if self.matched_codes else 0

            logger.info("🎉 Scraping optimizado y paralelo completado!")
            logger.info(f"   🎯 Productos coincidentes: {len(self.matched_codes)}")
            logger.info(f"   ✅ Productos exitosos: {successful_products}")
            logger.info(f"   ❌ Productos fallidos: {failed_products}")
            logger.info(f"   📈 Tasa éxito: {success_rate:.1f}%")
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   🚀 Velocidad: {successful_products/duration.total_seconds():.2f} productos/segundo")
            logger.info(f"   📄 Archivo CSV: {output_path.name}")
            logger.info(f"   📁 Ubicación: {output_path.absolute()}")

            if self.config.send_to_odoo and odoo_connected:
                logger.info(f"   🌐 Datos enviados a Odoo con nueva lógica (stock + compra + reposición)")
            else:
                logger.info(f"   🔌 Odoo: {'No disponible' if not odoo_connected else 'Deshabilitado'}")

        except Exception as e:
            logger.error(f"❌ Error crítico durante el proceso: {e}")
            raise
        finally:
            # Asegurar cierre del archivo CSV
            try:
                f.close()
                logger.info(f"📄 Archivo CSV cerrado: {output_path.absolute()}")
            except:
                logger.error("❌ Error al cerrar archivo CSV")

    def scrape_products(self, num_pages: int, bearer_token: str) -> None:
        """Realizar scraping completo de productos solo para generar dataset (sin procesar Odoo)"""
        logger.info(f"📡 Iniciando scraping completo de {num_pages} páginas para generar dataset...")

        # Configuración inicial
        headers = self._get_request_headers(bearer_token)
        total_items = 0
        successful_pages = 0
        failed_pages = 0
        start_time = datetime.now()

        # Preparar CSV (siempre se crea)
        fields = [
            "id", "codigo", "marca", "descripcion", "precioLista", "precioCosto",
            "precioVenta", "descuentos", "disponibilidad", "origen", "fotos"
        ]
        output_path = self.config.get_output_path()

        try:
            # Verificar y manejar archivo existente
            if output_path.exists():
                backup_path = output_path.with_suffix('.backup.csv')
                import shutil
                shutil.copy2(output_path, backup_path)
                logger.info(f"📄 Archivo existente respaldado como: {backup_path.name}")

            # Abrir archivo CSV
            f = open(output_path, "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()

            logger.info(f"📄 Dataset guardando en: {output_path.absolute()}")

            logger.info(f"⚙️  Configuración scraping completo:")
            logger.info(f"   📄 Páginas totales: {num_pages-1}")
            logger.info(f"   ⏱️  Retraso entre peticiones: {self.config.request_delay}s")
            logger.info(f"   ⌛ Timeout de página: {self.config.page_timeout}s")
            logger.info(f"   🎯 Objetivo: Generar dataset para análisis de coincidencias")

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

                    # Procesar items de la página (solo guardar en CSV)
                    page_items_processed = 0
                    for item in items:
                        try:
                            extracted_data = self._extract_item_data(item)

                            # Validar datos extraídos
                            if not extracted_data.get('codigo'):
                                logger.warning(f"⚠️ Item sin código omitido: {extracted_data.get('id', 'N/A')}")
                                continue

                            # Escribir siempre en CSV
                            writer.writerow(extracted_data)
                            total_items += 1
                            page_items_processed += 1

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

            logger.info("🎉 Scraping completo para dataset finalizado!")
            logger.info(f"   📊 Items procesados: {total_items}")
            logger.info(f"   📄 Páginas exitosas: {successful_pages}/{num_pages-1} ({success_rate:.1f}%)")
            logger.info(f"   ❌ Páginas fallidas: {failed_pages}")
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   📈 Velocidad: {total_items/duration.total_seconds():.2f} items/segundo")
            logger.info(f"   📄 Dataset CSV: {output_path.name}")
            logger.info(f"   📁 Ubicación: {output_path.absolute()}")
            logger.info(f"   🔍 Listo para análisis de coincidencias")

        except Exception as e:
            logger.error(f"❌ Error crítico durante el proceso: {e}")
            raise
        finally:
            # Asegurar cierre del archivo CSV
            try:
                f.close()
                logger.info(f"📄 Dataset CSV cerrado: {output_path.absolute()}")
            except:
                logger.error("❌ Error al cerrar archivo CSV")

    def run(self) -> None:
        """Ejecutar el proceso completo de scraping optimizado"""
        try:
            logger.info("Iniciando PrAutoParte Scraper Optimizado...")

            # 1. Obtener token de sesión
            logger.info("🔑 Obteniendo credenciales de sesión...")
            num_pages, bearer_token = self.login_and_get_session_data()

            # 2. Ejecutar scraping completo para generar dataset actualizado
            logger.info("📡 Ejecutando scraping completo para generar dataset actualizado...")
            self.scrape_products(num_pages, bearer_token)

            # 3. Ahora que tenemos el scraping actualizado, cargar coincidencias
            logger.info("🔍 Analizando coincidencias con datos actualizados...")
            self.matched_codes = self._load_matched_codes()

            # 4. Verificar que hay productos coincidentes
            if not self.matched_codes:
                logger.warning("⚠️ No se encontraron productos coincidentes. No hay nada que procesar.")
                logger.info("💡 El scraping se completó y se guardó en CSV, pero no hubo coincidencias con Odoo")
                return

            logger.info(f"🎯 Modo optimizado: Se procesarán {len(self.matched_codes)} productos coincidentes")

            # 5. Procesar solo los productos coincidentes con nueva lógica
            self.scrape_matched_products(bearer_token)

            logger.info("Proceso optimizado completado exitosamente")

        except Exception as e:
            logger.error(f"Error en el proceso principal: {e}")
            raise

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

def main_cli():
    """Función para manejar argumentos de línea de comandos"""

    parser = argparse.ArgumentParser(description='PrAutoParte Scraper')
    parser.add_argument('--once', action='store_true',
                       help='Ejecutar una sola vez en lugar del scheduler')
    parser.add_argument('--schedule', action='store_true',
                       help='Ejecutar con scheduler diario a las 9 AM (por defecto)')

    args = parser.parse_args()

    if args.once:
        logger.info("Modo ejecución única")
        main()
    else:
        logger.info("Modo scheduler (diario a las 9 AM)")
        run_scheduler()

if __name__ == "__main__":
    main_cli()