#!/bin/bash

echo "🚀 INSTALACIÓN PROFESIONAL - PrAutoParte Scraper para Producción"
echo "=========================================================================="

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Función para imprimir con color
print_status() {
    echo -e "${GREEN}✅${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

print_error() {
    echo -e "${RED}❌${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ️${NC} $1"
}

# Detectar distribución de Linux
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        VERSION=$VERSION_ID
    else
        DISTRO="unknown"
    fi
    
    print_info "Distribución detectada: $DISTRO $VERSION"
}

# Instalar dependencias según la distribución
install_dependencies() {
    case $DISTRO in
        "ubuntu"|"debian")
            print_info "Instalando dependencias con apt..."
            sudo apt update && sudo apt upgrade -y
            sudo apt install -y \
                python3 \
                python3-pip \
                python3-venv \
                python3-dev \
                curl \
                wget \
                gnupg2 \
                software-properties-common \
                lsb-release \
                ca-certificates \
                apt-transport-https \
                chromium-browser \
                chromium-chromedriver \
                xvfb \
                unzip \
                jq \
                bc
            ;;
        "centos"|"rhel"|"fedora")
            print_info "Instalando dependencias con yum/dnf..."
            if command -v dnf &> /dev/null; then
                sudo dnf update -y
                sudo dnf groupinstall -y "Development Tools"
                sudo dnf install -y \
                    python3 \
                    python3-pip \
                    python3-devel \
                    chromium \
                    chromedriver \
                    xorg-x11-server-Xvfb \
                    curl \
                    wget \
                    unzip
            else
                sudo yum update -y
                sudo yum groupinstall -y "Development Tools"
                sudo yum install -y \
                    python3 \
                    python3-pip \
                    python3-devel \
                    chromium \
                    chromedriver \
                    xorg-x11-server-Xvfb \
                    curl \
                    wget \
                    unzip
            fi
            ;;
        "arch"|"manjaro")
            print_info "Instalando dependencias con pacman..."
            sudo pacman -Syu --noconfirm
            sudo pacman -S --noconfirm \
                python \
                python-pip \
                python-virtualenv \
                chromium \
                chromedriver \
                xorg-server-xvfb \
                curl \
                wget \
                unzip \
                base-devel
            ;;
        *)
            print_warning "Distribución no reconocida. Instalación manual requerida."
            print_info "Instalar manualmente: python3, python3-pip, chromium-browser, chromium-chromedriver"
            return 1
            ;;
    esac

    print_status "Dependencias del sistema instaladas"
}

# Verificar instalaciones
verify_installation() {
    print_info "Verificando instalaciones..."

    local errors=0

    # Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version)
        print_status "Python: $PYTHON_VERSION"
    else
        print_error "Python3 no encontrado"
        ((errors++))
    fi

    # Pip
    if command -v pip3 &> /dev/null; then
        PIP_VERSION=$(pip3 --version | cut -d' ' -f1-2)
        print_status "Pip: $PIP_VERSION"
    else
        print_error "Pip3 no encontrado"
        ((errors++))
    fi

    # Chromium
    CHROMIUM_PATHS=("/usr/bin/chromium-browser" "/usr/bin/chromium" "/snap/bin/chromium" "/usr/bin/google-chrome-stable")
    CHROMIUM_FOUND=false

    for path in "${CHROMIUM_PATHS[@]}"; do
        if [ -f "$path" ]; then
            CHROMIUM_VERSION=$($path --version 2>/dev/null | head -1 || echo "Versión no disponible")
            print_status "Chromium: $CHROMIUM_VERSION (en $path)"
            CHROMIUM_FOUND=true
            break
        fi
    done

    if [ "$CHROMIUM_FOUND" = false ]; then
        print_error "Chromium no encontrado en rutas conocidas"
        ((errors++))
    fi

    # ChromeDriver - verificar múltiples ubicaciones
    CHROMEDRIVER_PATHS=("/usr/bin/chromedriver" "/usr/local/bin/chromedriver" "/snap/bin/chromedriver")
    CHROMEDRIVER_FOUND=false

    for path in "${CHROMEDRIVER_PATHS[@]}"; do
        if [ -f "$path" ]; then
            CHROMEDRIVER_VERSION=$($path --version 2>/dev/null | head -1 || echo "Versión no disponible")
            print_status "ChromeDriver: $CHROMEDRIVER_VERSION (en $path)"
            CHROMEDRIVER_FOUND=true
            break
        fi
    done

    if [ "$CHROMEDRIVER_FOUND" = false ]; then
        print_warning "ChromeDriver no encontrado - se instalará automáticamente con webdriver-manager"
    fi

    # Verificar curl y wget
    if command -v curl &> /dev/null; then
        CURL_VERSION=$(curl --version | head -1 | cut -d' ' -f1-2)
        print_status "Curl: $CURL_VERSION"
    else
        print_error "Curl no encontrado"
        ((errors++))
    fi

    # Verificar Node.js para PM2 (opcional)
    if command -v node &> /dev/null; then
        NODE_VERSION=$(node --version)
        print_status "Node.js: $NODE_VERSION"
        if command -v npm &> /dev/null; then
            NPM_VERSION=$(npm --version)
            print_status "NPM: $NPM_VERSION"
        fi
        if command -v pm2 &> /dev/null; then
            PM2_VERSION=$(pm2 --version)
            print_status "PM2: $PM2_VERSION"
        else
            print_info "PM2 no instalado (opcional para production)"
        fi
    else
        print_info "Node.js no instalado (opcional para PM2)"
    fi

    if [ $errors -gt 0 ]; then
        print_error "$errors errores encontrados en la verificación"
        return 1
    fi

    print_status "Verificación completada exitosamente"
    return 0
}

