#!/usr/bin/env python3
"""
Scraper de prueba para Bluecar SA
Script standalone para testear extracción y carga antes de integrar
"""

import os
import json
import time
import requests
import xmlrpc.client
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

# Importar utilidades desde main.py
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from main import CodeNormalizer, OdooConnector

# Cargar variables de entorno
load_dotenv()

# Cache global de productos en memoria
# Se carga una vez desde el CSV y se reusa para evitar releer
_PRODUCTS_CACHE: Optional[pd.DataFrame] = None


def load_products_to_memory(csv_path: str, force_reload: bool = False) -> pd.DataFrame:
    """
    Carga el dataset de productos en memoria (cache global).

    Solo se carga una vez. Usar force_reload=True para recargar.

    Args:
        csv_path: Ruta al CSV de productos Bluecar
        force_reload: Si True, recarga el dataset aunque ya esté en memoria

    Returns:
        DataFrame con solo columnas de stock (sin proveedor ni info de compra)
    """
    global _PRODUCTS_CACHE

    if _PRODUCTS_CACHE is not None and not force_reload:
        logger.info(f"♻️ Usando dataset en memoria ({len(_PRODUCTS_CACHE)} productos)")
        return _PRODUCTS_CACHE

    logger.info(f"📁 Cargando dataset a memoria desde: {csv_path}")

    if not Path(csv_path).exists():
        logger.error(f"❌ No existe el archivo: {csv_path}")
        return pd.DataFrame()

    # Cargar solo columnas necesarias (stock, no proveedor ni info de compra)
    df = pd.read_csv(csv_path)

    # Filtrar solo columnas relevantes para stock
    stock_columns = ['id', 'codigo', 'nombre', 'descripcion', 'marca', 'categoria',
                     'stock', 'line_id', 'group_id', 'brand_id', 'image_url', 'active', 'origen']

    # Mantener solo las columnas que existen
    columns_to_keep = [col for col in stock_columns if col in df.columns]
    df = df[columns_to_keep]

    _PRODUCTS_CACHE = df
    logger.info(f"✅ Dataset cargado en memoria: {len(df)} productos, {len(df.columns)} columnas")
    logger.info(f"   Columnas: {list(df.columns)}")

    return df


def get_products_from_cache() -> Optional[pd.DataFrame]:
    """Retorna el dataset en memoria o None si no está cargado"""
    return _PRODUCTS_CACHE


def clear_products_cache():
    """Limpia el cache de productos en memoria"""
    global _PRODUCTS_CACHE
    _PRODUCTS_CACHE = None
    logger.info("🗑️ Cache de productos limpiado")


@dataclass
class BluecarConfig:
    """Configuración para el scraper de Bluecar"""
    base_url: str = "https://www.bluecar.com.ar/"
    api_url: str = "https://bluecar-api-prod.herokuapp.com/api/products"
    api_params: str = "active=true&stock=false&page={page}"
    output_dir: str = "./output"
    headless: bool = True
    page_timeout: int = 15
    request_delay: float = 0.5


