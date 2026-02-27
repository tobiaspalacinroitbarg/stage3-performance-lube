"""
SV Scraper v2 - Sin Selenium
============================
Scraper para portal.sv.com.ar usando solo requests (sin Selenium).
Guarda el stock REAL (suma de sucursales) en Odoo.

Uso:
    python sv_scraper_v2.py              # Ejecutar normalmente
    python sv_scraper_v2.py --dry-run    # Solo mostrar, no modificar Odoo
    python sv_scraper_v2.py --limit 50   # Limitar a 50 productos
"""

import os
import time
import argparse
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Importar OdooConnector del main
from main import OdooConnector, ScrapingConfig


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SVConfigV2:
    """Configuración para el scraper de SV v2"""

    # Portal SV
    base_url: str = "https://portal.sv.com.ar"
    search_api_url: str = "https://portal.sv.com.ar/api/searchresults"

    # Credenciales SV
    sv_username: str = os.getenv("SV_USERNAME", "")
    sv_password: str = os.getenv("SV_PASSWORD", "")

    # Odoo
    odoo_url: str = os.getenv("ODOO_URL", "http://localhost:8069")
    odoo_db: str = os.getenv("ODOO_DB", "odoo")
    odoo_user: str = os.getenv("ODOO_USER", "admin")
    odoo_password: str = os.getenv("ODOO_PASSWORD", "admin")

    # Proveedor y ubicación en Odoo (hardcodeados)
    supplier_name: str = "SERVICIOS VIALES DE SANTA FE S A"
    scraping_location_name: str = "SV - Scraping"

    # Rendimiento
    request_delay: float = float(os.getenv("SV_REQUEST_DELAY", "0.05"))
    max_workers: int = int(os.getenv("SV_MAX_WORKERS", "5"))  # Reducido para estabilidad

    def __post_init__(self):
        if not self.sv_username or not self.sv_password:
            raise ValueError("SV_USERNAME y SV_PASSWORD son obligatorias en .env")


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class SVScraperV2:
    """Scraper para portal SV - versión sin Selenium"""

    def __init__(self, config: SVConfigV2):
        self.config = config
        self.session: Optional[requests.Session] = None
        self.session_cookies: List[dict] = []  # Cookies para crear sesiones thread-local
        self.session_headers: dict = {}  # Headers para crear sesiones thread-local
        self._thread_local = threading.local()  # Storage thread-local
        self.odoo_connector: Optional[OdooConnector] = None
        self.location_id: Optional[int] = None

    def _init_odoo(self) -> bool:
        """Inicializar conexión a Odoo"""
        try:
            # Crear config compatible con OdooConnector
            os.environ.setdefault("PRAUTO_USERNAME", "dummy")
            os.environ.setdefault("PRAUTO_PASSWORD", "dummy")
            
            scraping_config = ScrapingConfig()
            self.odoo_connector = OdooConnector(scraping_config)
            
            if not self.odoo_connector.connect():
                logger.error("No se pudo conectar a Odoo")
                return False
            
            # Obtener ubicación
            self.location_id = self.odoo_connector._get_scraping_location_by_name(
                self.config.scraping_location_name
            )
            if not self.location_id:
                logger.error(f"No se encontró ubicación: {self.config.scraping_location_name}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error inicializando Odoo: {e}")
            return False

    def login(self) -> bool:
        """Login via API (sin Selenium)"""
        logger.info("🔐 Iniciando login en portal.sv.com.ar (API)...")
        
        try:
            self.session = requests.Session()
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": self.config.base_url,
                "Referer": f"{self.config.base_url}/login",
            }
            self.session.headers.update(headers)
            
            # Obtener CSRF token
            csrf_response = self.session.get(f"{self.config.base_url}/api/auth/csrf")
            if csrf_response.status_code != 200:
                logger.error(f"No se pudo obtener CSRF token: {csrf_response.status_code}")
                return False
            
            csrf_token = csrf_response.json().get("csrfToken")
            if not csrf_token:
                logger.error("CSRF token vacío")
                return False
            
            # Login
            login_data = {
                "email": self.config.sv_username,
                "password": self.config.sv_password,
                "csrfToken": csrf_token,
                "callbackUrl": f"{self.config.base_url}/",
                "json": "true"
            }
            
            login_response = self.session.post(
                f"{self.config.base_url}/api/auth/callback/credentials",
                data=login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            
            # Verificar que tenemos session token
            session_token = None
            for cookie in self.session.cookies:
                if "session-token" in cookie.name:
                    session_token = cookie.value
                    break
            
            if not session_token:
                logger.error("No se obtuvo session token")
                return False
            
            # Guardar cookies y headers para crear sesiones thread-local
            # Copiar cookies correctamente (con todos sus atributos)
            self.session_cookies = []
            for cookie in self.session.cookies:
                self.session_cookies.append({
                    'name': cookie.name,
                    'value': cookie.value,
                    'domain': cookie.domain,
                    'path': cookie.path,
                })
            self.session_headers = dict(self.session.headers)
            
            logger.info(f"✅ Login exitoso (session token: {session_token[:30]}...)")
            return True
            
        except Exception as e:
            logger.error(f"Error en login: {e}")
            return False

    def _get_thread_session(self) -> requests.Session:
        """Obtener o crear una sesión para el thread actual"""
        if not hasattr(self._thread_local, 'session'):
            # Crear nueva sesión para este thread
            session = requests.Session()
            session.headers.update(self.session_headers)
            for cookie in self.session_cookies:
                session.cookies.set(
                    cookie['name'], 
                    cookie['value'],
                    domain=cookie.get('domain', ''),
                    path=cookie.get('path', '/')
                )
            self._thread_local.session = session
        return self._thread_local.session

    def search_product(self, code: str, retries: int = 3, retry_on_empty: int = 2) -> Optional[Dict]:
        """Buscar un producto por código y retornar match exacto.
        
        Args:
            code: Código del producto a buscar
            retries: Reintentos para errores de red/HTTP
            retry_on_empty: Reintentos adicionales cuando API devuelve [] (posible falso negativo)
        """
        if len(self.session_cookies) == 0:
            return None
        
        # Usar sesión thread-local
        session = self._get_thread_session()
        
        empty_retries_left = retry_on_empty
        
        for attempt in range(retries + 1):
            try:
                url = f"{self.config.search_api_url}?query={code}"
                response = session.get(url, timeout=30)
                
                if response.status_code != 200:
                    logger.warning(f"API status {response.status_code} para {code}")
                    if attempt < retries:
                        time.sleep(0.5)
                        continue
                    return None
                
                results = response.json()
                if not isinstance(results, list):
                    logger.warning(f"Respuesta no es lista para {code}: {type(results)}")
                    return None
                
                # Si la API devuelve [] pero aún tenemos retries para empty, reintentar
                if len(results) == 0 and empty_retries_left > 0:
                    empty_retries_left -= 1
                    time.sleep(0.3)  # Pequeña pausa antes de reintentar
                    continue
                
                # Buscar match exacto
                code_upper = code.strip().upper()
                code_no_spaces = code_upper.replace(" ", "")
                
                for product in results:
                    api_code = product.get("codigo", "").strip().upper()
                    
                    # Match exacto
                    if api_code == code_upper:
                        return product
                    
                    # 2nd try: sin espacios (SA17483 == SA 17483)
                    api_code_no_spaces = api_code.replace(" ", "")
                    if api_code_no_spaces == code_no_spaces:
                        logger.debug(f"Match sin espacios: {code} -> {api_code}")
                        return product
                
                # No hubo match exacto
                if len(results) > 0:
                    logger.debug(f"No match para {code}. Resultados: {[r.get('codigo') for r in results[:3]]}")
                return None
                
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout buscando {code} (intento {attempt + 1}/{retries + 1})")
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None
            except requests.exceptions.RequestException as e:
                logger.warning(f"Error de red buscando {code}: {e} (intento {attempt + 1}/{retries + 1})")
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None
            except Exception as e:
                logger.debug(f"Error buscando {code}: {e}")
                return None
        
        return None

    def get_total_stock(self, product: Dict) -> int:
        """Calcular stock total (SF + BA + MDZ + SA, sin OTROS)"""
        return sum([
            max(0, product.get("disponibleSF", 0) or 0),
            max(0, product.get("disponibleBA", 0) or 0),
            max(0, product.get("disponibleMDZ", 0) or 0),
            max(0, product.get("disponibleSA", 0) or 0),
        ])

    def get_odoo_products(self) -> Dict[str, Dict]:
        """Obtener productos de Odoo que deben buscarse en SV.
        
        Criterios:
        - SV como proveedor principal -> Incluir
        - Turbodisel como principal + SV como secundario -> Incluir
        - Otro caso -> Excluir
        """
        SUPPLIER_SV = "SERVICIOS VIALES DE SANTA FE S A"
        SUPPLIER_TURBO = "TURBODISEL SOCIEDAD ANONIMA"
        
        logger.info(f"🔍 Obteniendo productos para buscar en SV...")
        logger.info(f"   - Con SV como proveedor principal")
        logger.info(f"   - Con Turbodisel principal + SV secundario")
        
        # Obtener IDs de proveedores
        sv_id = self.odoo_connector._get_supplier_id_by_name(SUPPLIER_SV)
        turbo_id = self.odoo_connector._get_supplier_id_by_name(SUPPLIER_TURBO)
        
        if not sv_id:
            logger.error(f"No se encontró proveedor: {SUPPLIER_SV}")
            return {}
        
        # 1. Productos con SV como principal
        sv_principal_ids = self.odoo_connector._get_product_ids_by_supplier(SUPPLIER_SV)
        logger.info(f"📊 Productos con SV como principal: {len(sv_principal_ids)}")
        
        # 2. Si existe Turbodisel, buscar productos con Turbo principal + SV secundario
        turbo_with_sv_ids = set()
        if turbo_id:
            # Obtener todos los supplierinfo de ambos proveedores
            all_supplierinfo = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'product.supplierinfo', 'search_read',
                [[['partner_id', 'in', [sv_id, turbo_id]]]],
                {'fields': ['product_tmpl_id', 'partner_id', 'sequence']}
            )
            
            # Agrupar por template
            from collections import defaultdict
            template_suppliers = defaultdict(list)
            for si in all_supplierinfo:
                if si.get('product_tmpl_id'):
                    tmpl_id = si['product_tmpl_id'][0]
                    template_suppliers[tmpl_id].append({
                        'partner_id': si['partner_id'][0],
                        'sequence': si.get('sequence', 10)
                    })
            
            # Encontrar templates con Turbo principal + SV secundario
            turbo_primary_sv_secondary = []
            for tmpl_id, suppliers in template_suppliers.items():
                suppliers_sorted = sorted(suppliers, key=lambda x: x['sequence'])
                if len(suppliers_sorted) >= 2:
                    # El primero es el principal
                    if suppliers_sorted[0]['partner_id'] == turbo_id:
                        # Verificar que SV está como secundario
                        if any(s['partner_id'] == sv_id for s in suppliers_sorted[1:]):
                            turbo_primary_sv_secondary.append(tmpl_id)
            
            # Obtener product_ids de estos templates
            if turbo_primary_sv_secondary:
                products = self.odoo_connector.models.execute_kw(
                    self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                    'product.product', 'search',
                    [[['product_tmpl_id', 'in', turbo_primary_sv_secondary]]]
                )
                turbo_with_sv_ids = set(products)
            
            logger.info(f"📊 Productos con Turbo principal + SV secundario: {len(turbo_with_sv_ids)}")
        
        # Combinar ambos sets
        all_product_ids = sv_principal_ids.union(turbo_with_sv_ids)
        logger.info(f"📊 Total productos a buscar: {len(all_product_ids)}")
        
        # Obtener detalles de productos
        product_codes = {}
        product_ids_list = list(all_product_ids)
        batch_size = 200
        
        for i in range(0, len(product_ids_list), batch_size):
            batch = product_ids_list[i:i + batch_size]
            try:
                products = self.odoo_connector.models.execute_kw(
                    self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                    'product.product', 'read',
                    [batch],
                    {'fields': ['id', 'default_code', 'product_tmpl_id', 'type']}
                )
                
                for p in products:
                    code = p.get('default_code')
                    if code and str(code).strip():
                        code = str(code).strip()
                        product_type = p.get('type', '')
                        # Solo 'product' (storable) puede tener quants, no 'consu' ni 'service'
                        product_codes[code] = {
                            'product_id': p['id'],
                            'template_id': p['product_tmpl_id'][0] if p.get('product_tmpl_id') else None,
                            'is_storable': product_type == 'product',
                        }
            except Exception as e:
                logger.error(f"Error leyendo batch: {e}")
        
        logger.info(f"✅ {len(product_codes)} productos con código válido")
        return product_codes

    def scrape_all_products(self, codes_list: List[str]) -> Tuple[Dict[str, int], List[str], List[str]]:
        """Buscar todos los productos en paralelo.
        
        Returns:
            (stock_results, not_found_codes, found_without_stock_codes)
        """
        results = {}
        not_found = []
        found_without_stock = []
        lock = threading.Lock()
        total = len(codes_list)
        processed = 0
        found = 0
        
        start_time = datetime.now()
        
        def process_code(code: str) -> Tuple[str, int, bool, bool]:
            """Retorna (code, stock, was_found, had_api_error)"""
            try:
                product = self.search_product(code)
                if product:
                    return (code, self.get_total_stock(product), True, False)
                # No encontrado en resultados (pero API respondió OK)
                return (code, 0, False, False)
            except Exception as e:
                # Error de API/red
                logger.debug(f"Error procesando {code}: {e}")
                return (code, 0, False, True)
        
        api_errors = []
        
        logger.info(f"🚀 Buscando {total} productos con {self.config.max_workers} workers...")
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {executor.submit(process_code, code): code for code in codes_list}
            
            for future in as_completed(futures):
                code, stock, was_found, had_api_error = future.result()
                with lock:
                    results[code] = stock
                    processed += 1
                    
                    if had_api_error:
                        api_errors.append(code)
                        not_found.append(code)  # Tratamos errores de API como no encontrados
                    elif was_found:
                        if stock > 0:
                            found += 1
                        else:
                            found_without_stock.append(code)
                    else:
                        not_found.append(code)
                    
                    if processed % 100 == 0 or processed == total:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        rate = processed / elapsed if elapsed > 0 else 0
                        logger.info(f"📊 Progreso: {processed}/{total} ({processed/total*100:.1f}%) - "
                                   f"Con stock: {found} - Errores API: {len(api_errors)} - {rate:.1f} prod/s")
                
                time.sleep(self.config.request_delay)
        
        if api_errors:
            logger.warning(f"⚠️ {len(api_errors)} productos tuvieron errores de API (timeout/red)")
        
        return results, not_found, found_without_stock

    def retry_not_found_sequential(
        self, 
        not_found_codes: List[str], 
        stock_results: Dict[str, int],
        found_without_stock_codes: List[str]
    ) -> Tuple[Dict[str, int], List[str], List[str], int]:
        """Segunda pasada secuencial para productos no encontrados.
        
        Args:
            not_found_codes: Códigos que no se encontraron en primera pasada
            stock_results: Resultados actuales (se actualizan in-place)
            found_without_stock_codes: Lista de encontrados sin stock (se actualiza)
        
        Returns:
            (stock_results, updated_not_found, updated_found_without_stock, recovered_count)
        """
        if not not_found_codes:
            return stock_results, not_found_codes, found_without_stock_codes, 0
        
        logger.info(f"\n🔄 SEGUNDA PASADA: Re-intentando {len(not_found_codes)} productos (secuencial)...")
        
        still_not_found = []
        recovered = 0
        
        for i, code in enumerate(not_found_codes):
            try:
                product = self.search_product(code)
                
                if product:
                    stock = self.get_total_stock(product)
                    stock_results[code] = stock
                    recovered += 1
                    
                    if stock > 0:
                        logger.info(f"   ✅ Recuperado: {code} (stock: {stock})")
                    else:
                        found_without_stock_codes.append(code)
                        logger.debug(f"   ✅ Recuperado sin stock: {code}")
                else:
                    still_not_found.append(code)
                
                # Progreso cada 50
                if (i + 1) % 50 == 0:
                    logger.info(f"   📊 Progreso retry: {i + 1}/{len(not_found_codes)} - Recuperados: {recovered}")
                
                time.sleep(0.1)  # Pequeña pausa entre requests
                
            except Exception as e:
                logger.debug(f"   Error en retry {code}: {e}")
                still_not_found.append(code)
        
        logger.info(f"   ✅ Segunda pasada completada: {recovered} recuperados, {len(still_not_found)} no encontrados")
        
        return stock_results, still_not_found, found_without_stock_codes, recovered

    def _save_report_files(self, not_found_codes: List[str], found_without_stock_codes: List[str]) -> None:
        """Guardar archivos TXT con reportes (se sobrescriben en cada ejecución)"""
        output_dir = Path("./output")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Productos no encontrados en SV API
        not_found_file = output_dir / "sv_not_found.txt"
        with open(not_found_file, "w", encoding="utf-8") as f:
            f.write(f"# Productos no encontrados en API de SV\n")
            f.write(f"# Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total: {len(not_found_codes)}\n")
            f.write("#" + "=" * 50 + "\n\n")
            for code in sorted(not_found_codes):
                f.write(f"{code}\n")
        logger.info(f"📄 Guardado: {not_found_file} ({len(not_found_codes)} códigos)")
        
        # 2. Productos encontrados pero sin stock
        no_stock_file = output_dir / "sv_found_without_stock.txt"
        with open(no_stock_file, "w", encoding="utf-8") as f:
            f.write(f"# Productos encontrados en SV pero SIN stock\n")
            f.write(f"# Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total: {len(found_without_stock_codes)}\n")
            f.write("#" + "=" * 50 + "\n\n")
            for code in sorted(found_without_stock_codes):
                f.write(f"{code}\n")
        logger.info(f"📄 Guardado: {no_stock_file} ({len(found_without_stock_codes)} códigos)")

    def update_odoo_stock(self, product_codes: Dict[str, Dict], stock_results: Dict[str, int], dry_run: bool = False) -> Dict:
        """Actualizar stock en Odoo"""
        logger.info(f"📦 Actualizando stock en Odoo (ubicación ID: {self.location_id})...")
        
        if dry_run:
            logger.info("🔸 MODO DRY-RUN - No se modificará Odoo")
        
        results = {
            "updated": 0,
            "created": 0,
            "skipped_non_storable": 0,
            "errors": []
        }
        
        # Obtener quants existentes en esta ubicación
        product_ids = [info['product_id'] for info in product_codes.values() if info.get('is_storable')]
        
        existing_quants = {}
        if product_ids:
            quants = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'stock.quant', 'search_read',
                [[['product_id', 'in', product_ids], ['location_id', '=', self.location_id]]],
                {'fields': ['id', 'product_id', 'quantity']}
            )
            for q in quants:
                existing_quants[q['product_id'][0]] = q
        
        logger.info(f"📊 Quants existentes en ubicación: {len(existing_quants)}")
        
        # Preparar actualizaciones
        to_update = []  # (quant_id, quantity, code)
        to_create = []  # (product_id, quantity, code)
        
        for code, stock in stock_results.items():
            info = product_codes.get(code)
            if not info:
                continue
            
            if not info.get('is_storable'):
                results["skipped_non_storable"] += 1
                continue
            
            product_id = info['product_id']
            
            if product_id in existing_quants:
                quant = existing_quants[product_id]
                # Solo actualizar si cambió
                if quant['quantity'] != stock:
                    to_update.append((quant['id'], stock, code))
            else:
                to_create.append((product_id, stock, code))
        
        logger.info(f"📊 A actualizar: {len(to_update)}, A crear: {len(to_create)}")
        
        if dry_run:
            # Mostrar algunos ejemplos
            logger.info("\n📋 Ejemplos de actualizaciones:")
            for quant_id, qty, code in to_update[:10]:
                logger.info(f"   UPDATE: {code} -> quantity={qty}")
            for product_id, qty, code in to_create[:10]:
                logger.info(f"   CREATE: {code} -> quantity={qty}")
            
            results["updated"] = len(to_update)
            results["created"] = len(to_create)
            return results
        
        # ========================================
        # BATCH UPDATE - Agrupar por cantidad
        # ========================================
        logger.info("🔄 Ejecutando actualizaciones en batch...")
        
        # Agrupar updates por quantity (Odoo permite write a múltiples IDs con mismo valor)
        updates_by_qty = {}
        for quant_id, quantity, code in to_update:
            if quantity not in updates_by_qty:
                updates_by_qty[quantity] = []
            updates_by_qty[quantity].append((quant_id, code))
        
        # Ejecutar updates en batch por cantidad
        for quantity, items in updates_by_qty.items():
            quant_ids = [item[0] for item in items]
            codes = [item[1] for item in items]
            try:
                self.odoo_connector.models.execute_kw(
                    self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                    'stock.quant', 'write',
                    [quant_ids, {'quantity': quantity}]
                )
                results["updated"] += len(quant_ids)
                logger.debug(f"   ✅ Batch update: {len(quant_ids)} quants -> qty={quantity}")
            except Exception as e:
                logger.error(f"   ❌ Error batch update qty={quantity}: {e}")
                for code in codes:
                    results["errors"].append(f"{code}: {e}")
        
        # ========================================
        # BATCH CREATE - Crear todos de una vez
        # ========================================
        if to_create:
            logger.info(f"➕ Creando {len(to_create)} quants en batch...")
            
            # Crear registros en batches de 100
            batch_size = 100
            for i in range(0, len(to_create), batch_size):
                batch = to_create[i:i + batch_size]
                records = [
                    {
                        'product_id': product_id,
                        'location_id': self.location_id,
                        'quantity': quantity,
                    }
                    for product_id, quantity, code in batch
                ]
                
                try:
                    created_ids = self.odoo_connector.models.execute_kw(
                        self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                        'stock.quant', 'create',
                        [records]
                    )
                    results["created"] += len(batch)
                    logger.debug(f"   ✅ Batch create: {len(batch)} quants creados")
                except Exception as e:
                    logger.error(f"   ❌ Error batch create: {e}")
                    # Fallback: crear uno por uno
                    for product_id, quantity, code in batch:
                        try:
                            self.odoo_connector.models.execute_kw(
                                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                                'stock.quant', 'create',
                                [{
                                    'product_id': product_id,
                                    'location_id': self.location_id,
                                    'quantity': quantity,
                                }]
                            )
                            results["created"] += 1
                        except Exception as e2:
                            results["errors"].append(f"{code}: {e2}")
        
        logger.info(f"✅ Batch completado: {results['updated']} actualizados, {results['created']} creados")
        return results

    def run(self, dry_run: bool = False, limit: int = 0) -> bool:
        """Ejecutar el scraper completo"""
        start_time = datetime.now()
        
        logger.info("=" * 70)
        logger.info("SV SCRAPER v2 - SERVICIOS VIALES (Sin Selenium)")
        logger.info("=" * 70)
        
        # Fase 0: Inicializar Odoo
        logger.info("\n📋 FASE 0: Conectando a Odoo...")
        if not self._init_odoo():
            return False
        
        # Mostrar ubicación
        location_info = self.odoo_connector.models.execute_kw(
            self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
            'stock.location', 'read',
            [[self.location_id]],
            {'fields': ['id', 'name', 'complete_name']}
        )
        if location_info:
            loc = location_info[0]
            logger.info(f"✅ Ubicación: {loc['complete_name']} (ID: {loc['id']})")
        
        # Fase 1: Obtener productos de Odoo
        logger.info("\n📋 FASE 1: Obteniendo productos de Odoo...")
        product_codes = self.get_odoo_products()
        if not product_codes:
            logger.error("No se encontraron productos")
            return False
        
        codes_list = list(product_codes.keys())
        if limit > 0:
            codes_list = codes_list[:limit]
            logger.info(f"⚠️ Limitado a {limit} productos")
        
        # Fase 2: Login en portal SV
        logger.info("\n📋 FASE 2: Login en portal SV...")
        if not self.login():
            return False
        
        # Fase 3: Buscar productos en API
        logger.info(f"\n📋 FASE 3: Buscando {len(codes_list)} productos en API SV...")
        stock_results, not_found_codes, found_without_stock_codes = self.scrape_all_products(codes_list)
        
        # Estadísticas primera pasada
        with_stock = sum(1 for s in stock_results.values() if s > 0)
        total_units = sum(stock_results.values())
        
        logger.info(f"\n📊 Resultados primera pasada:")
        logger.info(f"   - Con stock (>0): {with_stock}")
        logger.info(f"   - Encontrados sin stock: {len(found_without_stock_codes)}")
        logger.info(f"   - No encontrados en API: {len(not_found_codes)}")
        logger.info(f"   - Total unidades: {total_units}")
        
        # Fase 3b: Segunda pasada secuencial para no encontrados
        recovered = 0
        if not_found_codes:
            stock_results, not_found_codes, found_without_stock_codes, recovered = \
                self.retry_not_found_sequential(not_found_codes, stock_results, found_without_stock_codes)
            
            # Recalcular estadísticas
            with_stock = sum(1 for s in stock_results.values() if s > 0)
            total_units = sum(stock_results.values())
        
        # Guardar archivos TXT
        self._save_report_files(not_found_codes, found_without_stock_codes)
        
        # Fase 4: Actualizar Odoo
        logger.info(f"\n📋 FASE 4: Actualizando Odoo...")
        update_results = self.update_odoo_stock(product_codes, stock_results, dry_run)
        
        # Resumen final
        duration = datetime.now() - start_time
        
        logger.info("\n" + "=" * 70)
        logger.info("RESUMEN FINAL")
        logger.info("=" * 70)
        logger.info(f"""
    Productos en Odoo:          {len(product_codes)}
    Productos buscados:         {len(codes_list)}
    
    RESULTADOS SCRAPING:
    - Con stock (>0):           {with_stock}
    - Encontrados sin stock:    {len(found_without_stock_codes)}
    - No encontrados en API:    {len(not_found_codes)}
    - Recuperados en 2da pasada:{recovered}
    - Total unidades:           {total_units}
    
    ACTUALIZACION ODOO:
    - Quants actualizados:      {update_results['updated']}
    - Quants creados:           {update_results['created']}
    - No-storable saltados:     {update_results['skipped_non_storable']}
    - Errores:                  {len(update_results['errors'])}
    
    ARCHIVOS GENERADOS:
    - output/sv_not_found.txt
    - output/sv_found_without_stock.txt
    
    Tiempo total:               {duration}
    {"⚠️ MODO DRY-RUN - No se modificó Odoo" if dry_run else "✅ Stock actualizado en Odoo"}
        """)
        
        if update_results['errors']:
            logger.warning(f"Errores: {update_results['errors'][:5]}")
        
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='SV Scraper v2 - Sin Selenium')
    parser.add_argument('--dry-run', action='store_true',
                        help='Solo mostrar, no modificar Odoo')
    parser.add_argument('--limit', type=int, default=0,
                        help='Limitar cantidad de productos (0=todos)')
    args = parser.parse_args()
    
    try:
        config = SVConfigV2()
        scraper = SVScraperV2(config)
        success = scraper.run(dry_run=args.dry_run, limit=args.limit)
        
        if success:
            logger.info("✅ Ejecución completada")
        else:
            logger.error("❌ Ejecución fallida")
            exit(1)
            
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
