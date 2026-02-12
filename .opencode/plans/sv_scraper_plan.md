# Plan: SV Portal Scraper (Servicios Viales)

## Objetivo
Crear un scraper para portal.sv.com.ar que busque productos de Odoo (proveedor "SERVICIOS VIALES DE SANTA FE S A") y extraiga precio + disponibilidad, actualizando stock en Odoo igual que el scraper de PrAutoParte.

## Diferencia clave con PrAutoParte
- PrAutoParte: scrapea TODO el catálogo (24k+ productos) y luego hace match con Odoo
- SV Portal: NO tiene catálogo completo, solo un "Cotizador de Partes" que busca código por código
- **Flujo invertido**: primero obtenemos códigos de Odoo, luego buscamos cada uno en el portal

## Arquitectura

### Archivo: `sv_scraper.py` (nuevo, archivo separado)
Importa `OdooConnector` y `CodeNormalizer` de `main.py` (igual que `bluecar_scraper_test.py`)

### Flujo de ejecución

```
1. Conectar a Odoo via XML-RPC
   └─> Obtener productos con proveedor principal "SERVICIOS VIALES DE SANTA FE S A"
   └─> Extraer sus `default_code` (ej: "7E8630")

2. Login en portal.sv.com.ar via Selenium
   └─> Navegar a /login
   └─> Fill email + password + submit
   └─> Extraer session cookie (NextAuth) o token

3. Para cada código de producto de Odoo:
   └─> Buscar en el cotizador (via API o Selenium)
   └─> Extraer: código, descripción, marca, disponibilidad (dots), precio, moneda
   └─> Mapear disponibilidad de sucursales a un valor 0/1/2

4. Actualizar Odoo
   └─> Misma lógica de stock.quant en TODO/Stock/StockSCRAP
   └─> Misma lógica inversa: disponibilidad 0 -> stock 1, disponibilidad 1/2 -> stock 0
   └─> Misma exclusión de KITs y no-storable
   └─> Batch update via OdooConnector.update_matched_products_batch()
```

### Fase de investigación pendiente (Stagehand)
Antes de implementar, necesitamos confirmar con Stagehand:
1. ¿Qué API calls hace el portal cuando buscás un código? (¿hay un endpoint REST detrás?)
2. Si hay API REST: usamos `requests.Session` con la cookie de sesión (más rápido)
3. Si no hay API: usamos Selenium para cada búsqueda (más lento pero funcional)
4. ¿Qué significan exactamente los dots de color? (rojo = sin stock? verde = con stock?)

### Decisiones de disponibilidad
Opciones a discutir:
- **Opción A**: Un producto tiene stock si CUALQUIER sucursal tiene dot verde
- **Opción B**: Un producto tiene stock solo si una sucursal ESPECÍFICA (ej: SF o BA) tiene dot verde
- **Opción C**: Usar el campo "Cantidad" de la tabla como disponibilidad

### Estructura de clases

```python
# sv_scraper.py

from main import CodeNormalizer, OdooConnector

class SVConfig:
    base_url = "https://portal.sv.com.ar"
    login_url = "https://portal.sv.com.ar/login"
    # Credenciales desde .env: SV_USERNAME, SV_PASSWORD
    # Odoo config: mismas vars de entorno
    supplier_name = "SERVICIOS VIALES DE SANTA FE S A"

class SVScraper:
    def __init__(config)
    def login_and_get_session() -> cookies/token
    def search_product(code) -> dict con datos
    def run():
        1. odoo_connector.connect()
        2. product_ids = odoo_connector._get_product_ids_by_supplier(supplier_name)
        3. codes = [obtener default_code de cada product_id]
        4. session = login_and_get_session()
        5. for code in codes: search_product(code) -> scraped_data
        6. match scraped codes con odoo codes
        7. update_matched_products_batch(products_data, cached_data)
```

### Variables de entorno nuevas
```
SV_USERNAME=administracion@performance-lube.com  # ya existe
SV_PASSWORD=martin2296                            # ya existe
SV_SUPPLIER_NAME=SERVICIOS VIALES DE SANTA FE S A  # nuevo, optional
```

### Estimación de tiempo de ejecución
- Si hay ~200-500 productos del proveedor y la búsqueda tarda ~2s cada una
- Total: ~7-17 minutos por ejecución
- Con API REST directa: ~2-5 minutos

## Pasos de implementación

1. [ ] Ejecutar script Stagehand para descubrir API del buscador
2. [ ] Confirmar lógica de disponibilidad con el usuario
3. [ ] Crear `sv_scraper.py` con estructura base
4. [ ] Implementar login (Selenium o requests según hallazgo)
5. [ ] Implementar búsqueda por código
6. [ ] Implementar extracción de datos
7. [ ] Conectar con OdooConnector para actualización batch
8. [ ] Testing con producto de ejemplo (7E8630)
9. [ ] Agregar al ecosystem.config.js (PM2)