class BluecarScraperTest:
    """Scraper de prueba para Bluecar SA"""

    def __init__(self, config: BluecarConfig = None):
        self.config = config or BluecarConfig()
        self.driver: Optional[webdriver.Chrome] = None
        self.session = requests.Session()

        # Credenciales desde .env o valores por defecto
        self.email = os.getenv("BLUECAR_EMAIL", "compras@performance-lube.com")
        self.password = os.getenv("BLUECAR_PASSWORD", "1155742024")

        # Configurar headless desde .env
        if os.getenv("HEADLESS", "true").lower() == "true":
            self.config.headless = True
        else:
            self.config.headless = False

        print(f"[*] Configuración:")
        print(f"    - Email: {self.email}")
        print(f"    - Headless: {self.config.headless}")
        print(f"    - Output dir: {self.config.output_dir}")

    def _get_chrome_driver(self) -> webdriver.Chrome:
        """Inicializa Chrome WebDriver con las configuraciones necesarias"""
        chrome_options = Options()

        if self.config.headless:
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--no-sandbox")

        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            print(f"[!] Error con ChromeDriverManager: {e}")
            print("[*] Intentando con ChromeDriver del sistema...")
            driver = webdriver.Chrome(options=chrome_options)

        driver.set_page_load_timeout(self.config.page_timeout)
        return driver

    def _safe_click(self, by: By, locator: str, timeout: int = 10) -> bool:
        """Click seguro con espera explícita"""
        try:
            wait = WebDriverWait(self.driver, timeout)
            element = wait.until(EC.element_to_be_clickable((by, locator)))
            element.click()
            print(f"    ✓ Click en: {locator}")
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"    ✗ Error al hacer click en {locator}: {e}")
            return False

    def _safe_send_keys(self, by: By, locator: str, text: str, timeout: int = 10, clear_first: bool = True) -> bool:
        """Envío de texto seguro con espera explícita"""
        try:
            wait = WebDriverWait(self.driver, timeout)
            element = wait.until(EC.presence_of_element_located((by, locator)))
            if clear_first:
                element.clear()
            element.send_keys(text)
            print(f"    ✓ Texto enviado a: {locator}")
            time.sleep(0.3)
            return True
        except Exception as e:
            print(f"    ✗ Error al enviar texto a {locator}: {e}")
            return False

    def login_and_get_token(self) -> Optional[str]:
        """
        Proceso de login en Bluecar y extracción del Bearer token

        Pasos:
        1. Ir a la página principal
        2. Click en botón Menu: //button[@aria-label="Menu"]
        3. Click en menú login: //ul[@role="menu"]/li[2]
        4. Ingresar email: //input[@name="email"]
        5. Ingresar password: //input[@name="password"]
        6. Click en submit: //button[@type="submit"]
        7. Extraer token del localStorage
        """
        print("\n[1] Iniciando Selenium y navegando a Bluecar...")
        self.driver = self._get_chrome_driver()

        try:
            print("[2] Cargando página principal...")
            self.driver.get(self.config.base_url)
            time.sleep(2)

            print("[3] Abriendo menú...")
            if not self._safe_click(By.XPATH, '//button[@aria-label="Menu"]'):
                return None

            print("[4] Click en opción de login...")
            if not self._safe_click(By.XPATH, '//ul[@role="menu"]/li[2]'):
                return None

            print("[5] Ingresando credenciales...")
            if not self._safe_send_keys(By.XPATH, '//input[@name="email"]', self.email):
                return None

            if not self._safe_send_keys(By.XPATH, '//input[@name="password"]', self.password):
                return None

            print("[6] Enviando formulario de login...")
            if not self._safe_click(By.XPATH, '//button[@type="submit"]'):
                return None

            # Esperar a que se complete el login
            print("[7] Esperando confirmación de login...")
            time.sleep(5)

            print("[8] Extrayendo Bearer token...")

            token = None

            # Primero verificar localStorage
            try:
                all_storage = self.driver.execute_script("""
                    let items = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        let key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                """)
                for key, value in all_storage.items():
                    if value and isinstance(value, str):
                        try:
                            data = json.loads(value)
                            if isinstance(data, dict):
                                token = data.get('token') or data.get('access_token') or data.get('accessToken') or data.get('jwt')
                                if token:
                                    print(f"    ✓ Token encontrado en localStorage (clave: '{key}')")
                                    break
                        except:
                            if 'eyJ' in value[:20]:
                                token = value
                                print(f"    ✓ Token JWT encontrado en localStorage")
                                break
            except Exception as e:
                pass

            # Si no está en localStorage, verificar sessionStorage
            if not token:
                try:
                    session_storage = self.driver.execute_script("""
                        let items = {};
                        for (let i = 0; i < sessionStorage.length; i++) {
                            let key = sessionStorage.key(i);
                            items[key] = sessionStorage.getItem(key);
                        }
                        return items;
                    """)
                    for key, value in session_storage.items():
                        if value and isinstance(value, str):
                            try:
                                data = json.loads(value)
                                if isinstance(data, dict):
                                    token = data.get('token') or data.get('access_token') or data.get('accessToken') or data.get('jwt')
                                    if token:
                                        print(f"    ✓ Token encontrado en sessionStorage")
                                        break
                            except:
                                if 'eyJ' in value[:20]:
                                    token = value
                                    print(f"    ✓ Token JWT encontrado en sessionStorage")
                                    break
                except Exception as e:
                    pass

            # Verificar cookies
            if not token:
                try:
                    cookies = self.driver.get_cookies()
                    for cookie in cookies:
                        if cookie['name'] in ['bluecar_prod_token', 'token', 'auth_token', 'jwt_token']:
                            cookie_token = cookie['value']
                            if cookie_token and ('eyJ' in cookie_token[:20]):
                                token = cookie_token
                                print(f"    ✓ Token encontrado en cookie: {cookie['name']}")
                                break
                except Exception as e:
                    pass

            if token:
                print(f"    Token: {token[:50]}...")
                return token
            else:
                print(f"    ✗ No se pudo extraer el token")
                return None

        except Exception as e:
            print(f"\n✗ Error durante el login: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _get_request_headers(self, bearer_token: str) -> Dict[str, str]:
        """Genera los headers para las peticiones a la API"""
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Authorization': f'Bearer {bearer_token}',
            'Connection': 'keep-alive',
            'Host': 'bluecar-api-prod.herokuapp.com',
            'Origin': 'https://www.bluecar.com.ar',
            'Referer': 'https://www.bluecar.com.ar/',
            'Sec-Ch-Ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?1',
            'Sec-Ch-Ua-Platform': '"Android"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36'
        }

    def _extract_product_data(self, item: Dict) -> Dict[str, any]:
        """Extrae y normaliza los datos de un producto"""
        return {
            'id': item.get('id'),
            'codigo': item.get('external_id') or item.get('code') or item.get('codigo') or '',
            'nombre': item.get('name') or item.get('nombre') or '',
            'descripcion': item.get('description') or item.get('descripcion') or '',
            'marca': item.get('brand_name') or item.get('brand') or item.get('marca') or '',
            'precio': item.get('price') or item.get('precio') or 0,
            'stock': item.get('stock') or item.get('stock_available') or 0,
            'categoria': item.get('line_name') or item.get('group_name') or item.get('category') or item.get('categoria') or '',
            'line_id': item.get('line_id'),
            'group_id': item.get('group_id'),
            'brand_id': item.get('brand_id'),
            'image_url': item.get('image_url') or '',
            'active': item.get('active', True),
            'origen': 'BLUECAR'
        }

    def scrape_all_pages(self, bearer_token: str, max_pages: int = None) -> List[Dict]:
        """
        Scrapea todas las páginas de productos de la API

        Args:
            bearer_token: Token de autenticación
            max_pages: Máximo de páginas a scrapear (None = todas, sin límite)
        """
        print("\n[9] Iniciando scraping de productos...")
        print(f"    URL base: {self.config.api_url}")
        if max_pages:
            print(f"    Límite de páginas: {max_pages}")
        else:
            print(f"    Modo: Todas las páginas (sin límite)")

        headers = self._get_request_headers(bearer_token)
        all_products = []
        page = 1

        while True:
            # Parar si se alcanzó max_pages
            if max_pages and page > max_pages:
                print(f"\n[*] Límite de páginas alcanzado: {max_pages}")
                break

            url = f"{self.config.api_url}?{self.config.api_params.format(page=page)}"
            print(f"\n    [*] Página {page}: {url}")

            try:
                response = self.session.get(url, headers=headers, timeout=15)

                # Manejar códigos de respuesta
                if response.status_code == 401:
                    print(f"    ✗ Error 401: Token expirado o inválido")
                    break
                elif response.status_code == 404:
                    print(f"    ✓ No hay más páginas (404)")
                    break
                elif response.status_code != 200:
                    print(f"    ✗ Error {response.status_code}: {response.text[:200]}")
                    break

                data = response.json()

                # La API puede devolver los datos directamente o en una clave
                if isinstance(data, list):
                    products = data
                elif isinstance(data, dict):
                    products = data.get('results') or data.get('data') or data.get('products') or data.get('items') or []
                else:
                    products = []

                if not products:
                    print(f"    ✓ No más productos en página {page}")
                    print(f"\n[✓] Scraping completado: {len(all_products)} productos totales en {page - 1} páginas")
                    break

                print(f"    ✓ {len(products)} productos encontrados")

                for item in products:
                    product_data = self._extract_product_data(item)
                    all_products.append(product_data)

                page += 1
                time.sleep(self.config.request_delay)

            except requests.exceptions.Timeout:
                print(f"    ✗ Timeout en página {page}")
                break
            except Exception as e:
                print(f"    ✗ Error en página {page}: {e}")
                break

        return all_products

    def save_to_csv(self, products: List[Dict], filename: str = None):
        """Guarda los productos en un archivo CSV"""
        if not products:
            print("\n[!] No hay productos para guardar")
            return None

        os.makedirs(self.config.output_dir, exist_ok=True)

        if filename is None:
            timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
            filename = f"bluecar_products_{timestamp}.csv"

        filepath = os.path.join(self.config.output_dir, filename)

        df = pd.DataFrame(products)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')

        print(f"\n[✓] CSV guardado:")
        print(f"    - Archivo: {filepath}")
        print(f"    - Productos: {len(products)}")
        print(f"    - Columnas: {list(df.columns)}")

        return filepath

    def cleanup(self):
        """Cierra el driver de Selenium"""
        if self.driver:
            self.driver.quit()
            print("\n[*] Selenium cerrado")

    def run(self, max_pages: int = None, save_csv: bool = True) -> Tuple[bool, List[Dict], Optional[str]]:
        """
        Ejecuta el proceso completo de scraping

        Args:
            max_pages: Máximo de páginas a scrapear (None = todas)
            save_csv: Si True, guarda los resultados en CSV

        Returns:
            Tuple con (success, products, csv_path)
        """
        try:
            print("=" * 60)
            print("BLUECAR SCRAPER - TEST STANDALONE")
            print("=" * 60)

            # Paso 1: Login y obtener token
            bearer_token = self.login_and_get_token()
            if not bearer_token:
                return False, [], None

            # Paso 2: Scrapear productos
            products = self.scrape_all_pages(bearer_token, max_pages=max_pages)

            if not products:
                print("\n[✗] No se obtuvieron productos")
                return False, [], None

            print(f"\n[✓] Scraping completado:")
            print(f"    - Total de productos: {len(products)}")

            # Paso 3: Guardar CSV
            csv_path = None
            if save_csv:
                csv_path = self.save_to_csv(products)

            return True, products, csv_path

        except Exception as e:
            print(f"\n[✗] Error durante la ejecución: {e}")
            import traceback
            traceback.print_exc()
            return False, [], None
        finally:
            self.cleanup()


