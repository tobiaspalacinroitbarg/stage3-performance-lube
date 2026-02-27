import os
from dotenv import load_dotenv
import xmlrpc.client
from collections import defaultdict
import math

def main():
    # Cargar variables desde odoo.env
    dotenv_path = os.path.join(os.path.dirname(__file__), 'odoo.env')
    load_dotenv(dotenv_path)
    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    username = os.getenv('ODOO_USERNAME')
    password = os.getenv('ODOO_PASSWORD')

    # Conectar a Odoo
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    uid = common.authenticate(db, username, password, {})
    if not uid:
        raise Exception("No se pudo autenticar con Odoo")
    print(f"Autenticado con UID: {uid}")

    # Buscar todos los orderpoints con warehouse_rotation > 0 y pedir min y max
    orderpoint_ids = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search',
        [[['warehouse_rotation', '>=', 0]]]
    )
    print(f"Orderpoints encontrados: {len(orderpoint_ids)}")

    orderpoints = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'read',
        [orderpoint_ids],
        {'fields': ['id', 'warehouse_rotation', 'product_min_qty', 'product_max_qty']}
    )

    # Agrupar por (min, max) los que necesitan update
    updates = defaultdict(list)
    skipped = 0


    for op in orderpoints:
        warehouse_rotation = op.get('warehouse_rotation')
        min_qty = op.get('product_min_qty')
        max_qty = op.get('product_max_qty')
        op_id = op['id']

        if warehouse_rotation is None or warehouse_rotation <= 0:
            continue

        new_min = float(math.ceil(warehouse_rotation))
        new_max = float(math.ceil(warehouse_rotation) * 2)

        if min_qty == new_min and max_qty == new_max:
            skipped += 1
            continue

        updates[(new_min, new_max)].append(op_id)

    print(f"Orderpoints a actualizar: {sum(len(ids) for ids in updates.values())}")
    print(f"Orderpoints ya correctos: {skipped}")

    # Hacer los updates agrupados con manejo de errores
    update_errors = []
    total_batches = len(updates)
    processed_batches = 0
    total_to_update = sum(len(ids) for ids in updates.values())
    updated_so_far = 0
    print(f"Procesando {total_batches} grupos de updates...")
    for (min_val, max_val), ids in updates.items():
        processed_batches += 1
        try:
            models.execute_kw(db, uid, password,
                'stock.warehouse.orderpoint', 'write',
                [ids, {'product_min_qty': min_val, 'product_max_qty': max_val}]
            )
            updated_so_far += len(ids)
            print(f"[{processed_batches}/{total_batches}] Actualizados {len(ids)} orderpoints a min={min_val}, max={max_val} (Total actualizados: {updated_so_far}/{total_to_update})")
        except Exception as e:
            print(f"[{processed_batches}/{total_batches}] Error actualizando orderpoints {ids} a min={min_val}, max={max_val}: {e}")
            update_errors.append({'ids': ids, 'min': min_val, 'max': max_val, 'error': str(e)})
        if processed_batches % 10 == 0:
            print(f"Progreso: {processed_batches}/{total_batches} grupos procesados...")

    print("Proceso finalizado.")
    if update_errors:
        print(f"Errores en {len(update_errors)} grupos de updates. Revisar la variable 'update_errors' para detalles.")

if __name__ == "__main__":
    import sys
    main()
    sys.exit(0)