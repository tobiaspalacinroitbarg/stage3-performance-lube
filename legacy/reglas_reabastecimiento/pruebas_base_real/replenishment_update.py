import os
from dotenv import load_dotenv
import xmlrpc.client
from collections import defaultdict
import math

def main():
    # Cargar variables desde odoo.env
    dotenv_path = os.path.join(os.path.dirname(__file__), 'odoo_real.env')
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


    # Leer los orderpoints en lotes para evitar timeout
    batch_size = 1000
    offset = 0
    total_found = 0
    updates = defaultdict(list)
    skipped = 0

    # Buscar todos los IDs primero usando warehouse_rotation
    orderpoint_ids = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search',
        [[['warehouse_rotation', '>=', 0]]]
    )
    total_orderpoints = len(orderpoint_ids)
    print(f"Orderpoints encontrados: {total_orderpoints}")

    while offset < total_orderpoints:
        batch_ids = orderpoint_ids[offset:offset+batch_size]
        orderpoints = models.execute_kw(db, uid, password,
            'stock.warehouse.orderpoint', 'read',
            [batch_ids],
            {'fields': ['id', 'warehouse_rotation', 'product_min_qty', 'product_max_qty', 'product_id', 'location_id']}
        )
        for op in orderpoints:
            warehouse_rotation = op.get('warehouse_rotation')
            min_qty = op.get('product_min_qty')
            max_qty = op.get('product_max_qty')
            op_id = op['id']
            product_id = op.get('product_id', [None, None])[0]
            location_id = op.get('location_id', [None, None])[0]

            if warehouse_rotation is None or warehouse_rotation < 0:
                continue

            new_min = float(math.ceil(warehouse_rotation))
            new_max = float(math.ceil(warehouse_rotation) * 2)

            if min_qty == new_min and max_qty == new_max:
                skipped += 1
                continue

            # Guardar info extra para impresión luego
            updates[(new_min, new_max)].append({'id': op_id, 'product_id': product_id, 'location_id': location_id})
        offset += batch_size
        print(f"Procesados {min(offset, total_orderpoints)}/{total_orderpoints} orderpoints...")

    print(f"Orderpoints a actualizar: {sum(len(ids) for ids in updates.values())}")
    print(f"Orderpoints ya correctos: {skipped}")

    # Hacer los updates agrupados con manejo de errores
    update_errors = []
    total_batches = len(updates)
    processed_batches = 0
    total_to_update = sum(len(ids) for ids in updates.values())
    updated_so_far = 0
    print(f"Procesando {total_batches} grupos de updates...")
    for (min_val, max_val), ops in updates.items():
        processed_batches += 1
        op_ids = [op['id'] for op in ops]
        try:
            models.execute_kw(db, uid, password,
                'stock.warehouse.orderpoint', 'write',
                [op_ids, {'product_min_qty': min_val, 'product_max_qty': max_val}]
            )
            updated_so_far += len(op_ids)
            # Obtener nombres de producto y location para cada orderpoint
            for op in ops:
                # Leer nombre producto
                product_name = None
                location_name = None
                if op['product_id']:
                    prod = models.execute_kw(db, uid, password,
                        'product.product', 'read',
                        [[op['product_id']], ['name']])
                    if prod and isinstance(prod, list) and 'name' in prod[0]:
                        product_name = prod[0]['name']
                if op['location_id']:
                    loc = models.execute_kw(db, uid, password,
                        'stock.location', 'read',
                        [[op['location_id']], ['name']])
                    if loc and isinstance(loc, list) and 'name' in loc[0]:
                        location_name = loc[0]['name']
                print(f"Orderpoint {op['id']} actualizado: Producto='{product_name}', Location='{location_name}', min={min_val}, max={max_val}")
            print(f"[{processed_batches}/{total_batches}] Actualizados {len(op_ids)} orderpoints a min={min_val}, max={max_val} (Total actualizados: {updated_so_far}/{total_to_update})")
        except Exception as e:
            print(f"[{processed_batches}/{total_batches}] Error actualizando orderpoints {[op['id'] for op in ops]} a min={min_val}, max={max_val}: {e}")
            update_errors.append({'ids': op_ids, 'min': min_val, 'max': max_val, 'error': str(e)})
        if processed_batches % 10 == 0:
            print(f"Progreso: {processed_batches}/{total_batches} grupos procesados...")

    print("Proceso finalizado.")
    if update_errors:
        print(f"Errores en {len(update_errors)} grupos de updates. Detalle de productos no modificados:")
        for err in update_errors:
            op_ids = err['ids']
            min_val = err['min']
            max_val = err['max']
            # Leer info de producto y location para cada orderpoint fallido
            for op_id in op_ids:
                try:
                    op = models.execute_kw(db, uid, password,
                        'stock.warehouse.orderpoint', 'read',
                        [[op_id], ['product_id', 'location_id']])
                    if op and isinstance(op, list):
                        op = op[0]
                        product_name = None
                        location_name = None
                        if op.get('product_id'):
                            prod = models.execute_kw(db, uid, password,
                                'product.product', 'read',
                                [[op['product_id'][0]], ['name']])
                            if prod and isinstance(prod, list) and 'name' in prod[0]:
                                product_name = prod[0]['name']
                        if op.get('location_id'):
                            loc = models.execute_kw(db, uid, password,
                                'stock.location', 'read',
                                [[op['location_id'][0]], ['name']])
                            if loc and isinstance(loc, list) and 'name' in loc[0]:
                                location_name = loc[0]['name']
                        print(f"  No modificado: Orderpoint {op_id}, Producto='{product_name}', Location='{location_name}', min={min_val}, max={max_val}")
                except Exception as e:
                    print(f"  No modificado: Orderpoint {op_id}, error al obtener info: {e}")
        print("Revisar la variable 'update_errors' para más detalles.")

if __name__ == "__main__":
    import sys
    main()
    sys.exit(0)