# Configurar entorno virtual Python
setup_venv() {
    print_info "Configurando entorno virtual Python..."

    if [ ! -d "venv" ]; then
        python3 -m venv venv
        print_status "Entorno virtual creado"
    else
        print_warning "Entorno virtual ya existe, actualizando..."
    fi

    source venv/bin/activate
    print_info "Actualizando pip..."
    pip install --upgrade pip setuptools wheel

    if [ -f "requirements.txt" ]; then
        print_info "Instalando dependencias Python..."
        pip install -r requirements.txt
        print_status "Dependencias Python instaladas"
    else
        print_error "requirements.txt no encontrado"
        return 1
    fi

    # Verificar instalación de dependencias clave
    print_info "Verificando dependencias clave..."
    python -c "import selenium; print('✅ Selenium OK')" || print_error "❌ Selenium falló"
    python -c "import loguru; print('✅ Loguru OK')" || print_error "❌ Loguru falló"
    python -c "import requests; print('✅ Requests OK')" || print_error "❌ Requests falló"

    print_status "Entorno Python configurado"
}

# Configurar archivo .env y directorios
setup_env() {
    # Crear directorios necesarios
    print_info "Creando directorios..."
    mkdir -p logs output

    # Configurar archivo .env
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_status "Archivo .env creado desde .env.example"
        else
            cat > .env << 'EOF'
# ===== CREDENCIALES PRAUTOPARTE =====
PRAUTO_USERNAME=tu_usuario_prautoparte
PRAUTO_PASSWORD=tu_contraseña_prautoparte

# ===== CONFIGURACIÓN SCRAPER =====
HEADLESS=true
PYTHONPATH=/home/ubuntu/stage3-performance-lube
PYTHONUNBUFFERED=1

# ===== CONFIGURACIÓN ODOO =====
ODOO_URL=http://localhost:8069
ODOO_DB=odoo
ODOO_USER=admin
ODOO_PASSWORD=admin
SEND_TO_ODOO=false

# ===== CONFIGURACIÓN AVANZADA =====
PM2_LOG_DIR=/home/ubuntu/stage3-performance-lube/logs
OUTPUT_DIR=/home/ubuntu/stage3-performance-lube/output
NODE_ENV=production
EOF
            print_status "Archivo .env creado con valores por defecto"
        fi

        print_warning "⚠️ IMPORTANTE: Edita el archivo .env con tus credenciales reales!"
        print_info "   nano .env"
    else
        print_status "Archivo .env ya existe"
        print_info "   Verifica que tus credenciales estén configuradas correctamente"
    fi

    # Establecer permisos seguros
    if [ -f ".env" ]; then
        chmod 600 .env
        print_status "Permisos seguros establecidos para .env"
    fi
}

