"""
Scraper para portal.sv.com.ar (Servicios Viales de Santa Fe S A)

Flujo:
1. Obtener de Odoo los códigos de productos con SV como proveedor principal
2. Login en portal.sv.com.ar via Selenium para obtener session cookie
3. Buscar cada código via API REST (GET /api/searchresults?query=CODE)
4. Extraer disponibilidad y precio
5. Actualizar stock en Odoo (misma lógica que PrAutoParte: stock.quant en TODO/Stock/StockSCRAP)
"""

import os
import time
import json
import threading
import requests as http_requests
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
import argparse

from main import CodeNormalizer, OdooConnector

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SVConfig:
    """Configuración para el scraper de Servicios Viales"""

    # Portal SV
    base_url: str = "https://portal.sv.com.ar"
    login_url: str = "https://portal.sv.com.ar/login"
    search_api_url: str = "https://portal.sv.com.ar/api/searchresults"

    # Credenciales SV (desde .env)
    sv_username: str = os.getenv("SV_USERNAME", "")
    sv_password: str = os.getenv("SV_PASSWORD", "")

    # Nombre del proveedor en Odoo (exacto)
    supplier_name: str = "SERVICIOS VIALES DE SANTA FE S A"

    # Odoo (mismas vars que main.py)
    odoo_url: str = os.getenv("ODOO_URL", "http://localhost:8069")
    odoo_db: str = os.getenv("ODOO_DB", "odoo")
    odoo_user: str = os.getenv("ODOO_USER", "admin")
    odoo_password: str = os.getenv("ODOO_PASSWORD", "admin")

    # Directorios
    output_dir: str = os.getenv("OUTPUT_DIR", "./output")
    logs_dir: str = os.getenv("PM2_LOG_DIR", "./logs")

    # Rendimiento
    request_delay: float = float(os.getenv("SV_REQUEST_DELAY", "0.2"))
    max_workers: int = int(os.getenv("SV_MAX_WORKERS", "5"))
    page_timeout: int = int(os.getenv("PAGE_TIMEOUT", "15"))
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"

    # Archivos
    odoo_products_file: str = os.getenv("ODOO_PRODUCTS_FILE", "Producto (product.template).xlsx")
    merged_output_file: str = os.getenv("MERGED_OUTPUT_FILE", "productos_merged.csv")

    def __post_init__(self):
        if not self.sv_username or not self.sv_password:
            raise ValueError("SV_USERNAME y SV_PASSWORD son obligatorias en .env")
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class SVScraper:
    """Scraper para portal Servicios Viales"""

    def __init__(self, config: SVConfig):
        self.config = config
        self.session_cookie: Optional[str] = None
        self.http_session: Optional[http_requests.Session] = None
        self.driver: Optional[webdriver.Chrome] = None

        # Odoo connector
        self._init_odoo_connector()

    def _init_odoo_connector(self):
        """Inicializar OdooConnector con un config compatible"""
        from main import ScrapingConfig

        orig_prauto_user = os.environ.get("PRAUTO_USERNAME")
        orig_prauto_pass = os.environ.get("PRAUTO_PASSWORD")
        if not orig_prauto_user:
            os.environ["PRAUTO_USERNAME"] = "dummy"
        if not orig_prauto_pass:
            os.environ["PRAUTO_PASSWORD"] = "dummy"

        try:
            scraping_config = ScrapingConfig()
            self.odoo_connector = OdooConnector(scraping_config)
        finally:
            if orig_prauto_user is None:
                os.environ.pop("PRAUTO_USERNAME", None)
            else:
                os.environ["PRAUTO_USERNAME"] = orig_prauto_user
            if orig_prauto_pass is None:
                os.environ.pop("PRAUTO_PASSWORD", None)
            else:
                os.environ["PRAUTO_PASSWORD"] = orig_prauto_pass

    # ------------------------------------------------------------------
    # Phase 1: Login via Selenium to get NextAuth session cookie
    # ------------------------------------------------------------------

    def _get_chrome_driver(self) -> webdriver.Chrome:
        """Crear Chrome driver configurado"""
        options = Options()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        installed_path = ChromeDriverManager().install()
        # webdriver_manager bug: a veces devuelve THIRD_PARTY_NOTICES en vez del binario
        if Path(installed_path).name != "chromedriver":
            chromedriver_path = str(Path(installed_path).parent / "chromedriver")
        else:
            chromedriver_path = installed_path

        service = Service(chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return driver

    def login_and_get_session(self) -> bool:
        """Login al portal SV via Selenium y obtener la session cookie de NextAuth"""
        logger.info("🔐 Iniciando login en portal.sv.com.ar...")

        try:
            self.driver = self._get_chrome_driver()
            self.driver.get(self.config.login_url)
            time.sleep(3)

            wait = WebDriverWait(self.driver, self.config.page_timeout)
            email_input = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="email"]'))
            )
            email_input.clear()
            email_input.send_keys(self.config.sv_username)

            password_input = self.driver.find_element(By.CSS_SELECTOR, 'input[name="password"]')
            password_input.clear()
            password_input.send_keys(self.config.sv_password)

            submit_btn = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            submit_btn.click()
            time.sleep(6)

            current_url = self.driver.current_url
            if "/login" in current_url:
                logger.error("❌ Login falló - seguimos en la página de login")
                return False

            logger.info(f"✅ Login exitoso - URL: {current_url}")

            cookies = self.driver.get_cookies()
            session_cookie = None
            for cookie in cookies:
                if cookie["name"] == "__Secure-next-auth.session-token":
                    session_cookie = cookie["value"]
                    break

            if not session_cookie:
                for cookie in cookies:
                    if "session-token" in cookie["name"]:
                        session_cookie = cookie["value"]
                        break

            if not session_cookie:
                logger.error("❌ No se encontró la cookie de sesión NextAuth")
                logger.debug(f"Cookies disponibles: {[c['name'] for c in cookies]}")
                return False

            self.session_cookie = session_cookie
            logger.info(f"✅ Session cookie obtenida: {session_cookie[:40]}...")

            self.http_session = http_requests.Session()
            for cookie in cookies:
                self.http_session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", "portal.sv.com.ar"),
                    path=cookie.get("path", "/"),
                )

            self.http_session.headers.update({
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://portal.sv.com.ar/",
                "User-Agent": self.driver.execute_script("return navigator.userAgent"),
            })

            return True

        except Exception as e:
            logger.error(f"❌ Error durante login: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None

    # ------------------------------------------------------------------
    # Phase 2: Search products via API
    # ------------------------------------------------------------------

    def search_product(self, code: str) -> List[Dict]:
        """Buscar un producto por código en la API del portal SV
        Thread-safe: usa self.http_session que es safe para requests concurrentes.
        """
        if not self.http_session:
            return []

        try:
            url = f"{self.config.search_api_url}?query={code}"
            response = self.http_session.get(url, timeout=30)

            if response.status_code == 401:
                logger.error("❌ Sesión expirada (401)")
                return []

            if response.status_code != 200:
                logger.warning(f"⚠️ Búsqueda {code}: status {response.status_code}")
                return []

            data = response.json()
            if not isinstance(data, list):
                return []

            return data

        except http_requests.exceptions.Timeout:
            logger.warning(f"⚠️ Timeout buscando {code}")
            return []
        except Exception as e:
            logger.error(f"❌ Error buscando {code}: {e}")
            return []

    def _has_any_stock(self, product: Dict) -> bool:
        """Verificar si un producto tiene stock en CUALQUIER sucursal"""
        return any([
            product.get("disponibleSF", 0) > 0,
            product.get("disponibleBA", 0) > 0,
            product.get("disponibleMDZ", 0) > 0,
            product.get("disponibleSA", 0) > 0,
            product.get("disponibleOTROS", 0) > 0,
        ])

    def _process_search_result(self, code: str) -> Tuple[str, Optional[Dict]]:
        """Buscar un código y procesar el resultado. Retorna (code, data_dict o None).
        Diseñado para ejecución paralela.
        """
        results = self.search_product(code)
        if not results:
            return (code, None)

        # Buscar coincidencia exacta
        exact_match = None
        for product in results:
            if product.get("codigo", "").strip().upper() == code.strip().upper():
                exact_match = product
                break

        if not exact_match:
            return (code, None)

        has_stock = self._has_any_stock(exact_match)

        data = {
            'disponibilidad': 1 if has_stock else 0,
            'precioCosto': exact_match.get('precioUnitario', 0),
            'moneda': exact_match.get('moneda', 'U$S'),
            'descripcion': exact_match.get('descripcion', ''),
            'marca': exact_match.get('marcaPortal', ''),
            'descuento': exact_match.get('descuento', 0),
            'disponibleSF': exact_match.get('disponibleSF', 0),
            'disponibleBA': exact_match.get('disponibleBA', 0),
            'disponibleMDZ': exact_match.get('disponibleMDZ', 0),
            'disponibleSA': exact_match.get('disponibleSA', 0),
            'disponibleOTROS': exact_match.get('disponibleOTROS', 0),
        }
        return (code, data)

    # ------------------------------------------------------------------
    # Phase 3: Get product codes from Odoo
    # ------------------------------------------------------------------

    def get_odoo_product_codes(self) -> Dict[str, Dict]:
        """Obtener códigos de productos de Odoo que tienen a SV como proveedor principal

        Returns: dict { default_code: { product_id, template_id, is_storable } }

        Nota: is_storable=True para 'product' y 'consu' (consumibles también trackean
        stock en stock.quant en esta implementación, coherente con los datos existentes).
        """
        logger.info(f"🔍 Obteniendo productos con proveedor principal '{self.config.supplier_name}'...")

        if not self.odoo_connector.connect():
            logger.error("❌ No se pudo conectar a Odoo")
            return {}

        product_ids = self.odoo_connector._get_product_ids_by_supplier(self.config.supplier_name)
        if not product_ids:
            logger.warning("⚠️ No se encontraron productos con este proveedor")
            return {}

        logger.info(f"📊 {len(product_ids)} product.product IDs encontrados")

        product_codes = {}
        product_ids_list = list(product_ids)
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
                        # Tanto 'product' (storable) como 'consu' (consumible) pueden
                        # tener stock.quant. Solo 'service' se excluye.
                        product_type = p.get('type', '')
                        product_codes[code] = {
                            'product_id': p['id'],
                            'template_id': p['product_tmpl_id'][0] if p.get('product_tmpl_id') else None,
                            'is_storable': product_type in ('product', 'consu'),
                        }

            except Exception as e:
                logger.error(f"❌ Error leyendo batch {i}-{i + batch_size}: {e}")

        logger.info(f"✅ {len(product_codes)} productos con código válido obtenidos de Odoo")
        return product_codes

    # ------------------------------------------------------------------
    # Phase 4: Prepare Odoo cached data for batch update
    # ------------------------------------------------------------------

    def prepare_odoo_cached_data(self, product_codes: Dict[str, Dict]) -> Optional[Dict]:
        """Preparar datos cacheados para actualización batch en Odoo"""
        try:
            logger.info("🔄 Preparando datos cacheados de Odoo...")

            location_id = self.odoo_connector._get_depo_scraping_location()
            if not location_id:
                logger.error("❌ No se encontró ubicación TODO/Stock/StockSCRAP")
                return None

            supplier_id = self.odoo_connector._get_supplier_id_by_name(self.config.supplier_name)
            if not supplier_id:
                logger.warning(f"⚠️ No se encontró proveedor '{self.config.supplier_name}'")

            product_info = {}
            template_ids = set()

            for code, info in product_codes.items():
                if info.get('template_id'):
                    product_info[code] = info
                    template_ids.add(info['template_id'])

            # Detectar kits (productos con mrp.bom)
            kits_info = set()
            template_ids_list = list(template_ids)

            for i in range(0, len(template_ids_list), 200):
                batch = template_ids_list[i:i + 200]
                try:
                    boms = self.odoo_connector.models.execute_kw(
                        self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                        'mrp.bom', 'search_read',
                        [[['product_tmpl_id', 'in', batch]]],
                        {'fields': ['product_tmpl_id']}
                    )
                    for bom in boms:
                        if bom.get('product_tmpl_id'):
                            kits_info.add(bom['product_tmpl_id'][0])
                except Exception as e:
                    logger.warning(f"⚠️ Error detectando kits batch {i}: {e}")

            logger.info(f"✅ Datos cacheados preparados:")
            logger.info(f"   - Productos: {len(product_info)}")
            logger.info(f"   - Kits detectados: {len(kits_info)}")
            logger.info(f"   - Location ID: {location_id}")
            logger.info(f"   - Supplier ID: {supplier_id}")

            return {
                'scraping_location_id': location_id,
                'supplier_id': supplier_id,
                'product_info': product_info,
                'kits_info': kits_info,
                'existing_rules': {},
            }

        except Exception as e:
            logger.error(f"❌ Error preparando datos cacheados: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ------------------------------------------------------------------
    # Phase 5: Main execution flow
    # ------------------------------------------------------------------

    def _scrape_all_codes_parallel(self, codes_list: List[str]) -> Dict[str, Dict]:
        """Buscar todos los códigos en paralelo usando ThreadPoolExecutor.
        Cada worker hace un GET liviano a la API SV.
        """
        scraped_results = {}
        found_count = 0
        not_found_count = 0
        error_count = 0
        total = len(codes_list)
        processed = 0
        lock = threading.Lock()
        start_time = datetime.now()

        def _on_result(code, data):
            nonlocal found_count, not_found_count, processed
            with lock:
                processed += 1
                if data:
                    scraped_results[code] = data
                    found_count += 1
                else:
                    not_found_count += 1

                if processed % 100 == 0 or processed == total:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta_sec = (total - processed) / rate if rate > 0 else 0
                    logger.info(
                        f"📊 Progreso: {processed}/{total} ({processed/total*100:.1f}%) - "
                        f"Encontrados: {found_count} - No encontrados: {not_found_count} - "
                        f"ETA: {eta_sec/60:.1f} min"
                    )

        workers = self.config.max_workers
        logger.info(f"🚀 Usando {workers} workers paralelos, delay={self.config.request_delay}s")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for code in codes_list:
                future = executor.submit(self._process_search_result, code)
                futures[future] = code
                # Small stagger between submissions to avoid burst
                time.sleep(self.config.request_delay / workers)

            for future in as_completed(futures):
                try:
                    code, data = future.result(timeout=60)
                    _on_result(code, data)
                except Exception as e:
                    code = futures[future]
                    with lock:
                        processed += 1
                        error_count += 1
                    logger.error(f"❌ Error procesando {code}: {e}")

        logger.info(f"\n📊 RESUMEN SCRAPING:")
        logger.info(f"   - Total códigos buscados: {total}")
        logger.info(f"   - Encontrados (match exacto): {found_count}")
        logger.info(f"   - No encontrados: {not_found_count}")
        logger.info(f"   - Errores: {error_count}")

        return scraped_results

    def run(self, dry_run: bool = False) -> bool:
        """Ejecutar el scraper completo"""
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("SV SCRAPER - SERVICIOS VIALES DE SANTA FE")
        logger.info("=" * 60)

        # --- Paso 1: Obtener códigos de Odoo ---
        logger.info("\n📋 FASE 1: Obteniendo códigos de productos desde Odoo...")
        product_codes = self.get_odoo_product_codes()
        if not product_codes:
            logger.error("❌ No se obtuvieron códigos de Odoo. Abortando.")
            return False

        # --- Paso 2: Login en portal SV ---
        logger.info("\n🔐 FASE 2: Login en portal.sv.com.ar...")
        if not self.login_and_get_session():
            logger.error("❌ Login falló. Abortando.")
            return False

        # --- Paso 3: Buscar códigos en paralelo ---
        codes_list = list(product_codes.keys())
        logger.info(f"\n🔍 FASE 3: Buscando {len(codes_list)} códigos en portal SV...")

        scraped_results = self._scrape_all_codes_parallel(codes_list)

        if not scraped_results:
            logger.warning("⚠️ No se encontró ningún producto. Abortando.")
            return False

        # Estadísticas de disponibilidad
        with_stock = sum(1 for v in scraped_results.values() if v['disponibilidad'] == 1)
        without_stock = sum(1 for v in scraped_results.values() if v['disponibilidad'] == 0)
        logger.info(f"   - Con stock (alguna sucursal): {with_stock}")
        logger.info(f"   - Sin stock (ninguna sucursal): {without_stock}")

        # --- Paso 4: Actualizar Odoo ---
        logger.info(f"\n📦 FASE 4: Actualizando {len(scraped_results)} productos en Odoo...")

        if dry_run:
            logger.info("⚠️ MODO DRY RUN - No se realizarán cambios en Odoo")
            for code, data in list(scraped_results.items())[:10]:
                logger.info(f"   - {code}: disp={data['disponibilidad']}, "
                           f"precio={data['precioCosto']} {data['moneda']}")
            if len(scraped_results) > 10:
                logger.info(f"   ... y {len(scraped_results) - 10} más")

            duration = datetime.now() - start_time
            logger.info(f"\n⏱️ Tiempo total: {duration}")
            return True

        # Preparar cached_data para batch update
        cached_data = self.prepare_odoo_cached_data(product_codes)
        if not cached_data:
            logger.error("❌ No se pudieron preparar datos de Odoo. Abortando actualización.")
            return False

        products_data = []
        for code, data in scraped_results.items():
            if code in cached_data['product_info']:
                products_data.append((code, data))

        logger.info(f"📊 Productos para actualizar en Odoo: {len(products_data)}")

        if not products_data:
            logger.warning("⚠️ Ningún producto matched para actualizar")
            return False

        # Ejecutar batch update
        results = self.odoo_connector.update_matched_products_batch(products_data, cached_data)

        # Resumen final
        duration = datetime.now() - start_time
        logger.info("\n" + "=" * 60)
        logger.info("✅ SV SCRAPER COMPLETADO")
        logger.info(f"   - Códigos buscados: {len(codes_list)}")
        logger.info(f"   - Productos encontrados: {len(scraped_results)}")
        logger.info(f"   - Productos actualizados en Odoo: {len(products_data)}")
        if results.get("stock"):
            stock = results["stock"]
            logger.info(f"   - Stock updated: {len(stock.get('updated', []))}")
            logger.info(f"   - Stock created: {len(stock.get('created', []))}")
            logger.info(f"   - Kits skipped: {len(stock.get('kits_skipped', []))}")
            logger.info(f"   - Non-storable skipped: {len(stock.get('non_storable_skipped', []))}")
            logger.info(f"   - Errors: {len(stock.get('errors', []))}")
        logger.info(f"   - Tiempo total: {duration}")
        logger.info("=" * 60)

        return results.get("success", False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Función principal"""
    parser = argparse.ArgumentParser(description='SV Scraper - Servicios Viales')
    parser.add_argument('--dry-run', action='store_true',
                       help='Solo mostrar lo que haría sin actualizar Odoo')
    parser.add_argument('--once', action='store_true',
                       help='Ejecutar una sola vez (default)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Limitar cantidad de productos a procesar (0=todos)')
    args = parser.parse_args()

    try:
        config = SVConfig()
        scraper = SVScraper(config)
        success = scraper.run(dry_run=args.dry_run)

        if success:
            logger.info("✅ Ejecución exitosa")
        else:
            logger.error("❌ Ejecución fallida")
            exit(1)

    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
