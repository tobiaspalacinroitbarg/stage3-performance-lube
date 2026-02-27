# =============================================================================
# Dockerfile base para Scrapers (PR, SV, Bluecar)
# =============================================================================
FROM python:3.11-slim

# Instalar dependencias del sistema (Chrome para PR y Bluecar + gcc para psutil)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    xvfb \
    chromium \
    chromium-driver \
    cron \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Crear enlaces simbólicos para compatibilidad
RUN ln -sf /usr/bin/chromium /usr/bin/google-chrome \
    && ln -sf /usr/bin/chromedriver /usr/local/bin/chromedriver

# Crear directorio de trabajo
WORKDIR /app

# Copiar requirements e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY main.py .
COPY sv_scraper_v2.py .
COPY csv_manager.py .

# Crear directorios necesarios
RUN mkdir -p logs output

# Variables de entorno
ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

# El comando se define en docker-compose para cada servicio
CMD ["python", "--version"]