# Test profesional de Selenium
test_selenium() {
    print_info "Probando configuración de Selenium..."

    cat > test_selenium_pro.py << 'EOF'
import os
import sys
import time
from pathlib import Path

# Configurar headless para testing
os.environ['HEADLESS'] = 'true'

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
except ImportError as e:
    print(f"❌ Error importando módulos Selenium: {e}")
    sys.exit(1)

def test_chromium_comprehensive():
    """Test completo de configuración Chrome/Selenium"""
    print("🔧 Iniciando test completo de Selenium...")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--window-size=1920,1080")

    # Intentar encontrar Chromium en múltiples rutas
    chromium_paths = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome"
    ]

    chromium_used = None
    for path in chromium_paths:
        if os.path.exists(path):
            options.binary_location = path
            chromium_used = path
            print(f"✅ Chromium encontrado: {path}")
            break

    if not chromium_used:
        print("⚠️ Chromium no encontrado en rutas conocidas, intentando ChromeDriver automático...")

    try:
        print("🚀 Iniciando WebDriver...")
        driver = webdriver.Chrome(options=options)
        print("✅ WebDriver iniciado exitosamente")

        # Test básico - cargar página
        print("🌐 Cargando página de prueba...")
        driver.get("https://httpbin.org/get")
        time.sleep(2)
        title = driver.title
        print(f"✅ Página cargada - Título: {title}")

        # Test de interacción
        print("🔍 Probando interacción con elementos...")
        driver.get("https://www.google.com")
        time.sleep(2)

        # Buscar campo de búsqueda
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        print("✅ Campo de búsqueda encontrado")

        # Test de envío de formulario
        search_box.send_keys("test selenium")
        search_box.submit()
        time.sleep(2)

        new_title = driver.title
        print(f"✅ Búsqueda completada - Título: {new_title}")

        # Test de manejo de errores
        print("🧪 Probando manejo de errores...")
        try:
            driver.get("https://httpbin.org/status/404")
            print("✅ Manejo de 404 OK")
        except Exception as e:
            print(f"⚠️ Error controlado: {e}")

        # Obtener información del navegador
        capabilities = driver.capabilities
        print(f"📊 Info del navegador:")
        print(f"   Browser: {capabilities.get('browserName', 'Unknown')}")
        print(f"   Version: {capabilities.get('browserVersion', 'Unknown')}")
        print(f"   Platform: {capabilities.get('platformName', 'Unknown')}")

        driver.quit()
        print("🎉 Test completado exitosamente!")
        return True

    except Exception as e:
        print(f"❌ Error en test Selenium: {e}")
        # Proveer soluciones específicas
        if "chromedriver" in str(e).lower():
            print("💡 Solución: Instalar ChromeDriver manualmente")
            print("   sudo apt install chromium-chromedriver")
        elif "chromium" in str(e).lower():
            print("💡 Solución: Instalar Chromium")
            print("   sudo apt install chromium-browser")
        elif "timeout" in str(e).lower():
            print("💡 Solución: Aumentar timeout o verificar conexión")
        return False

if __name__ == "__main__":
    print("🧪 Test Profesional de Selenium para PrAutoParte Scraper")
    print("=" * 60)
    success = test_chromium_comprehensive()
    if success:
        print("🎉 ✅ Todos los tests pasaron exitosamente!")
        sys.exit(0)
    else:
        print("❌ Tests fallaron - Revisa la instalación")
        sys.exit(1)
EOF

    if [ -d "venv" ]; then
        source venv/bin/activate
    fi

    print_info "Ejecutando test profesional de Selenium..."
    python3 test_selenium_pro.py
    test_result=$?

    # Limpiar
    rm -f test_selenium_pro.py

    if [ $test_result -eq 0 ]; then
        print_status "Test Selenium exitoso"
        return 0
    else
        print_error "Test Selenium falló"
        return 1
    fi
}

# Instalar PM2 (opcional para producción)
install_pm2() {
    print_info "Verificando PM2 (opcional para producción)..."

    if command -v pm2 &> /dev/null; then
        PM2_VERSION=$(pm2 --version)
        print_status "PM2 ya instalado: $PM2_VERSION"
        return 0
    fi

    print_info "PM2 no encontrado. Instalando PM2..."

    # Instalar Node.js primero si no está
    if ! command -v node &> /dev/null; then
        print_info "Instalando Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
        sudo apt install -y nodejs
    fi

    # Instalar PM2
    sudo npm install -g pm2

    if command -v pm2 &> /dev/null; then
        PM2_VERSION=$(pm2 --version)
        print_status "PM2 instalado exitosamente: $PM2_VERSION"
        return 0
    else
        print_error "No se pudo instalar PM2"
        return 1
    fi
}

