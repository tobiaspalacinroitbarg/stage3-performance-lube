Instrucciones para ejecutar el scheduler con pm2

1) Instalar pm2 en el servidor (si no está instalado):

```bash
npm install -g pm2
```

2) Ir al directorio del proyecto donde está `scheduler_runner.py`:

```bash
cd "/Users/pablo/Documents/Saucotec/Performance - Stage 2/reglas_reabastecimiento/pruebas_base_real"
```

3) Iniciar el scheduler con pm2 usando el ecosystem o directamente:

Usando ecosystem (recomendado):

```bash
pm2 start ecosystem.config.js
```

O directamente lanzar el script:

```bash
pm2 start scheduler_runner.py --interpreter python3 --name replenishment-scheduler
```

4) Ver estado y logs:

```bash
pm2 status
pm2 logs replenishment-scheduler --lines 200
```

5) Guardar la configuración para arrancar pm2 en el boot del servidor (opcional):

```bash
pm2 save
pm2 startup
# Ejecuta el comando que pm2 startup imprime para habilitar el servicio
```

Notas:
- El `scheduler_runner.py` calcula el próximo domingo a las 20:00 en la zona horaria del servidor, duerme hasta ese momento y ejecuta `replenishment_update_auto.py`. 
- Los logs por ejecución se guardan en `logs/scheduler_YYYYMMDD_HHMMSS.out.log` y `.err.log`.
- Asegúrate de que el entorno Python (virtualenv) tenga las dependencias instaladas (`python-dotenv`, etc.). Si usas un virtualenv, usa el comando `pm2 start ... --interpreter /path/to/venv/bin/python` para usarlo.
