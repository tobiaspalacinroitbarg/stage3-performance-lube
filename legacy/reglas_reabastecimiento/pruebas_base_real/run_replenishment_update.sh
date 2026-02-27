#!/bin/bash
# Script wrapper para ejecutar replenishment_update_auto.py
# Este script se usa desde cron para asegurar el entorno correcto

# Cambiar al directorio del script
cd "$(dirname "$0")"

# Activar el entorno virtual de Python si existe
# Ajusta esta ruta según tu configuración
if [ -d "../../venv" ]; then
    source ../../venv/bin/activate
elif [ -d "../venv" ]; then
    source ../venv/bin/activate
fi

# Ejecutar el script de Python
python3 replenishment_update_auto.py

# Guardar el código de salida
exit_code=$?

# Opcional: enviar notificación si falló
if [ $exit_code -ne 0 ]; then
    echo "Error en replenishment_update_auto.py - Exit code: $exit_code"
fi

exit $exit_code