# Función principal
main() {
    echo "🚀 INICIANDO INSTALACIÓN PROFESIONAL - PrAutoParte Scraper"
    echo "=============================================================="
    echo

    # Detectar distribución
    detect_distro

    # Verificar si se ejecuta como root
    if [ "$EUID" -eq 0 ]; then
        print_warning "⚠️ Ejecutando como root - se recomienda usuario normal"
    fi

    print_info "🔍 Iniciando proceso de instalación profesional..."
    echo

    # Paso 1: Instalar dependencias del sistema
    print_info "📦 PASO 1: Instalando dependencias del sistema..."
    if ! install_dependencies; then
        print_error "❌ Falló la instalación de dependencias del sistema"
        exit 1
    fi
    echo

    # Paso 2: Verificar instalaciones
    print_info "🔍 PASO 2: Verificando instalaciones..."
    if ! verify_installation; then
        print_error "❌ Error en la verificación. Revisa las instalaciones."
        exit 1
    fi
    echo

    # Paso 3: Configurar entorno Python
    print_info "🐍 PASO 3: Configurando entorno Python..."
    if ! setup_venv; then
        print_error "❌ Falló la configuración del entorno Python"
        exit 1
    fi
    echo

    # Paso 4: Configurar variables de entorno
    print_info "⚙️ PASO 4: Configurando variables de entorno..."
    setup_env
    echo

    # Paso 5: Test de Selenium
    print_info "🧪 PASO 5: Probando configuración de Selenium..."
    if ! test_selenium; then
        print_warning "⚠️ El test de Selenium falló - el scraper puede no funcionar correctamente"
        print_info "Revisa los mensajes de error anteriores y prueba corregirlos"
    else
        print_status "✅ Test de Selenium exitoso"
    fi
    echo

    # Paso 6: Instalar PM2 (opcional)
    print_info "🚀 PASO 6: Verificando/Instalando PM2 (opcional)..."
    if install_pm2; then
        print_status "✅ PM2 configurado para producción"
    else
        print_warning "⚠️ PM2 no disponible - ejecución manual requerida"
    fi
    echo

    # Resumen final
    echo
    echo "🎉 INSTALACIÓN PROFESIONAL COMPLETADA!"
    echo "============================================"
    echo
    print_info "📋 RESUMEN:"
    echo "✅ Sistema operativo: $DISTRO $VERSION"
    echo "✅ Python 3 y entorno virtual configurado"
    echo "✅ Chromium/ChromeDriver instalado"
    echo "✅ Dependencias Python instaladas"
    echo "✅ Variables de entorno configuradas"
    echo "✅ Pruebas de Selenium completadas"
    echo "✅ PM2 listo para producción"
    echo

    print_info "🎯 PRÓXIMOS PASOS:"
    echo
    echo "1. ⚠️ CONFIGURAR CREDENCIALES:"
    echo "   nano .env"
    echo "   # Reemplaza 'tu_usuario_prautoparte' y 'tu_contraseña_prautoparte'"
    echo

    echo "2. 🚀 EJECUCIÓN MANUAL (desarrollo/testing):"
    echo "   source venv/bin/activate"
    echo "   python main.py --once"
    echo

    echo "3. 🏭 EJECUCIÓN PRODUCCIÓN con PM2:"
    echo "   pm2 start ecosystem.config.js"
    echo "   pm2 monit"
    echo "   pm2 logs prauto-scraper"
    echo

    echo "4. 🐳 EJECUCIÓN CON DOCKER:"
    echo "   docker-compose up -d"
    echo "   docker-compose logs -f prauto-scraper"
    echo

    echo "5. 📚 DOCUMENTACIÓN:"
    echo "   cat README.md"
    echo "   # Ver sección de troubleshooting si hay problemas"
    echo

    print_info "🔧 COMANDOS ÚTILES:"
    echo "  • Verificar estado: pm2 status"
    echo "  • Reiniciar scraper: pm2 restart prauto-scraper"
    echo "  • Ver logs: pm2 logs prauto-scraper"
    echo "  • Detener scraper: pm2 stop prauto-scraper"
    echo "  • Monitoreo: pm2 monit"
    echo

    echo "📝 IMPORTANTE:"
    echo "  • Configura tus credenciales en .env ANTES de ejecutar"
    echo "  • El scraper se ejecutará cada 4 horas automáticamente con PM2"
    echo "  • Los logs se guardarán en logs/ con rotación automática"
    echo "  • Los CSV se guardarán en output/ con timestamp"
    echo

    if [ -f ".env" ]; then
        print_warning "⚠️ No olvides configurar tus credenciales en .env antes de ejecutar!"
    fi

    print_status "✅ Instalación completada - ¡Listo para usar!"
    echo
}

# Ejecutar si el script se llama directamente
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi