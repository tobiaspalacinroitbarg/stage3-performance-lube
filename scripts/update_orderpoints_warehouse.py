"""
Script para actualizar warehouse_id de orderpoints existentes.

Busca todos los orderpoints con location_id = 22 y les actualiza warehouse_id = 13.

Uso:
  python scripts/update_orderpoints_warehouse.py           # Ejecutar
  python scripts/update_orderpoints_warehouse.py --dry-run # Preview sin cambios
"""
import os
import sys
import argparse
from dotenv import load_dotenv
import xmlrpc.client

# ============================================================
# CONFIGURACIÓN HARDCODEADA
# ============================================================
FILTER_LOCATION_ID = 22   # Filtrar orderpoints con este location_id
NEW_WAREHOUSE_ID = 13     # Nuevo warehouse_id a asignar
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description='Actualizar warehouse_id de orderpoints existentes'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Modo preview: mostrar qué se actualizaría sin ejecutar cambios'
    )
    return parser.parse_args()


def connect_odoo():
    """Conectar a Odoo y retornar (models, uid, db, password)"""
    # HARDCODEADO para no interferir con otros procesos
    url = 'https://train-pldistribucion-27-02-1.adhoc.ar/'
    db = 'odoo'
    username = 'matiasblanch@performance-lube.com'
    password = 'Zairita2023'

    if not all([url, db, username, password]):
        print("Error: Faltan variables de entorno en .env")
        sys.exit(1)

    print(f"Conectando a: {url}")
    print(f"Base de datos: {db}")

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    uid = common.authenticate(db, username, password, {})
    if not uid:
        print("Error: No se pudo autenticar con Odoo")
        sys.exit(1)

    print(f"Autenticado con UID: {uid}")
    return models, uid, db, password


def main():
    args = parse_args()
    
    if args.dry_run:
        print("=" * 60)
        print("MODO DRY-RUN: No se realizarán cambios, solo preview")
        print("=" * 60)
    
    print(f"\nConfiguración:")
    print(f"  Location ID (filtro): {FILTER_LOCATION_ID}")
    print(f"  Warehouse ID (nuevo): {NEW_WAREHOUSE_ID}")
    print("-" * 60)
    
    models, uid, db, password = connect_odoo()
    
    # Buscar orderpoints con location_id especificado
    print(f"\nBuscando orderpoints con location_id = {FILTER_LOCATION_ID}...")
    
    orderpoint_ids = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search',
        [[['location_id', '=', FILTER_LOCATION_ID]]]
    )
    
    print(f"  Encontrados: {len(orderpoint_ids)} orderpoints")
    
    if not orderpoint_ids:
        print("\nNo hay orderpoints para actualizar.")
        return
    
    # Leer datos actuales para mostrar info
    print("\nLeyendo datos actuales...")
    orderpoints = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'read',
        [orderpoint_ids],
        {'fields': ['id', 'product_id', 'location_id', 'warehouse_id']}
    )
    
    # Contar cuántos ya tienen el warehouse correcto vs los que hay que cambiar
    already_correct = 0
    to_update = []
    
    for op in orderpoints:
        current_wh = op.get('warehouse_id')
        current_wh_id = current_wh[0] if current_wh else None
        
        if current_wh_id == NEW_WAREHOUSE_ID:
            already_correct += 1
        else:
            to_update.append(op)
    
    print(f"\nResumen:")
    print(f"  Ya tienen warehouse_id={NEW_WAREHOUSE_ID}: {already_correct}")
    print(f"  A actualizar:                         {len(to_update)}")
    
    if not to_update:
        print("\nTodos los orderpoints ya tienen el warehouse correcto.")
        return
    
    if args.dry_run:
        print("\n" + "-" * 60)
        print(f"SE ACTUALIZARÍAN {len(to_update)} ORDERPOINTS:")
        print("-" * 60)
        for i, op in enumerate(to_update[:15], 1):
            product = op.get('product_id')
            product_name = product[1] if product else 'Sin producto'
            current_wh = op.get('warehouse_id')
            current_wh_name = current_wh[1] if current_wh else 'Sin warehouse'
            print(f"  {i}. ID {op['id']}: {product_name[:40]} | WH actual: {current_wh_name}")
        if len(to_update) > 15:
            print(f"  ... y {len(to_update) - 15} más")
        
        print("\n[DRY-RUN] No se realizaron cambios.")
        return
    
    # Ejecutar actualización masiva
    print("\n" + "-" * 60)
    print("ACTUALIZANDO...")
    print("-" * 60)
    
    ids_to_update = [op['id'] for op in to_update]
    
    try:
        # Actualización masiva con write
        models.execute_kw(db, uid, password,
            'stock.warehouse.orderpoint', 'write',
            [ids_to_update, {'warehouse_id': NEW_WAREHOUSE_ID}]
        )
        print(f"  Actualizados {len(ids_to_update)} orderpoints exitosamente.")
    except Exception as e:
        print(f"  ERROR en actualización masiva: {e}")
        print("\n  Intentando actualización en batches...")
        
        batch_size = 500
        updated = 0
        errors = 0
        
        for i in range(0, len(ids_to_update), batch_size):
            batch = ids_to_update[i:i+batch_size]
            try:
                models.execute_kw(db, uid, password,
                    'stock.warehouse.orderpoint', 'write',
                    [batch, {'warehouse_id': NEW_WAREHOUSE_ID}]
                )
                updated += len(batch)
                print(f"    Batch {i//batch_size + 1}: {len(batch)} actualizados")
            except Exception as e2:
                errors += len(batch)
                print(f"    Batch {i//batch_size + 1}: ERROR - {e2}")
        
        print(f"\n  Actualizados: {updated}")
        print(f"  Errores: {errors}")
        return
    
    # Resumen final
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Orderpoints actualizados: {len(ids_to_update)}")
    print(f"  Nuevo warehouse_id:       {NEW_WAREHOUSE_ID}")


if __name__ == "__main__":
    main()