def main():
    """Función principal para ejecutar el scraper"""

    import argparse
    parser = argparse.ArgumentParser(description='Scraper Bluecar')
    parser.add_argument('--scrape', action='store_true', help='Ejecutar scraping')
    parser.add_argument('--process', action='store_true', help='Procesar y subir a Odoo')
    parser.add_argument('--bluecar-csv', type=str, help='CSV de Bluecar (para --process)')
    parser.add_argument('--merged-csv', type=str, help='CSV de productos merged (para --process)')
    parser.add_argument('--no-dry-run', action='store_true', help='Ejecutar cambios reales en Odoo')
    args = parser.parse_args()

    # Si no hay argumentos, ejecutar scraping por defecto
    if not args.scrape and not args.process:
        args.scrape = True

    if args.scrape:
        # Crear scraper
        scraper = BluecarScraperTest()

        # Ejecutar (max_pages=None para todas las páginas)
        success, products, csv_path = scraper.run(max_pages=None, save_csv=True)

        if success:
            print("\n" + "=" * 60)
            print("SCRAPING COMPLETADO CON ÉXITO")
            print("=" * 60)

            # Cargar productos en memoria para uso posterior
            if csv_path:
                print(f"\n[*] Cargando productos en memoria...")
                load_products_to_memory(csv_path)

            # Mostrar primeros productos como muestra
            if products:
                print("\n[✓] Primeros 3 productos:")
                for i, p in enumerate(products[:3], 1):
                    print(f"\n    Producto {i}:")
                    for k, v in p.items():
                        if v:
                            print(f"        - {k}: {v}")
        else:
            print("\n" + "=" * 60)
            print("SCRAPING FALLÓ")
            print("=" * 60)

    if args.process:
        bluecar_csv = args.bluecar_csv or "./output/bluecar_products.csv"
        merged_csv = args.merged_csv or "./output/productos_merged.csv"
        dry_run = not args.no_dry_run

        process_bluecar_to_odoo(
            bluecar_csv=bluecar_csv,
            productos_merged=merged_csv,
            dry_run=dry_run,
            use_cache=True
        )




