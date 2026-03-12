# Performance Lube - Sistema de Scrapers Multi-Proveedor

Sistema automatizado de sincronización de stock desde múltiples proveedores a Odoo.

---

## 📋 Tabla de Contenidos

- [Scrapers Disponibles](#-scrapers-disponibles)
- [Configuración Inicial](#%EF%B8%8F-configuración-inicial)
- [Uso - Ejecución Manual vs Automática](#-uso---ejecución-manual-vs-automática)
- [Configuración del Schedule (Cron)](#-configuración-del-schedule-cron)
- [Logs y Monitoreo](#-logs-y-monitoreo)
- [Troubleshooting](#-troubleshooting)
- [Estructura del Proyecto](#-estructura-del-proyecto)

---

## 🤖 Scrapers Disponibles

| Scraper | Proveedor | Ubicación Odoo | Frecuencia Recomendada | Tiempo Estimado |
|---------|-----------|----------------|------------------------|-----------------|
| **PR Scraper** | PR Autopartes | `TODO/Stock/PR - Scraping` | Cada 4 horas | 20-30 min |
| **SV Scraper** | Servicios Viales | `TODO/Stock/SV - Scraping` | Cada 4 horas | 60-90 min |
| **Replenishment Min/Max** | N/A | Todas las reglas | 1 vez al día | 5-10 min |

---

## ⚙️ Configuración Inicial

### 1. Clonar el repositorio

```bash
cd ~
git clone <repo-url> stage3-performance-lube
cd stage3-performance-lube
```

### 2. Crear archivo `.env`

```bash
cp .env.example .env
nano .env
```

### 3. Configurar variables obligatorias

```bash
# ===== ODOO (OBLIGATORIO) =====
ODOO_URL=https://pldistribucion.adhoc.ar
ODOO_DB=odoo
ODOO_USER=tu_email@ejemplo.com
ODOO_PASSWORD=tu_password

# ===== PR AUTOPARTES (OBLIGATORIO) =====
PRAUTO_USERNAME=tu_usuario
PRAUTO_PASSWORD=tu_password

# ===== SERVICIOS VIALES (OBLIGATORIO) =====
SV_USERNAME=tu_email@ejemplo.com
SV_PASSWORD=tu_password

# ===== PERFORMANCE =====
SV_MAX_WORKERS=5
SV_REQUEST_DELAY=0.1

# ===== SCHEDULE (UTC timezone) =====
# Para 3 AM Argentina (-03), usar 6 AM UTC
SCRAPERS_SCHEDULE=0 6 * * *
```

**⚠️ IMPORTANTE:** El schedule usa **UTC timezone**, no hora Argentina.

---

## 🚀 Uso - Ejecución Manual vs Automática

### 📌 Opción A: Ejecución Manual (Una Vez)

**Cuándo usar:**
- Primera vez que lo instalás
- Para testing y debugging
- Cuando necesitás ejecutar inmediatamente

**Comando:**

```bash
cd ~/stage3-performance-lube
docker-compose build --no-cache
docker-compose up scrapers
```

**Características:**
- ✅ Ejecuta los 3 scrapers en serie: PR → (60s) → SV → (60s) → MinMax
- ✅ Ves el output en tiempo real en tu terminal
- ✅ Termina automáticamente cuando finaliza
- ✅ Ideal para debugging y verificar que todo funciona

**Flujo de ejecución:**

```
[1/3] PR Scraper - PR Autopartes
----------------------------------------
... (20-30 minutos)

Esperando 60s antes del siguiente...

[2/3] SV Scraper - Servicios Viales
----------------------------------------
... (60-90 minutos)

Esperando 60s antes del siguiente...

[3/3] Replenishment Min/Max
----------------------------------------
... (5-10 minutos)

========================================
COMPLETADO - Todos los scrapers OK
========================================
```

---

### 📌 Opción B: Ejecución Automática (Cron en Background)

**Cuándo usar:**
- En producción
- Cuando querés que corra automáticamente todos los días
- Para dejar el servidor funcionando sin supervisión

**Comando:**

```bash
cd ~/stage3-performance-lube
docker-compose build --no-cache
docker-compose up scrapers-cron -d
```

**Características:**
- ✅ Corre en **background** (detached mode con `-d`)
- ✅ Se ejecuta según el **schedule configurado** (ver sección siguiente)
- ✅ Reinicia automáticamente si el container falla (`restart: unless-stopped`)
- ✅ Logs van a archivo: `logs/scrapers.log`
- ✅ Queda funcionando 24/7

---

### 🔄 Comandos Útiles para el Cron

```bash
# Ver si está corriendo
docker ps | grep scrapers-cron

# Ver logs en tiempo real
docker-compose logs -f scrapers-cron

# Ver últimas 100 líneas
docker-compose logs scrapers-cron --tail 100

# Ver logs del archivo
tail -f logs/scrapers.log

# Detener
docker-compose stop scrapers-cron

# Reiniciar
docker-compose restart scrapers-cron

# Eliminar y recrear (si cambiaste .env)
docker-compose stop scrapers-cron
docker-compose rm -f scrapers-cron
docker-compose up scrapers-cron -d
```

---

## 📅 Configuración del Schedule (Cron)

### ⚠️ IMPORTANTE: Timezone UTC

**El cron usa UTC**, NO hora Argentina. Tenés que hacer la conversión:

| Hora deseada (Argentina -03) | Valor en `.env` | Cron Schedule |
|-------------------------------|-----------------|---------------|
| 2:00 AM | `SCRAPERS_SCHEDULE=0 5 * * *` | 5 AM UTC |
| 3:00 AM | `SCRAPERS_SCHEDULE=0 6 * * *` | 6 AM UTC |
| 4:00 AM | `SCRAPERS_SCHEDULE=0 7 * * *` | 7 AM UTC |
| 6:00 AM | `SCRAPERS_SCHEDULE=0 9 * * *` | 9 AM UTC |

### Formato Cron

```
* * * * *
│ │ │ │ │
│ │ │ │ └─── Día de la semana (0-7, 0=Domingo)
│ │ │ └───── Mes (1-12)
│ │ └─────── Día del mes (1-31)
│ └───────── Hora (0-23, en UTC)
└─────────── Minuto (0-59)
```

### Ejemplos Comunes

```bash
# Todos los días a las 3 AM Argentina (6 AM UTC)
SCRAPERS_SCHEDULE=0 6 * * *

# Cada 4 horas
SCRAPERS_SCHEDULE=0 */4 * * *

# Lunes a viernes a las 6 AM Argentina (9 AM UTC)
SCRAPERS_SCHEDULE=0 9 * * 1-5

# Dos veces al día: 6 AM y 6 PM Argentina (9 AM y 9 PM UTC)
SCRAPERS_SCHEDULE=0 9,21 * * *
```

### Aplicar Cambios de Schedule

```bash
# 1. Editar .env
nano .env

# 2. Rebuild y reiniciar
cd ~/stage3-performance-lube
docker-compose stop scrapers-cron
docker-compose rm -f scrapers-cron
docker-compose up scrapers-cron -d

# 3. Verificar que se aplicó
docker exec scrapers-cron crontab -l
```

Debería mostrar:
```
0 6 * * * cd /app && python main.py --once && sleep 60 && python sv_scraper_v2.py && sleep 60 && python scripts/update_replenishment_minmax.py >> /app/logs/scrapers.log 2>&1
```

---

## 📊 Logs y Monitoreo

### Archivos de Log

```
logs/
├── scrapers.log         # Logs del cron automático
├── pr-scraper.log       # Logs individuales (si usás profiles)
├── sv-scraper.log
└── replenishment-minmax.log
```

### Ver Logs en Tiempo Real

```bash
# Cron automático (dentro del container)
docker-compose logs -f scrapers-cron

# Archivo de log (en el host)
tail -f logs/scrapers.log

# Últimas 100 líneas
tail -100 logs/scrapers.log
```

### Buscar Errores

```bash
# Buscar errores en logs
grep -i error logs/scrapers.log
grep "❌" logs/scrapers.log
grep "429" logs/scrapers.log  # Rate limiting

# Ver resumen de última ejecución
tail -50 logs/scrapers.log | grep "RESUMEN FINAL" -A 20
```

### Ejemplo de Output Exitoso

```
======================================================================
RESUMEN FINAL
======================================================================

    Productos en Odoo:          2867
    Productos buscados:         2867

    RESULTADOS SCRAPING:
    - Con stock (>0):           2260
    - Encontrados sin stock:    490
    - No encontrados en API:    117
    - Total unidades:           300708

    ACTUALIZACION ODOO:
    - Quants actualizados:      1849
    - Quants creados:           517
    - No-storable saltados:     0
    - Errores:                  0
```

---

## 🔧 Troubleshooting

### ❌ El cron no está ejecutando

**Síntoma:** No hay logs nuevos, el scraper no corre a la hora configurada.

**Diagnóstico:**

```bash
# 1. Verificar horario del servidor
docker exec scrapers-cron date

# 2. Verificar cron configurado
docker exec scrapers-cron crontab -l

# 3. Verificar que el daemon está corriendo
docker exec scrapers-cron ps aux | grep cron
```

**Solución común:** El schedule está en UTC, no en hora Argentina. 

- Si querés 3 AM Argentina, usá `SCRAPERS_SCHEDULE=0 6 * * *` (6 AM UTC)
- Rebuild el container después de cambiar `.env`

---

### ❌ Error "429 Too Many Requests"

**Síntoma:** Logs muestran errores de rate limiting de Odoo.

**Solución:**

1. El sistema ya tiene retry automático con backoff exponencial (10 intentos)
2. Si sigue fallando, aumentá los delays en `.env`:

```bash
SV_REQUEST_DELAY=0.2
SV_MAX_WORKERS=3
```

3. Rebuild y reiniciar:

```bash
docker-compose restart scrapers-cron
```

---

### ❌ Scraper se colgó / no termina

**Síntoma:** La ejecución lleva más de 3 horas o se quedó trabado.

**Diagnóstico:**

```bash
# Ver si está corriendo
docker ps | grep scrapers

# Ver procesos dentro del container
docker exec scrapers ps aux
```

**Solución:**

```bash
# Matar y reiniciar
docker-compose restart scrapers-cron

# Ver en qué se colgó
tail -100 logs/scrapers.log
```

**Causas comunes:**
- Timeout de Selenium esperando elemento
- Rate limiting extremo de Odoo
- Página de proveedor caída

---

### ❌ No se actualizan los quants (stock = 0)

**Síntoma:** Logs muestran `"No-storable saltados: 2867"`.

**Causa:** Productos marcados como no almacenables en Odoo.

**Verificar:**

```bash
grep "No-storable saltados" logs/scrapers.log
```

**Solución:** En Odoo, verificar que los productos tengan:
- Tipo de Producto = `Producto Almacenable`
- Campo `is_storable` = `True`

---

### ❌ Códigos no matchean (SV Scraper)

**Síntoma:** Logs muestran `"No match para CÓDIGO. Resultados: [...]"`

**Casos implementados:**

1. **Match sin espacios:** `SA17483` = `SA 17483`
2. **Match con sufijo "i":** `TR1145` = `TR1145i` (solo códigos que empiezan con T)

**Ver en logs:**

```bash
grep "No match para" logs/scrapers.log
```

---

## 📁 Estructura del Proyecto

```
stage3-performance-lube/
├── main.py                    # PR Scraper
├── sv_scraper_v2.py          # SV Scraper
├── scripts/
│   ├── update_replenishment_minmax.py
│   └── create_replenishment_rules.py
├── odoo_code/                # Automatizaciones para Odoo
│   ├── v2_automatizacion_consumo_stock_scraper.py
│   ├── v2_generacion_orden_compra_venta_scraper.py
│   └── v2_correo_notificacion_venta_scraper.py
├── docker-compose.yml
├── Dockerfile
├── .env                      # Configuración (crear desde .env.example)
├── .env.example
├── logs/                     # Logs de ejecución
├── output/                   # Archivos CSV generados
└── README.md                 # Esta documentación
```

---

## 🔐 Seguridad

- ⚠️ **Nunca commitear el archivo `.env`** - contiene credenciales
- El `.env` está en `.gitignore`
- Usar credenciales específicas con permisos mínimos en Odoo
- Los scrapers solo tienen acceso a ubicaciones de scraping

---

## 🔄 Actualizaciones

Para actualizar el código en producción:

```bash
# 1. Pull cambios
cd ~/stage3-performance-lube
git pull

# 2. Rebuild containers
docker-compose build --no-cache

# 3. Reiniciar servicio
docker-compose restart scrapers-cron

# 4. Verificar que funcionó
docker-compose logs scrapers-cron --tail 50
```

---

## 🛠️ Servicios Individuales (Debug)

Para correr scrapers individuales (útil para debugging):

```bash
# Solo PR Scraper
docker-compose --profile individual run --rm pr-scraper

# Solo SV Scraper
docker-compose --profile individual run --rm sv-scraper

# Solo Replenishment
docker-compose --profile individual run --rm replenishment-minmax
```

---

## 📞 Soporte

Para problemas o preguntas:

1. Revisar esta documentación
2. Revisar logs en `logs/scrapers.log`
3. Revisar sección [Troubleshooting](#-troubleshooting)

---

**Última actualización:** Marzo 2026
