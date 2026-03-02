"""
Script para actualizar min/max de reglas de reabastecimiento basado en rotación.

Fórmula:
- product_min_qty = ceil(warehouse_rotation)
- product_max_qty = ceil(warehouse_rotation) * 2

Características:
- URL hardcodeada a producción (pldistribucion.adhoc.ar)
- Agrupa updates por (min, max) para minimizar requests
- Delay entre requests para evitar rate limiting
- Retry con backoff en caso de 429

Uso:
  python scripts/update_replenishment_minmax.py           # Ejecutar
  python scripts/update_replenishment_minmax.py --dry-run # Preview sin cambios
"""
import os
import sys
import time
import math
import argparse
from collections import defaultdict
from dotenv import load_dotenv
import xmlrpc.client


def parse_args():
    parser = argparse.ArgumentParser(
        description='Actualizar min/max de reglas de reabastecimiento basado en rotación'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Modo preview: mostrar qué se actualizaría sin ejecutar cambios'
    )
    return parser.parse_args()


def connect_odoo():
    """Conectar a Odoo producción"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_root, '.env'))

    # URL hardcodeada a producción
    url = "https://pldistribucion.adhoc.ar"
    db = "odoo"
    username = os.getenv('ODOO_USER')
    password = os.getenv('ODOO_PASSWORD')

    if not all([username, password]):
        print("Error: Faltan variables de entorno en .env")
        print("  Requeridas: ODOO_USER, ODOO_PASSWORD")
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


def get_orderpoints_with_rotation(models, uid, db, password):
    """Obtener orderpoints con warehouse_rotation >= 0"""
    print("\nObteniendo orderpoints con rotación...")
    
    orderpoint_ids = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search',
        [[['warehouse_rotation', '>=', 0]]]
    )
    print(f"  Orderpoints encontrados: {len(orderpoint_ids)}")

    orderpoints = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'read',
        [orderpoint_ids],
        {'fields': ['id', 'warehouse_rotation', 'product_min_qty', 'product_max_qty', 'product_id']}
    )
    
    return orderpoints


def main():
    args = parse_args()
    
    print("=" * 60)
    print("ACTUALIZACIÓN DE MIN/MAX BASADO EN ROTACIÓN")
    print("=" * 60)
    print(f"\nFórmula:")
    print(f"  min_qty = ceil(warehouse_rotation)")
    print(f"  max_qty = ceil(warehouse_rotation) * 2")
    print("-" * 60)
    
    # Conectar
    models, uid, db, password = connect_odoo()
    
    # Obtener orderpoints
    orderpoints = get_orderpoints_with_rotation(models, uid, db, password)
    
    # Agrupar por (new_min, new_max) los que necesitan update
    updates = defaultdict(list)
    skipped_no_rotation = 0
    skipped_already_correct = 0
    
    print("\nAnalizando orderpoints...")
    
    for op in orderpoints:
        warehouse_rotation = op.get('warehouse_rotation')
        current_min = op.get('product_min_qty')
        current_max = op.get('product_max_qty')
        op_id = op['id']
        
        # Saltar si no tiene rotación válida
        if warehouse_rotation is None or warehouse_rotation <= 0:
            skipped_no_rotation += 1
            continue
        
        # Calcular nuevos valores
        new_min = float(math.ceil(warehouse_rotation))
        new_max = float(math.ceil(warehouse_rotation) * 2)
        
        # Saltar si ya está correcto
        if current_min == new_min and current_max == new_max:
            skipped_already_correct += 1
            continue
        
        # Agregar al grupo correspondiente
        updates[(new_min, new_max)].append({
            'id': op_id,
            'product_id': op.get('product_id'),
            'rotation': warehouse_rotation,
            'old_min': current_min,
            'old_max': current_max
        })
    
    total_to_update = sum(len(ops) for ops in updates.values())
    
    # Resumen
    print(f"\nResumen:")
    print(f"  A actualizar:          {total_to_update}")
    print(f"  Ya correctos:          {skipped_already_correct}")
    print(f"  Sin rotación válida:   {skipped_no_rotation}")
    print(f"  Grupos de update:      {len(updates)}")
    
    if not updates:
        print("\nNo hay orderpoints para actualizar.")
        return
    
    # Dry run: mostrar qué se haría
    if args.dry_run:
        print("\n" + "-" * 60)
        print("CAMBIOS QUE SE HARÍAN:")
        print("-" * 60)
        
        for (new_min, new_max), ops in sorted(updates.items()):
            print(f"\n  min={new_min}, max={new_max} ({len(ops)} orderpoints):")
            for op in ops[:3]:  # Mostrar máx 3 ejemplos
                product_name = op['product_id'][1] if op['product_id'] else 'N/A'
                print(f"    - {product_name[:40]} (rot={op['rotation']:.1f}, actual: {op['old_min']}/{op['old_max']})")
            if len(ops) > 3:
                print(f"    ... y {len(ops) - 3} más")
        
        print("\n" + "-" * 60)
        print("[DRY-RUN] No se realizaron cambios.")
        return
    
    # Ejecutar updates
    print("\n" + "-" * 60)
    print("EJECUTANDO UPDATES (con delay entre grupos)...")
    print("-" * 60)
    
    updated_count = 0
    error_count = 0
    total_groups = len(updates)
    
    for i, ((new_min, new_max), ops) in enumerate(sorted(updates.items()), 1):
        op_ids = [op['id'] for op in ops]
        
        # Retry con backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                models.execute_kw(db, uid, password,
                    'stock.warehouse.orderpoint', 'write',
                    [op_ids, {'product_min_qty': new_min, 'product_max_qty': new_max}]
                )
                updated_count += len(ops)
                print(f"  [{i}/{total_groups}] Actualizados {len(ops)} orderpoints -> min={new_min}, max={new_max} (Total: {updated_count}/{total_to_update})")
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 3
                    print(f"  [{i}/{total_groups}] Rate limited, esperando {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                print(f"  [{i}/{total_groups}] ERROR: {e}")
                error_count += len(ops)
                break
        
        # Delay entre grupos
        time.sleep(0.3)
    
    # Resumen final
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Actualizados:   {updated_count}")
    print(f"  Errores:        {error_count}")
    print(f"  Ya correctos:   {skipped_already_correct}")
    print(f"  Sin rotación:   {skipped_no_rotation}")


if __name__ == "__main__":
    main()
