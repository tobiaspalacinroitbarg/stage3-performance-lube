#!/bin/bash

echo "🐧 CONFIGURACIÓN PARA LINUX - PrAutoParte Scraper"
echo "=================================================="

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
            sudo apt update
            sudo apt install -y \
                python3 \
                python3-pip \
                python3-venv \
                chromium-browser \
                chromium-chromedriver \
                xvfb \
                curl \
                wget
            ;;
        "centos"|"rhel"|"fedora")
            print_info "Instalando dependencias con yum/dnf..."
            if command -v dnf &> /dev/null; then
                sudo dnf install -y \
                    python3 \
                    python3-pip \
                    chromium \
                    chromedriver \
                    xorg-x11-server-Xvfb
            else
                sudo yum install -y \
                    python3 \
                    python3-pip \
                    chromium \
                    chromedriver \
                    xorg-x11-server-Xvfb
            fi
            ;;
        "arch"|"manjaro")
            print_info "Instalando dependencias con pacman..."
            sudo pacman -S --noconfirm \
                python \
                python-pip \
                chromium \
                chromedriver \
                xorg-server-xvfb
            ;;
        *)
            print_warning "Distribución no reconocida. Instalación manual requerida."
            print_info "Instalar manualmente: python3, python3-pip, chromium-browser, chromium-chromedriver"
            ;;
    esac
}

# Verificar instalaciones
verify_installation() {
    print_info "Verificando instalaciones..."
    
    # Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version)
        print_status "Python: $PYTHON_VERSION"
    else
        print_error "Python3 no encontrado"
        return 1
    fi
    
    # Pip
    if command -v pip3 &> /dev/null; then
        PIP_VERSION=$(pip3 --version)
        print_status "Pip: $PIP_VERSION"
    else
        print_error "Pip3 no encontrado"
        return 1
    fi
    
    # Chromium
    CHROMIUM_PATHS=("/usr/bin/chromium-browser" "/usr/bin/chromium" "/snap/bin/chromium")
    CHROMIUM_FOUND=false
    
    for path in "${CHROMIUM_PATHS[@]}"; do
        if [ -f "$path" ]; then
            CHROMIUM_VERSION=$($path --version 2>/dev/null || echo "Versión no disponible")
            print_status "Chromium: $CHROMIUM_VERSION (en $path)"
            CHROMIUM_FOUND=true
            break
        fi
    done
    
    if [ "$CHROMIUM_FOUND" = false ]; then
        print_error "Chromium no encontrado en rutas conocidas"
        return 1
    fi
    
    # ChromeDriver
    if command -v chromedriver &> /dev/null; then
        CHROMEDRIVER_VERSION=$(chromedriver --version)
        print_status "ChromeDriver: $CHROMEDRIVER_VERSION"
    else
        print_warning "ChromeDriver no encontrado en PATH"
        # Verificar en ubicaciones comunes
        if [ -f "/usr/bin/chromedriver" ]; then
            print_status "ChromeDriver encontrado en /usr/bin/chromedriver"
        else
            print_error "ChromeDriver no encontrado"
            return 1
        fi
    fi
}

# Configurar entorno virtual
setup_venv() {
    print_info "Configurando entorno virtual..."
    
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        print_status "Entorno virtual creado"
    else
        print_warning "Entorno virtual ya existe"
    fi
    
    source venv/bin/activate
    pip install --upgrade pip
    
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        print_status "Dependencias Python instaladas"
    else
        print_warning "requirements.txt no encontrado"
    fi
}

# Configurar archivo .env
setup_env() {
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_status "Archivo .env creado desde .env.example"
            print_warning "EDITA .env con tus credenciales antes de ejecutar!"
        else
            cat > .env << EOF
PRAUTO_USERNAME=tu-usuario
PRAUTO_PASSWORD=tu-password
HEADLESS=true
EOF
            print_status "Archivo .env creado"
            print_warning "EDITA .env con tus credenciales antes de ejecutar!"
        fi
    else
        print_status "Archivo .env ya existe"
    fi
}

# Test básico
test_selenium() {
    print_info "Probando configuración de Selenium..."
    
    cat > test_selenium.py << 'EOF'
import os
os.environ['HEADLESS'] = 'true'

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def test_chromium():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    # Intentar encontrar Chromium
    chromium_paths = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium"
    ]
    
    for path in chromium_paths:
        if os.path.exists(path):
            options.binary_location = path
            print(f"Usando: {path}")
            break
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.get("https://www.google.com")
        title = driver.title
        driver.quit()
        print(f"✅ Test exitoso! Título: {title}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_chromium()
EOF

    if [ -d "venv" ]; then
        source venv/bin/activate
    fi
    
    python3 test_selenium.py
    rm test_selenium.py
}

# Función principal
main() {
    detect_distro
    
    print_info "Iniciando instalación..."
    
    # Instalar dependencias del sistema
    install_dependencies
    
    # Verificar instalaciones
    if ! verify_installation; then
        print_error "Error en la verificación. Revisa las instalaciones."
        exit 1
    fi
    
    # Configurar entorno Python
    setup_venv
    
    # Configurar variables de entorno
    setup_env
    
    # Test de Selenium
    test_selenium
    
    echo
    print_status "🎉 INSTALACIÓN COMPLETADA!"
    echo
    print_info "Pasos siguientes:"
    echo "1. Edita el archivo .env con tus credenciales"
    echo "2. Activa el entorno virtual: source venv/bin/activate"
    echo "3. Ejecuta el scraper: python main.py"
    echo
    print_info "O usa Docker:"
    echo "docker-compose run --rm prauto-scraper"
}

# Ejecutar si el script se llama directamente
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi