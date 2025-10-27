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

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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
    page_timeout: int = int(os.getenv("PAGE_TIMEOUT", "10"))
    request_delay: float = float(os.getenv("REQUEST_DELAY", "0.5"))
    window_size: str = "1920,1080"
    batch_size: int = int(os.getenv("BATCH_SIZE", "10"))

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
        """Buscar producto por código"""
        if not self.models:
            return None

        try:
            product_ids = self.models.execute_kw(
                self.db, self.uid, self.password,
                'product.product', 'search_read',
                [[['default_code', '=', product_code]]],
                {'fields': ['id', 'default_code']}
            )

            if product_ids:
                logger.info(f"Producto encontrado: {product_code} (ID: {product_ids[0]['id']})")
                return product_ids[0]['id']
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
            result = self.odoo_connector.create_or_update_product(product_data)
            if result.get("success"):
                action = result.get("action", "processed")
                logger.info(f"✅ Producto {product_data.get('codigo')} {action} en Odoo")
                return True
            else:
                logger.error(f"❌ Error al enviar producto {product_data.get('codigo')} a Odoo: {result.get('error')}")
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
    
    def scrape_products(self, num_pages: int, bearer_token: str) -> None:
        """Realizar scraping profesional de productos con manejo robusto de errores"""
        logger.info(f"🚀 Iniciando scraping de {num_pages} páginas...")

        # Configuración inicial
        headers = self._get_request_headers(bearer_token)
        total_items = 0
        successful_pages = 0
        failed_pages = 0
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

            # Configuración de procesamiento por lotes
            batch_products = []
            batch_size = self.config.batch_size

            logger.info(f"⚙️  Configuración:")
            logger.info(f"   📦 Tamaño de lote Odoo: {batch_size}")
            logger.info(f"   ⏱️  Retraso entre peticiones: {self.config.request_delay}s")
            logger.info(f"   ⌛ Timeout de página: {self.config.page_timeout}s")
            logger.info(f"   🌐 Integración Odoo: {'✅ Activa' if odoo_connected else '❌ Inactiva'}")

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

                    # Procesar items de la página
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

                            # Procesamiento para Odoo si está conectado
                            if self.config.send_to_odoo and odoo_connected:
                                batch_products.append(extracted_data)

                                # Enviar lote cuando alcanza el tamaño
                                if len(batch_products) >= batch_size:
                                    batch_result = self._send_batch_to_odoo(batch_products)
                                    if batch_result.get("success"):
                                        logger.info(f"✅ Lote {len(batch_products)} productos a Odoo: {batch_result.get('success_rate', 0):.1f}% éxito")
                                    else:
                                        logger.error(f"❌ Error al enviar lote a Odoo: {batch_result.get('error')}")
                                    batch_products = []

                        except Exception as e:
                            logger.error(f"❌ Error procesando item en página {page}: {e}")
                            continue

                    # Estadísticas de la página
                    page_end_time = datetime.now()
                    page_duration = page_end_time - page_start_time
                    successful_pages += 1

                    logger.info(f"✅ Página {page} completada - Items: {page_items_processed}/{len(items)} - Tiempo: {page_duration.total_seconds():.1f}s")

                    # Enviar último lote parcial si hay items
                    if batch_products and page == num_pages - 1:
                        batch_result = self._send_batch_to_odoo(batch_products)
                        if batch_result.get("success"):
                            logger.info(f"✅ Último lote a Odoo: {batch_result.get('success_rate', 0):.1f}% éxito")
                        batch_products = []

                    # Pausa controlada entre peticiones
                    if page < num_pages - 1:  # No pausar en la última página
                        sleep_time = self.config.request_delay
                        logger.debug(f"⏱️  Pausa de {sleep_time}s...")
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

            logger.info("🎉 Scraping completado!")
            logger.info(f"   📊 Items procesados: {total_items}")
            logger.info(f"   📄 Páginas exitosas: {successful_pages}/{num_pages-1} ({success_rate:.1f}%)")
            logger.info(f"   ❌ Páginas fallidas: {failed_pages}")
            logger.info(f"   ⏱️  Tiempo total: {duration}")
            logger.info(f"   📈 Velocidad: {total_items/duration.total_seconds():.2f} items/segundo")
            logger.info(f"   📄 Archivo CSV: {output_path.name}")
            logger.info(f"   📁 Ubicación: {output_path.absolute()}")

            if self.config.send_to_odoo and odoo_connected:
                logger.info(f"   🌐 Datos también enviados a Odoo")
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

            # Limpiar recursos
            if batch_products:
                logger.warning(f"⚠️ Quedaron {len(batch_products)} productos sin enviar a Odoo")
    
    def run(self) -> None:
        """Ejecutar el proceso completo de scraping"""
        try:
            logger.info("Iniciando PrAutoParte Scraper...")
            
            # Obtener datos de sesión
            num_pages, bearer_token = self.login_and_get_session_data()
            
            # Realizar scraping
            self.scrape_products(num_pages, bearer_token)
            
            logger.info("Proceso completado exitosamente")
            
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