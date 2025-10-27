# PrAutoParte Scraper - Sistema Profesional de Scraping

Scraper profesional para extraer datos de productos desde [PrAutoParte](https://www.prautopartes.com.ar/) con soporte completo para deployment en producción con PM2, Docker e integración con Odoo.

## Características Principales

### 🔥 Scraping Profesional
- ✅ Scraping automatizado con Selenium y requests
- ✅ Manejo robusto de errores y reintentos
- ✅ Logging detallado con rotación de archivos
- ✅ Configuración por variables de entorno
- ✅ Gestión automática de ChromeDriver
- ✅ Exportación a CSV estructurado

### 🚀 Deployment Profesional
- ✅ Soporte para PM2 (process manager)
- ✅ Configuración Docker completa
- ✅ Integración automática con Odoo
- ✅ Scheduling automático cada 4 horas
- ✅ Monitoreo y reinicio automático
- ✅ Gestión de memoria y recursos

### 🌐 Integración Empresarial
- ✅ API XML-RPC para Odoo
- ✅ Sincronización automática de productos
- ✅ Gestión de categorías por marca
- ✅ Actualización de precios y stock
- ✅ Manejo de productos duplicados

## Arquitectura del Sistema

```
prauto-scraper/
├── main.py                     # Script principal profesionalizado
├── requirements.txt            # Dependencias de Python
├── .env.example               # Ejemplo de variables de entorno
├── .env                      # Variables de entorno de producción
├── ecosystem.config.js       # Configuración PM2
├── Dockerfile                # Imagen Docker
├── docker-compose.yml        # Orquestación Docker
├── setup_linux.sh            # Script de instalación automática
├── csv_manager.py            # Gestión de archivos CSV
├── logs/                     # Directorio de logs (automático)
├── output/                   # Directorio de salida (automático)
├── README.md                 # Documentación completa
└── .gitignore               # Archivos a ignorar
```

## 🚀 Guía de Instalación y Deployment

### Paso 1: Instalación del Sistema

#### Opción A: Instalación Automatizada (Recomendada)
```bash
# Clonar repositorio (si aplica)
git clone https://github.com/tustage3/stage3-performance-lube.git
cd stage3-performance-lube

# Ejecutar instalación automática
chmod +x setup_linux.sh
./setup_linux.sh
```

#### Opción B: Instalación Manual Complete

1. **Instalar dependencias del sistema:**

   **Ubuntu/Debian 20.04/22.04:**
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y python3 python3-pip python3-venv python3-dev curl wget gnupg software-properties-common
   sudo apt install -y chromium-browser chromium-chromedriver xvfb
   ```

   **CentOS/RHEL 8/9:**
   ```bash
   sudo dnf update -y
   sudo dnf install -y python3 python3-pip python3-devel curl wget which
   sudo dnf install -y chromium chromedriver xorg-x11-server-Xvfb
   ```

   **Arch/Manjaro:**
   ```bash
   sudo pacman -Syu
   sudo pacman -S python python-pip python-virtualenv curl wget
   sudo pacman -S chromium chromedriver xorg-server-xvfb
   ```

2. **Instalar PM2 (Process Manager):**
   ```bash
   # Instalar Node.js y PM2
   curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
   sudo apt install -y nodejs
   sudo npm install -g pm2
   ```

3. **Configurar entorno Python:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configurar variables de entorno:**
   ```bash
   cp .env.example .env
   nano .env  # Editar con tus credenciales
   ```

### Paso 2: Configuración de Producción

#### Configuración de Variables de Entorno
Crear y editar el archivo `.env`:

```bash
# Copiar template
cp .env.example .env
nano .env
```

Configurar las siguientes variables:

```env
# ===== CREDENCIALES PRAUTOPARTE =====
PRAUTO_USERNAME=tu_usuario_prautoparte
PRAUTO_PASSWORD=tu_contraseña_prautoparte

# ===== CONFIGURACIÓN SCRAPER =====
HEADLESS=true  # Siempre true en producción
PYTHONPATH=/home/ubuntu/stage3-performance-lube
PYTHONUNBUFFERED=1

# ===== CONFIGURACIÓN ODOO =====
ODOO_URL=http://your-odoo-server.com:8069
ODOO_DB=your_database_name
ODOO_USER=your_odoo_user
ODOO_PASSWORD=your_odoo_password
SEND_TO_ODOO=true  # true/false para enviar datos a Odoo

# ===== CONFIGURACIÓN AVANZADA =====
PM2_LOG_DIR=/home/ubuntu/stage3-performance-lube/logs
OUTPUT_DIR=/home/ubuntu/stage3-performance-lube/output
```

#### Configuración de Directorios
```bash
# Crear directorios necesarios
mkdir -p logs output
chmod 755 logs output
```

### Paso 3: Iniciar el Servicio

#### Opción A: Ejecución Única
```bash
# Activar entorno
source venv/bin/activate

# Ejecutar scraper una vez
python main.py --once
```

#### Opción B: PM2 Process Manager (Recomendado)
```bash
# Iniciar el proceso con PM2
pm2 start ecosystem.config.js

# Verificar estado
pm2 status
pm2 logs prauto-scraper

# Reiniciar si es necesario
pm2 restart prauto-scraper
```

#### Opción C: Docker
```bash
# Construir y ejecutar con Docker Compose
docker-compose up -d

# Ver logs
docker-compose logs -f prauto-scraper
```

### Paso 4: Configuración de Monitoreo

#### Configuración de Logs Rotativos
PM2 maneja automáticamente la rotación de logs. Para configurar:

```bash
# Instalar plugin de rotación de logs
pm2 install pm2-logrotate

# Configurar rotación diaria
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 30
pm2 set pm2-logrotate:compress true
```

#### Monitoreo del Sistema
```bash
# Ver procesos activos
pm2 monit

# Ver lista de procesos
pm2 list

# Ver uso de memoria y CPU
pm2 info prauto-scraper
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

## ⚙️ Configuración y Variables de Entorno

### Variables de Entorno Esenciales

| Variable | Descripción | Valor Ejemplo | Obligatorio |
|----------|-------------|---------------|-------------|
| `PRAUTO_USERNAME` | Usuario para login PrAutoParte | `30-71727423-3` | ✅ |
| `PRAUTO_PASSWORD` | Contraseña para login PrAutoParte | `10831` | ✅ |
| `HEADLESS` | Ejecutar Chrome sin GUI | `true` | ❌ |
| `PYTHONPATH` | Ruta del proyecto | `/home/ubuntu/stage3-performance-lube` | ❌ |
| `PYTHONUNBUFFERED` | Buffer de Python | `1` | ❌ |

### Variables de Entorno para Odoo

| Variable | Descripción | Valor Ejemplo | Obligatorio |
|----------|-------------|---------------|-------------|
| `ODOO_URL` | URL del servidor Odoo | `http://localhost:8069` | ❌ |
| `ODOO_DB` | Nombre de la base de datos Odoo | `odoo` | ❌ |
| `ODOO_USER` | Usuario de Odoo | `admin` | ❌ |
| `ODOO_PASSWORD` | Contraseña de Odoo | `admin` | ❌ |
| `SEND_TO_ODOO` | Enviar datos a Odoo | `true/false` | ❌ |

### Variables de Entorno de Producción

| Variable | Descripción | Valor Ejemplo | Obligatorio |
|----------|-------------|---------------|-------------|
| `PM2_LOG_DIR` | Directorio de logs PM2 | `/home/ubuntu/stage3-performance-lube/logs` | ❌ |
| `OUTPUT_DIR` | Directorio de salida CSV | `/home/ubuntu/stage3-performance-lube/output` | ❌ |
| `NODE_ENV` | Entorno de Node.js | `production` | ❌ |

### Ejemplo de .env Completo
```env
# ===== CREDENCIALES PRAUTOPARTE =====
PRAUTO_USERNAME=tu_usuario_prautoparte
PRAUTO_PASSWORD=tu_contraseña_prautoparte

# ===== CONFIGURACIÓN SCRAPER =====
HEADLESS=true
PYTHONPATH=/home/ubuntu/stage3-performance-lube
PYTHONUNBUFFERED=1

# ===== CONFIGURACIÓN ODOO =====
ODOO_URL=http://your-odoo-server.com:8069
ODOO_DB=production_db
ODOO_USER=api_user
ODOO_PASSWORD=secure_password
SEND_TO_ODOO=true

# ===== CONFIGURACIÓN AVANZADA =====
PM2_LOG_DIR=/home/ubuntu/stage3-performance-lube/logs
OUTPUT_DIR=/home/ubuntu/stage3-performance-lube/output
NODE_ENV=production
```

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

## 🛠️ Administración y Mantenimiento

### Comandos PM2 Esenciales

```bash
# Iniciar y detener
pm2 start ecosystem.config.js
pm2 stop prauto-scraper
pm2 restart prauto-scraper
pm2 delete prauto-scraper

# Monitoreo
pm2 status
pm2 monit
pm2 logs prauto-scraper
pm2 info prauto-scraper

# Gestión de procesos
pm2 save              # Guardar procesos actuales
pm2 resurrect         # Restaurar procesos guardados
pm2 startup           # Configurar inicio automático
pm2 unstartup         # Desactivar inicio automático
```

### Actualización del Sistema

```bash
# Actualizar dependencias Python
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt --upgrade

# Actualizar Chrome y ChromeDriver
sudo apt update && sudo apt upgrade -y chromium-browser chromium-chromedriver

# Reiniciar servicio PM2
pm2 restart prauto-scraper
```

### Rotación y Gestión de Logs

```bash
# Ver logs en tiempo real
pm2 logs prauto-scraper --lines 100

# Configurar rotación automática
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 30
pm2 set pm2-logrotate:compress true

# Limpiar logs antiguos manualmente
find logs/ -name "*.log" -mtime +30 -delete
```

### 🚨 Troubleshooting Común

#### Errores de Autenticación PrAutoParte
```bash
# Error: "Credenciales no encontradas"
- Verificar archivo .env existe
- Comprobar PRAUTO_USERNAME y PRAUTO_PASSWORD
- Probar credenciales manualmente en el sitio web

# Error: "Token de autorización no encontrado"
- Las credenciales pueden ser incorrectas
- El sitio web puede haber cambiado el login
- Verificar la estructura de sesión
```

#### Problemas con Chrome/ChromeDriver
```bash
# Error: "ChromeDriver not found"
- Usar el script setup_linux.sh
- Verificar instalación: chromium-browser --version
- Revisar instalación: chromedriver --version

# Error: "ChromeDriver cannot be killed"
- Matar procesos zombie: pkill -f chrome
- Limpiar procesos: pkill -f chromedriver
- Reiniciar servicio PM2

# Error de memoria Chrome
- Configurar límite de memoria en ecosystem.config.js
- Usar `--disable-dev-shm-usage` en Chrome options
- Aumentar memoria Docker: --memory="2g"
```

#### Errores de Conexión Odoo
```bash
# Error: "Falló la autenticación con Odoo"
- Verificar URL, DB, usuario y contraseña
- Probar conexión manual: curl http://odoo-server:8069
- Verificar firewall y puertos

# Error: "Connection refused"
- Odoo no está corriendo
- Puerto 8069 bloqueado
- URL incorrecta en configuración
```

#### Errores PM2
```bash
# PM2 no inicia automáticamente
pm2 startup
pm2 save

# Proceso consume mucha memoria
pm2 restart prauto-scraper
# Ajustar max_memory_restart en ecosystem.config.js

# Logs no rotan
pm2 install pm2-logrotate
pm2 restart prauto-scraper
```

### 🔒 Consideraciones de Seguridad

#### Configuración Segura
- Usar variables de entorno para credenciales
- No commitear archivo .env
- Usar HTTPS para Odoo si está disponible
- Configurar firewall para acceso a puertos

#### Permisos de Sistema
```bash
# Permisos recomendados
chmod 600 .env                    # Solo usuario dueño
chmod 755 logs output             # Acceso para servidor web
chmod 700 venv                    # Solo usuario dueño

# Crear usuario dedicado
sudo useradd -r -s /bin/false scraper
sudo chown -R scraper:scraper /home/ubuntu/stage3-performance-lube
```

#### Backup y Recuperación
```bash
# Backup de configuración
tar -czf backup_config.tar.gz .env ecosystem.config.js requirements.txt

# Backup de datos
tar -czf backup_data.tar.gz output/ logs/

# Recuperación
tar -xzf backup_config.tar.gz
tar -xzf backup_data.tar.gz
pm2 restart prauto-scraper
```

### 📊 Monitoreo y Alertas

#### Métricas Clave
- Tiempo de ejecución promedio
- Cantidad de productos procesados
- Uso de memoria y CPU
- Errores de conexión
- Status de integración Odoo

#### Alertas Sugeridas
```bash
# Monitorear uso de memoria
pm2 monit | grep prauto-scraper

# Verificar logs de error
grep -i error logs/scraper_$(date +%Y-%m-%d).log

# Verificar ejecución reciente
ls -la output/articulos_$(date +%Y-%m-%d).csv
```

### 🔧 Configuración Avanzada

### Ajustes de Rendimiento
El scraper se puede configurar modificando la clase `ScrapingConfig` en `main.py`:

```python
@dataclass
class ScrapingConfig:
    base_url: str = "https://www.prautopartes.com.ar/"
    catalog_url: str = "https://www.prautopartes.com.ar/catalogo"
    api_url: str = "https://www.prautopartes.com.ar/api/Articulos/Buscar"
    output_dir: str = "./output"

    # Configuración Odoo
    odoo_url: str = "http://localhost:8069"
    odoo_db: str = "odoo"
    odoo_user: str = "admin"
    odoo_password: str = "admin"

    # Ajustes de rendimiento
    page_timeout: int = 10           # Timeout para cargar páginas
    request_delay: float = 0.5       # Pausa entre peticiones API
    window_size: str = "1920,1080"   # Tamaño de ventana del browser
    send_to_odoo: bool = True        # Enviar datos directamente a Odoo
    batch_size: int = 10             # Tamaño de lote para Odoo
```

### Escalabilidad Horizontal
```bash
# Para múltiples instancias (modificar ecosystem.config.js)
instances: 'max',  # Usar todos los CPUs disponibles
exec_mode: 'cluster'  # Modo cluster

# Ejecutar múltiples scrapers
pm2 start ecosystem.config.js -i max
```

### Configuración de Scheduling
```bash
# Modificar scheduling en ecosystem.config.js
cron_restart: '0 */4 * * *'  # Cada 4 horas

# O configurar via cron system
crontab -e
# Agregar: 0 */4 * * * cd /home/ubuntu/stage3-performance-lube && pm2 restart prauto-scraper
```

## 📝 Licencia y Términos de Uso

Este proyecto es para uso educativo y de desarrollo. **Es responsabilidad del usuario:**

- Respetar los términos de servicio de PrAutoParte
- No sobrecargar los servidores del sitio web objetivo
- Cumplir con las políticas de robots.txt
- Mantener confidencialidad de credenciales y datos
- Usar el scraper de manera ética y responsable

**Aviso Legal:** El uso de este scraper es bajo su propio riesgo. Los desarrolladores no son responsables por el mal uso o consecuencias del uso de esta herramienta.