class BluecarProductMatcher:
    """Clase para hacer match de productos Bluecar con Odoo y cargar stock"""

    def __init__(self, bluecar_csv_path: str = None, productos_merged_path: str = None,
                 use_cache: bool = True):
        """
        Args:
            bluecar_csv_path: Ruta al CSV (opcional si use_cache=True y ya está cargado)
            productos_merged_path: Ruta al CSV de productos merged
            use_cache: Si True, usa el dataset en memoria en lugar de leer CSV
        """
        self.bluecar_csv_path = Path(bluecar_csv_path) if bluecar_csv_path else None
        self.productos_merged_path = Path(productos_merged_path) if productos_merged_path else None
        self.df_bluecar = None
        self.df_productos = None
        self.matched_products = []
        self.scraping_to_odoo_code = {}
        self.use_cache = use_cache

        # Inicializar conector Odoo
        self.odoo_connector = OdooConnector(type('Config', (), {
            'odoo_url': os.getenv('ODOO_URL', 'http://localhost:8069'),
            'odoo_db': os.getenv('ODOO_DB', 'odoo'),
            'odoo_user': os.getenv('ODOO_USER', 'admin'),
            'odoo_password': os.getenv('ODOO_PASSWORD', 'admin')
        })())

    def load_datasets(self) -> bool:
        """Cargar los datasets de Bluecar y productos merged"""
        try:
            logger.info("📁 Cargando datasets...")

            # Cargar CSV de Bluecar - usar caché si está disponible
            if self.use_cache and _PRODUCTS_CACHE is not None:
                self.df_bluecar = _PRODUCTS_CACHE
                logger.info(f"♻️ Bluecar desde caché: {len(self.df_bluecar)} registros")
            elif self.bluecar_csv_path and self.bluecar_csv_path.exists():
                self.df_bluecar = load_products_to_memory(str(self.bluecar_csv_path))
                logger.info(f"✅ Bluecar desde CSV: {len(self.df_bluecar)} registros")
            else:
                if self.use_cache:
                    logger.error("❌ No hay dataset en memoria ni CSV proporcionado")
                    logger.error("   Ejecuta primero: load_products_to_memory('ruta/al.csv')")
                else:
                    logger.error(f"❌ No existe el archivo: {self.bluecar_csv_path}")
                return False

            # Cargar CSV de productos merged
            if self.productos_merged_path and self.productos_merged_path.exists():
                self.df_productos = pd.read_csv(self.productos_merged_path)
                logger.info(f"✅ Productos merged: {len(self.df_productos)} registros")
            else:
                logger.error(f"❌ No existe el archivo: {self.productos_merged_path}")
                return False

            return True

        except Exception as e:
            logger.error(f"❌ Error al cargar datasets: {e}")
            return False

    def match_products(self) -> Tuple[int, int]:
        """Hacer match de productos usando normalización de códigos"""
        try:
            logger.info("🔍 Iniciando match de productos...")

            # Obtener códigos de productos Odoo (desde productos_merged)
            codigos_odoo = set()
            codigos_odoo_norm = {}  # normalized -> original

            # Usar la columna codigo_merged como referencia principal
            if 'codigo_merged' in self.df_productos.columns:
                df_clean = self.df_productos.dropna(subset=['codigo_merged'])
                for code in df_clean['codigo_merged']:
                    original_code = str(code).strip()
                    normalized_code = CodeNormalizer.normalize_code(code)
                    if normalized_code:
                        codigos_odoo.add(original_code)
                        codigos_odoo_norm[normalized_code] = original_code

            logger.info(f"📊 Códigos Odoo únicos: {len(codigos_odoo)}")

            # Obtener códigos de Bluecar
            codigos_bluecar = set()
            codigos_bluecar_norm = {}

            if 'codigo' in self.df_bluecar.columns:
                df_clean = self.df_bluecar.dropna(subset=['codigo'])
                for code in df_clean['codigo']:
                    original_code = str(code).strip()
                    normalized_code = CodeNormalizer.normalize_code(code)
                    if normalized_code and original_code:
                        codigos_bluecar.add(original_code)
                        codigos_bluecar_norm[normalized_code] = original_code

            logger.info(f"📊 Códigos Bluecar únicos: {len(codigos_bluecar)}")

            # Encontrar coincidencias exactas
            matched_exact = codigos_odoo.intersection(codigos_bluecar)
            for code in matched_exact:
                self.scraping_to_odoo_code[code] = code

            # Encontrar coincidencias normalizadas
            matched_normalized = set()
            for norm_code in codigos_odoo_norm:
                if norm_code in codigos_bluecar_norm:
                    scraping_code = codigos_bluecar_norm[norm_code]
                    odoo_code = codigos_odoo_norm[norm_code]
                    matched_normalized.add(scraping_code)
                    self.scraping_to_odoo_code[scraping_code] = odoo_code

            total_matched = len(matched_exact) + len(matched_normalized)

            logger.info(f"✅ Coincidencias exactas: {len(matched_exact)}")
            logger.info(f"🔍 Coincidencias normalizadas: {len(matched_normalized)}")
            logger.info(f"🎯 Total coincidencias: {total_matched}")

            # Preparar lista de productos matched con datos para cargar a Odoo
            for scraping_code, odoo_code in self.scraping_to_odoo_code.items():
                bluecar_row = self.df_bluecar[self.df_bluecar['codigo'] == scraping_code]
                if not bluecar_row.empty:
                    product_data = bluecar_row.iloc[0].to_dict()
                    # Convertir a formato esperado por Odoo
                    self.matched_products.append({
                        'codigo': odoo_code,  # Usar código Odoo para actualizar
                        'codigo_scraping': scraping_code,
                        'precioCosto': product_data.get('precio', 0),
                        'disponibilidad': product_data.get('stock', 0),
                        'descripcion': product_data.get('nombre', ''),
                        'marca': product_data.get('marca', ''),
                        'origen': 'BLUECAR'
                    })

            return len(matched_exact), len(matched_normalized)

        except Exception as e:
            logger.error(f"❌ Error en match de productos: {e}")
            import traceback
            traceback.print_exc()
            return 0, 0

    def prepare_odoo_cached_data(self) -> Optional[Dict]:
        """Preparar datos cacheados para actualización optimizada a Odoo"""
        try:
            if not self.odoo_connector.connect():
                logger.error("❌ No se pudo conectar a Odoo")
                return None

            logger.info("🔄 Preparando datos cacheados de Odoo...")

            # Obtener location_id de TODO/Stock/StockSCRAP
            location_id = self.odoo_connector._get_depo_scraping_location()
            if not location_id:
                logger.error("❌ No se encontró ubicación TODO/Stock/StockSCRAP")
                return None

            # Obtener supplier_id para Bluecar (o crearlo)
            supplier_id = self._get_or_create_bluecar_supplier()
            if not supplier_id:
                logger.warning("⚠️ No se pudo crear proveedor Bluecar")

            # Obtener información de productos coincidentes
            product_info = {}
            kits_info = set()

            for product in self.matched_products:
                odoo_code = product['codigo']
                # Buscar producto por código
                product_id = self.odoo_connector.search_product_by_code(odoo_code)
                if product_id:
                    # Obtener template_id
                    try:
                        p_data = self.odoo_connector.models.execute_kw(
                            self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                            'product.product', 'read',
                            [[product_id]],
                            {'fields': ['product_tmpl_id', 'type']}
                        )
                        if p_data:
                            template_id = p_data[0]['product_tmpl_id'][0]
                            product_type = p_data[0].get('type', 'product')

                            product_info[odoo_code] = {
                                'product_id': product_id,
                                'template_id': template_id,
                                'is_storable': product_type == 'product'
                            }

                            # Verificar si es kit
                            boms = self.odoo_connector.models.execute_kw(
                                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                                'mrp.bom', 'search_read',
                                [[['product_tmpl_id', '=', template_id]]],
                                {'fields': ['id'], 'limit': 1}
                            )
                            if boms:
                                kits_info.add(template_id)

                    except Exception as e:
                        logger.warning(f"⚠️ Error obteniendo info de producto {odoo_code}: {e}")

            logger.info(f"✅ Datos cacheados preparados:")
            logger.info(f"   - Productos encontrados: {len(product_info)}")
            logger.info(f"   - Kits detectados: {len(kits_info)}")
            logger.info(f"   - Location ID: {location_id}")
            logger.info(f"   - Supplier ID: {supplier_id}")

            return {
                'scraping_location_id': location_id,
                'supplier_id': supplier_id,
                'product_info': product_info,
                'kits_info': kits_info,
                'existing_rules': {}  # No usado por ahora
            }

        except Exception as e:
            logger.error(f"❌ Error preparando datos cacheados: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _get_or_create_bluecar_supplier(self) -> Optional[int]:
        """Obener o crear proveedor Bluecar"""
        try:
            suppliers = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'res.partner', 'search_read',
                [[['name', '=', 'Bluecar SA (Scraping)'], ['supplier_rank', '>', 0]]],
                {'fields': ['id', 'name']}
            )

            if suppliers:
                logger.info(f"✅ Proveedor Bluecar existente: {suppliers[0]['id']}")
                return suppliers[0]['id']

            # Crear nuevo proveedor
            logger.info("➕ Creando proveedor 'Bluecar SA (Scraping)'...")
            supplier_id = self.odoo_connector.models.execute_kw(
                self.odoo_connector.db, self.odoo_connector.uid, self.odoo_connector.password,
                'res.partner', 'create',
                [{
                    'name': 'Bluecar SA (Scraping)',
                    'company_type': 'company',
                    'supplier_rank': 1,
                    'customer_rank': 0,
                    'is_company': True,
                    'email': 'info@bluecar.com.ar',
                    'comment': 'Proveedor automático generado por sistema de scraping - Bluecar SA'
                }]
            )
            logger.info(f"✅ Proveedor 'Bluecar SA (Scraping)' creado: {supplier_id}")
            return supplier_id

        except Exception as e:
            logger.error(f"❌ Error creando proveedor Bluecar: {e}")
            return None

    def upload_to_odoo(self, cached_data: Dict, dry_run: bool = True) -> Dict:
        """Cargar productos matched a Odoo"""
        try:
            logger.info(f"🚀 Iniciando carga a Odoo (dry_run={dry_run})...")

            # Preparar datos para batch update
            products_data = []
            for product in self.matched_products:
                odoo_code = product['codigo']
                if odoo_code in cached_data['product_info']:
                    products_data.append((odoo_code, product))

            logger.info(f"📊 Productos para actualizar: {len(products_data)}")

            if dry_run:
                logger.info("⚠️ MODO DRY RUN - No se realizarán cambios en Odoo")
                for code, data in products_data[:5]:  # Mostrar primeros 5
                    logger.info(f"   - {code}: stock={data.get('disponibilidad', 0)}, "
                              f"precio=${data.get('precioCosto', 0)}")
                if len(products_data) > 5:
                    logger.info(f"   ... y {len(products_data) - 5} más")
                return {
                    "success": True,
                    "dry_run": True,
                    "total_products": len(products_data)
                }

            # Ejecutar actualización batch
            results = self.odoo_connector.update_matched_products_batch(
                products_data, cached_data
            )

            return results

        except Exception as e:
            logger.error(f"❌ Error en carga a Odoo: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def save_matched_report(self, output_path: str = None):
        """Guardar reporte de productos matched"""
        try:
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"./output/bluecar_matched_{timestamp}.csv"

            df_matched = pd.DataFrame(self.matched_products)
            df_matched.to_csv(output_path, index=False, encoding='utf-8-sig')
            logger.info(f"✅ Reporte guardado: {output_path}")

        except Exception as e:
            logger.error(f"❌ Error guardando reporte: {e}")


def process_bluecar_to_odoo(bluecar_csv: str = None, productos_merged: str = None,
                              dry_run: bool = True, use_cache: bool = True) -> bool:
    """
    Función principal para procesar productos Bluecar y cargar a Odoo

    Args:
        bluecar_csv: Ruta al CSV de productos Bluecar (opcional si ya está en memoria)
        productos_merged: Ruta al CSV de productos merged (Odoo)
        dry_run: Si True, solo muestra lo que haría sin ejecutar cambios
        use_cache: Si True, usa el dataset en memoria en lugar de leer CSV
    """
    try:
        logger.info("=" * 60)
        logger.info("BLUECAR PRODUCT MATCHER & ODOO UPLOADER")
        logger.info("=" * 60)

        # Cargar productos a memoria si se proporciona CSV
        if bluecar_csv and use_cache:
            load_products_to_memory(bluecar_csv)

        # Crear matcher
        matcher = BluecarProductMatcher(
            bluecar_csv_path=bluecar_csv,
            productos_merged_path=productos_merged,
            use_cache=use_cache
        )

        # Cargar datasets
        if not matcher.load_datasets():
            return False

        # Hacer match de productos
        exact, normalized = matcher.match_products()

        if exact + normalized == 0:
            logger.warning("⚠️ No se encontraron coincidencias")
            return False

        # Preparar datos cacheados de Odoo
        cached_data = matcher.prepare_odoo_cached_data()
        if not cached_data:
            logger.error("❌ No se pudieron preparar datos de Odoo")
            return False

        # Cargar a Odoo
        results = matcher.upload_to_odoo(cached_data, dry_run=dry_run)

        # Guardar reporte
        matcher.save_matched_report()

        logger.info("=" * 60)
        if results.get("success"):
            logger.info("✅ PROCESO COMPLETADO")
        else:
            logger.error("❌ PROCESO FALLÓ")
        logger.info("=" * 60)

        return results.get("success", False)

    except Exception as e:
        logger.error(f"❌ Error en proceso principal: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    main()
