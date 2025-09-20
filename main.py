import os
import json
import csv
import time
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

# Cargar variables de entorno
load_dotenv()

@dataclass
class ScrapingConfig:
    """Configuración del scraper"""
    base_url: str = "https://www.prautopartes.com.ar/"
    catalog_url: str = "https://www.prautopartes.com.ar/catalogo"
    api_url: str = "https://www.prautopartes.com.ar/api/Articulos/Buscar"
    output_dir: str = "."  # Carpeta base del proyecto
    page_timeout: int = 10
    request_delay: float = 0.5
    window_size: str = "1920,1080"
    
    def get_output_filename(self) -> str:
        """Generar nombre del archivo con fecha actual"""
        today = datetime.now().strftime("%Y-%m-%d")
        return f"articulos_{today}.csv"
    
    def get_output_path(self) -> Path:
        """Obtener ruta completa del archivo de salida"""
        return Path(self.output_dir) / self.get_output_filename()

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
    
    def _setup_logging(self) -> None:
        """Configurar sistema de logging"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        logger.add(
            log_dir / "scraper_{time:YYYY-MM-DD}.log",
            rotation="1 day",
            retention="7 days",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
        )
    
    def _get_chrome_driver(self) -> webdriver.Chrome:
        """Crear instancia del driver Chrome/Chromium con configuración optimizada"""
        chrome_options = Options()
        chrome_options.add_argument(f"--window-size={self.config.window_size}")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-images")  # Mejora rendimiento
        chrome_options.add_argument("--disable-javascript")  # Solo si no es necesario JS
        chrome_options.add_argument("--disable-css-media-queries")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_argument("--disable-features=TranslateUI")
        
        # Para deployment en servidor sin GUI (siempre headless en Linux)
        if os.getenv("HEADLESS", "true").lower() == "true":
            chrome_options.add_argument("--headless=new")  # Usar nuevo headless mode
        
        # Configuración específica para Linux/Chromium
        if os.name == 'posix':  # Linux/Unix
            # Intentar encontrar Chromium primero
            chromium_paths = [
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/snap/bin/chromium",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable"
            ]
            
            for path in chromium_paths:
                if os.path.exists(path):
                    chrome_options.binary_location = path
                    logger.info(f"Usando browser: {path}")
                    break
        
        try:
            # Método 1: webdriver-manager
            logger.info("Instalando ChromeDriver con webdriver-manager...")
            driver_path = ChromeDriverManager().install()
            logger.info(f"ChromeDriver instalado en: {driver_path}")
            
            service = Service(driver_path)
            return webdriver.Chrome(service=service, options=chrome_options)
            
        except Exception as e:
            logger.warning(f"Error con webdriver-manager: {e}")
            
            # Método 2: chromedriver del sistema (Linux)
            try:
                logger.info("Intentando con chromedriver del PATH...")
                return webdriver.Chrome(options=chrome_options)
                
            except Exception as e2:
                logger.warning(f"Error con chromedriver del PATH: {e2}")
                
                # Método 3: chromedriver instalado via apt (Linux)
                try:
                    logger.info("Intentando con chromedriver instalado via apt...")
                    service = Service("/usr/bin/chromedriver")
                    return webdriver.Chrome(service=service, options=chrome_options)
                    
                except Exception as e3:
                    logger.error(f"Error con chromedriver de apt: {e3}")
                    
                    # Error final con instrucciones específicas para Linux
                    error_msg = """
                    Error al inicializar ChromeDriver en Linux. Soluciones:
                    
                    OPCIÓN 1 - Instalar via apt (Ubuntu/Debian):
                        sudo apt update
                        sudo apt install -y chromium-browser chromium-chromedriver
                    
                    OPCIÓN 2 - Snap (más actualizado):
                        sudo snap install chromium
                    
                    OPCIÓN 3 - Docker (recomendado para servidores):
                        docker-compose up prauto-scraper
                    
                    OPCIÓN 4 - Manual:
                        wget https://chromedriver.storage.googleapis.com/LATEST_RELEASE
                        # Seguir instrucciones en README.md
                    """
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
    
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
    
    def scrape_products(self, num_pages: int, bearer_token: str) -> None:
        """Realizar scraping de todos los productos"""
        logger.info(f"Iniciando scraping de {num_pages} páginas...")
        
        headers = self._get_request_headers(bearer_token)
        fields = [
            "id", "codigo", "marca", "descripcion", "precioLista", "precioCosto",
            "precioVenta", "descuentos", "disponibilidad", "origen", "fotos"
        ]
        
        # Obtener ruta del archivo con fecha
        output_path = self.config.get_output_path()
        logger.info(f"Guardando datos en: {output_path.absolute()}")
        
        # Verificar si el archivo ya existe
        if output_path.exists():
            logger.warning(f"El archivo {output_path.name} ya existe y será sobrescrito")
        
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                
                total_items = 0
                start_time = datetime.now()
                
                for page in range(1, num_pages):
                    try:
                        payload = self._create_payload(page)
                        
                        response = self.session.post(
                            self.config.api_url,
                            headers=headers,
                            data=payload,
                            timeout=30
                        )
                        response.raise_for_status()
                        
                        data = response.json()
                        items = data.get("items", [])
                        
                        for item in items:
                            extracted_data = self._extract_item_data(item)
                            writer.writerow(extracted_data)
                            total_items += 1
                        
                        logger.info(f"Página {page}/{num_pages-1} procesada - Items: {len(items)} - Total: {total_items}")
                        
                        # Pausa entre peticiones para evitar sobrecarga
                        time.sleep(self.config.request_delay)
                        
                    except requests.RequestException as e:
                        logger.error(f"Error en página {page}: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Error inesperado en página {page}: {e}")
                        continue
                
                end_time = datetime.now()
                duration = end_time - start_time
                
                logger.info(f"✅ Scraping completado exitosamente!")
                logger.info(f"   📄 Archivo: {output_path.name}")
                logger.info(f"   📁 Ubicación: {output_path.absolute()}")
                logger.info(f"   📊 Items guardados: {total_items}")
                logger.info(f"   ⏱️  Tiempo total: {duration}")
                logger.info(f"   📈 Velocidad: {total_items/duration.total_seconds():.2f} items/segundo")
                
        except Exception as e:
            logger.error(f"Error al crear archivo CSV: {e}")
            raise
    
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
    config = ScrapingConfig()
    scraper = PrAutoParteScraper(config)
    scraper.run()

if __name__ == "__main__":
    main()