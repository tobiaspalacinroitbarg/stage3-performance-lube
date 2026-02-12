"""
Test del SV scraper con 50 productos reales, sin dry-run.
Muestra detalle de cada producto para verificar en Odoo.
"""
import os
import time
import json
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from sv_scraper import SVConfig, SVScraper

# Limit to 50 products
LIMIT = 50

def main():
    config = SVConfig()
    scraper = SVScraper(config)

    logger.info("=" * 60)
    logger.info(f"TEST SV SCRAPER - {LIMIT} PRODUCTOS (SIN DRY-RUN)")
    logger.info("=" * 60)

    # --- Paso 1: Obtener códigos de Odoo ---
    logger.info("\n📋 FASE 1: Obteniendo códigos de Odoo...")
    product_codes = scraper.get_odoo_product_codes()
    if not product_codes:
        logger.error("❌ No se obtuvieron códigos. Abortando.")
        return

    # Tomar solo los primeros LIMIT
    codes_subset = dict(list(product_codes.items())[:LIMIT])
    logger.info(f"📊 Usando {len(codes_subset)} de {len(product_codes)} códigos")

    # --- Paso 2: Login ---
    logger.info("\n🔐 FASE 2: Login en portal.sv.com.ar...")
    if not scraper.login_and_get_session():
        logger.error("❌ Login falló.")
        return

    # --- Paso 3: Buscar cada código ---
    logger.info(f"\n🔍 FASE 3: Buscando {len(codes_subset)} códigos...")

    scraped_results = {}
    detail_log = []  # Para reporte detallado

    for idx, (code, info) in enumerate(codes_subset.items(), 1):
        results = scraper.search_product(code)

        if results:
            exact_match = None
            for product in results:
                if product.get("codigo", "").strip().upper() == code.strip().upper():
                    exact_match = product
                    break

            if exact_match:
                has_stock = scraper._has_any_stock(exact_match)
                disponibilidad = 1 if has_stock else 0
                # Lógica inversa para Odoo: disp=0 -> stock=1, disp=1 -> stock=0
                odoo_stock = 1 if disponibilidad == 0 else 0

                scraped_results[code] = {
                    'disponibilidad': disponibilidad,
                    'precioCosto': exact_match.get('precioUnitario', 0),
                    'moneda': exact_match.get('moneda', 'U$S'),
                    'descripcion': exact_match.get('descripcion', ''),
                    'marca': exact_match.get('marcaPortal', ''),
                    'descuento': exact_match.get('descuento', 0),
                    'disponibleSF': exact_match.get('disponibleSF', 0),
                    'disponibleBA': exact_match.get('disponibleBA', 0),
                    'disponibleMDZ': exact_match.get('disponibleMDZ', 0),
                    'disponibleSA': exact_match.get('disponibleSA', 0),
                    'disponibleOTROS': exact_match.get('disponibleOTROS', 0),
                }

                detail_log.append({
                    'code': code,
                    'product_id': info['product_id'],
                    'template_id': info['template_id'],
                    'sv_disponibilidad': disponibilidad,
                    'odoo_stock_quant': odoo_stock,
                    'sf': exact_match.get('disponibleSF', 0),
                    'ba': exact_match.get('disponibleBA', 0),
                    'mdz': exact_match.get('disponibleMDZ', 0),
                    'sa': exact_match.get('disponibleSA', 0),
                    'otr': exact_match.get('disponibleOTROS', 0),
                    'precio': exact_match.get('precioUnitario', 0),
                    'moneda': exact_match.get('moneda', ''),
                    'status': 'FOUND',
                })

                logger.info(f"  [{idx}/{LIMIT}] {code}: "
                           f"SV={'CON STOCK' if has_stock else 'SIN STOCK'} "
                           f"→ Odoo quant={odoo_stock} | "
                           f"SF={exact_match.get('disponibleSF',0)} "
                           f"BA={exact_match.get('disponibleBA',0)} "
                           f"MDZ={exact_match.get('disponibleMDZ',0)} "
                           f"SA={exact_match.get('disponibleSA',0)} "
                           f"OTR={exact_match.get('disponibleOTROS',0)} | "
                           f"${exact_match.get('precioUnitario',0)} {exact_match.get('moneda','')}")
            else:
                detail_log.append({'code': code, 'status': 'NO_EXACT_MATCH'})
                logger.info(f"  [{idx}/{LIMIT}] {code}: NO EXACT MATCH (got {len(results)} results)")
        else:
            detail_log.append({'code': code, 'status': 'NOT_FOUND'})
            logger.info(f"  [{idx}/{LIMIT}] {code}: NOT FOUND in SV portal")

        time.sleep(0.5)

    # --- Resumen pre-update ---
    found = [d for d in detail_log if d['status'] == 'FOUND']
    not_found = [d for d in detail_log if d['status'] != 'FOUND']
    with_stock = [d for d in found if d['sv_disponibilidad'] == 1]
    without_stock = [d for d in found if d['sv_disponibilidad'] == 0]

    logger.info(f"\n📊 RESUMEN PRE-UPDATE:")
    logger.info(f"   Total buscados: {LIMIT}")
    logger.info(f"   Encontrados: {len(found)}")
    logger.info(f"   No encontrados: {len(not_found)}")
    logger.info(f"   Con stock en SV (→ quant=0 en Odoo): {len(with_stock)}")
    logger.info(f"   Sin stock en SV (→ quant=1 en Odoo): {len(without_stock)}")

    if not scraped_results:
        logger.warning("⚠️ Nada para actualizar.")
        return

    # --- Paso 4: Preparar cached data y actualizar Odoo ---
    logger.info(f"\n📦 FASE 4: Actualizando {len(scraped_results)} productos en Odoo (REAL)...")

    cached_data = scraper.prepare_odoo_cached_data(codes_subset)
    if not cached_data:
        logger.error("❌ No se pudieron preparar datos de Odoo.")
        return

    products_data = []
    for code, data in scraped_results.items():
        if code in cached_data['product_info']:
            products_data.append((code, data))

    logger.info(f"📊 Productos para batch update: {len(products_data)}")

    # EJECUTAR SIN DRY-RUN
    results = scraper.odoo_connector.update_matched_products_batch(products_data, cached_data)

    # --- Resumen final ---
    logger.info("\n" + "=" * 60)
    logger.info("RESULTADO DEL TEST")
    logger.info("=" * 60)

    if results.get("stock"):
        stock = results["stock"]
        logger.info(f"   Stock updated: {len(stock.get('updated', []))}")
        logger.info(f"   Stock created: {len(stock.get('created', []))}")
        logger.info(f"   Kits skipped: {len(stock.get('kits_skipped', []))}")
        logger.info(f"   Non-storable skipped: {len(stock.get('non_storable_skipped', []))}")
        logger.info(f"   Errors: {len(stock.get('errors', []))}")

        if stock.get('errors'):
            for err in stock['errors'][:10]:
                logger.warning(f"   ERROR: {err}")

    # Guardar detalle para verificación
    logger.info("\n📋 DETALLE PARA VERIFICAR EN ODOO:")
    logger.info(f"{'CODE':<15} {'PROD_ID':<10} {'SV_DISP':<10} {'ODOO_QTY':<10} {'PRECIO':<12} {'STATUS'}")
    logger.info("-" * 75)
    for d in detail_log:
        if d['status'] == 'FOUND':
            logger.info(f"{d['code']:<15} {d['product_id']:<10} "
                       f"{'CON STOCK' if d['sv_disponibilidad'] else 'SIN STOCK':<10} "
                       f"{d['odoo_stock_quant']:<10} "
                       f"${d['precio']:<11} {d['status']}")
        else:
            logger.info(f"{d['code']:<15} {'':10} {'':10} {'':10} {'':12} {d['status']}")

    logger.info(f"\n✅ Verificá en Odoo (Inventario > Informes > Inventario) "
               f"filtrando por ubicación TODO/Stock/StockSCRAP "
               f"y buscando estos códigos.")


if __name__ == "__main__":
    main()
