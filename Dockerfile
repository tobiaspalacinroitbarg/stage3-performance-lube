FROM python:3.11-slim

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    xvfb \
    chromium-browser \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Crear enlaces simbólicos para compatibilidad
RUN ln -s /usr/bin/chromium-browser /usr/bin/google-chrome \
    && ln -s /usr/bin/chromedriver /usr/local/bin/chromedriver

# Crear directorio de trabajo
WORKDIR /app

# Copiar archivos de requirements
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY . .

# Crear directorios necesarios
RUN mkdir -p logs output

# Variables de entorno para Linux
ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

# Comando por defecto
CMD ["python", "main.py"]