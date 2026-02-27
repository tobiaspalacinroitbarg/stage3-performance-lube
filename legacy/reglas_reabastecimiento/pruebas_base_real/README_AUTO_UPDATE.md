# Configuración Automática de Replenishment Update

Este servicio ejecuta automáticamente el script de actualización de orderpoints todos los domingos a las 20:00 hrs.

## Archivos Creados

1. **replenishment_update_auto.py** - Script principal con logging automático
2. **run_replenishment_update.sh** - Wrapper bash para ejecutar desde cron
3. **logs/** - Directorio donde se guardan los logs (se crea automáticamente)

## Configuración del Cron Job

### Paso 1: Dar permisos de ejecución al script

```bash
chmod +x "/Users/pablo/Documents/Saucotec/Performance - Stage 2/reglas_reabastecimiento/pruebas_base_real/run_replenishment_update.sh"
```

### Paso 2: Editar el crontab

```bash
crontab -e
```

### Paso 3: Agregar la siguiente línea

Para ejecutar todos los domingos a las 20:00:

```bash
0 20 * * 0 /Users/pablo/Documents/Saucotec/Performance\ -\ Stage\ 2/reglas_reabastecimiento/pruebas_base_real/run_replenishment_update.sh >> /Users/pablo/Documents/Saucotec/Performance\ -\ Stage\ 2/reglas_reabastecimiento/pruebas_base_real/logs/cron.log 2>&1
```

**Formato del cron:**
- `0` = minuto (0)
- `20` = hora (20:00)
- `*` = día del mes (cualquiera)
- `*` = mes (cualquiera)
- `0` = día de la semana (0 = domingo)

### Paso 4: Verificar que el cron esté configurado

```bash
crontab -l
```

## Logs

Los logs se guardan en:
- **Logs del script:** `logs/replenishment_update_YYYYMMDD_HHMMSS.log`
- **Logs del cron:** `logs/cron.log`

## Prueba Manual

Para probar el script manualmente antes de que se ejecute automáticamente:

```bash
cd "/Users/pablo/Documents/Saucotec/Performance - Stage 2/reglas_reabastecimiento/pruebas_base_real"
./run_replenishment_update.sh
```

O directamente el script de Python:

```bash
python3 replenishment_update_auto.py
```

## Diferencias con replenishment_update.py

- **Logging automático:** Guarda logs con timestamp
- **Manejo robusto de errores:** Retorna códigos de salida apropiados
- **Sin interacción:** Diseñado para ejecución desatendida
- **Mismo comportamiento:** La lógica de actualización es idéntica

## Alternativa: Usar launchd (macOS)

Si prefieres usar el sistema nativo de macOS en lugar de cron, puedo crear un archivo `.plist` para launchd.

## Monitoreo

Para verificar que el servicio se ejecutó correctamente:

1. Revisar los logs en `logs/`
2. Verificar la última ejecución: `ls -lt logs/`
3. Ver el contenido del último log: `tail -f logs/replenishment_update_*.log`

## Desactivar el Servicio

Para desactivar el cron job:

```bash
crontab -e
# Comentar o eliminar la línea agregada
```
