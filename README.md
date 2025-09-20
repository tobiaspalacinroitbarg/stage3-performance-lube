# PrAutoParte Scraper

Scraper profesional para extraer datos de productos desde [PrAutoParte](https://www.prautopartes.com.ar/).

## Características

- ✅ Scraping automatizado con Selenium y requests
- ✅ Manejo robusto de errores y reintentos
- ✅ Logging detallado con rotación de archivos
- ✅ Configuración por variables de entorno
- ✅ Soporte para deployment con Docker
- ✅ Gestión automática de ChromeDriver
- ✅ Exportación a CSV estructurado

## Estructura del Proyecto

```
prauto-scraper/
├── main.py              # Script principal profesionalizado
├── requirements.txt     # Dependencias de Python
├── .env.example        # Ejemplo de variables de entorno
├── Dockerfile          # Imagen Docker
├── docker-compose.yml  # Orquestación Docker
├── README.md           # Documentación
├── logs/               # Directorio de logs (se crea automáticamente)
└── output/             # Directorio de salida (se crea automáticamente)
```

## Instalación en Linux

### Método Automatizado (Recomendado)
```bash
# Hacer ejecutable el script
chmod +x setup_linux.sh

# Ejecutar instalación automática
./setup_linux.sh
```

### Método Manual

1. **Instalar dependencias del sistema:**

   **Ubuntu/Debian:**
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-pip python3-venv chromium-browser chromium-chromedriver xvfb
   ```
   
   **CentOS/RHEL/Fedora:**
   ```bash
   sudo dnf install -y python3 python3-pip chromium chromedriver xorg-x11-server-Xvfb
   ```
   
   **Arch/Manjaro:**
   ```bash
   sudo pacman -S python python-pip chromium chromedriver xorg-server-xvfb
   ```

2. **Configurar el proyecto:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   # Editar .env con tus credenciales
   ```

3. **Ejecutar:**
   ```bash
   python main.py
   ```

## Instalación Local (Windows)

1. **Configurar el entorno:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configurar variables de entorno:**
   ```bash
   copy .env.example .env
   # Editar .env con tus credenciales
   ```

3. **Ejecutar el scraper:**
   ```bash
   python main.py
   ```

## Deployment con Docker

### Opción 1: Docker Build Manual
```bash
# Construir imagen
docker build -t prauto-scraper .

# Ejecutar contenedor
docker run --rm \
  --env-file .env \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/logs:/app/logs \
  prauto-scraper
```

### Opción 2: Docker Compose
```bash
# Ejecutar una vez
docker-compose run --rm prauto-scraper

# O ejecutar en background
docker-compose up -d
```

### Opción 3: Con Scheduling (Cron)
```bash
# Crear archivo de cron
echo "0 2 * * * cd /app && python main.py" > crontab

# Ejecutar con cron
docker-compose --profile cron up -d scraper-cron
```

## Variables de Entorno

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `PRAUTO_USERNAME` | Usuario para login | `30-71727423-3` |
| `PRAUTO_PASSWORD` | Contraseña para login | `10831` |
| `HEADLESS` | Ejecutar Chrome sin GUI | `true/false` |

## Configuración Avanzada

El scraper se puede configurar modificando la clase `ScrapingConfig` en `main.py`:

```python
@dataclass
class ScrapingConfig:
    base_url: str = "https://www.prautopartes.com.ar/"
    output_file: str = "articulos.csv"
    page_timeout: int = 10           # Timeout para cargar páginas
    request_delay: float = 0.5       # Pausa entre peticiones API
    window_size: str = "1920,1080"   # Tamaño de ventana del browser
```

## Logs

Los logs se guardan automáticamente en el directorio `logs/` con:
- Rotación diaria
- Retención de 7 días
- Formato estructurado con timestamp

## Archivos de Salida

El scraper guarda los datos en archivos CSV con la fecha del scraping:

```
articulos_2025-09-20.csv  # Scraping del 20 de septiembre de 2025
articulos_2025-09-21.csv  # Scraping del 21 de septiembre de 2025
```

### Gestión de Archivos CSV

Usa el script `csv_manager.py` para gestionar los archivos:

```bash
# Listar archivos CSV disponibles
python csv_manager.py list

# Ver información detallada del último CSV
python csv_manager.py info

# Ver información de un CSV específico
python csv_manager.py info 2025-09-20

# Comparar dos archivos CSV
python csv_manager.py compare 2025-09-20 2025-09-21

# Limpiar archivos antiguos (>7 días)
python csv_manager.py cleanup --days 7
```

| Campo | Descripción |
|-------|-------------|
| `id` | ID único del producto |
| `codigo` | Código del producto |
| `marca` | Marca del producto |
| `descripcion` | Descripción detallada |
| `precioLista` | Precio de lista |
| `precioCosto` | Precio de costo |
| `precioVenta` | Precio de venta |
| `descuentos` | Descuentos aplicables |
| `disponibilidad` | Estado de disponibilidad |
| `origen` | Origen del producto |
| `fotos` | URLs de fotos (separadas por coma) |

## Troubleshooting

### Error: "Credenciales no encontradas"
- Verificar que el archivo `.env` existe y tiene las credenciales correctas
- Asegurar que las variables `PRAUTO_USERNAME` y `PRAUTO_PASSWORD` están definidas

### Error: "ChromeDriver not found"
- El script usa `webdriver-manager` para descargar automáticamente ChromeDriver
- En Docker, Chrome se instala automáticamente

### Error de timeout en elementos
- Ajustar `page_timeout` en la configuración
- Verificar conectividad de red
- El sitio web puede estar lento o caído

### Problemas de memoria en Docker
- Aumentar memoria disponible para Docker
- Usar `--shm-size=2g` si hay problemas con Chrome

## Consideraciones de Rendimiento

- **Rate Limiting**: El scraper incluye pausas entre peticiones (`request_delay`)
- **Memoria**: Chrome puede usar mucha RAM, especialmente en modo GUI
- **Red**: Las peticiones son secuenciales para evitar sobrecargar el servidor

## Mantenimiento

- Los logs se rotan automáticamente cada día
- Actualizar dependencias regularmente: `pip install -r requirements.txt --upgrade`
- Monitorear cambios en la estructura del sitio web

## Licencia

Este proyecto es para uso educativo y de desarrollo. Respetar los términos de servicio del sitio web objetivo.