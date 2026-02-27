import os
from dotenv import load_dotenv
import xmlrpc.client
import sys


def load_env():
    dotenv_path = os.path.join(os.path.dirname(__file__), 'odoo.env')
    load_dotenv(dotenv_path)
    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    user = os.getenv('ODOO_USERNAME')
    pwd = os.getenv('ODOO_PASSWORD')
    if not all([url, db, user, pwd]):
        raise ValueError('Faltan variables de entorno en odoo.env')
    return url, db, user, pwd


def connect(url):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return common, models


def preview_and_update():
    url, db, user, pwd = load_env()
    common, models = connect(url)
    uid = common.authenticate(db, user, pwd, {})
    if not uid:
        raise SystemExit('No se pudo autenticar con Odoo')

    # IDs conocidos (ajustar si es necesario)
    location_vlante_existencias = 22  # VLANTE / Existencias
    route_comprar = 8  # ID de la ruta "comprar"

    # Buscar orderpoints sin route en la ubicación indicada
    domain = [
        ('location_id', '=', location_vlante_existencias),
        ('route_id', '=', False),
    ]
    orderpoint_ids = models.execute_kw(db, uid, pwd,
                                       'stock.warehouse.orderpoint',
                                       'search', [domain])

    print(f"Reglas encontradas: {len(orderpoint_ids)}")
    if not orderpoint_ids:
        print('No hay orderpoints que cumplan el criterio.')
        return

    # Leer info relevante para el preview
    fields = ['id', 'product_id', 'location_id', 'route_id', 'product_min_qty', 'product_max_qty']
    orderpoints = models.execute_kw(db, uid, pwd,
                                    'stock.warehouse.orderpoint',
                                    'read', [orderpoint_ids], {'fields': fields})

    # Recolectar products para mostrar nombres
    product_ids = [op['product_id'][0] for op in orderpoints if op.get('product_id')]
    products = {}
    if product_ids:
        prods = models.execute_kw(db, uid, pwd, 'product.product', 'read', [product_ids], {'fields': ['id', 'name']})
        products = {p['id']: p['name'] for p in prods}

    # Mostrar preview (máx 50 filas)
    print('\nPreview (hasta 50):')
    for op in orderpoints[:50]:
        pid = op.get('product_id')
        if isinstance(pid, list) and pid:
            pid_str = f"{pid[0]} - {products.get(pid[0], pid[1] if len(pid) > 1 else '')}"
        elif pid:
            pid_str = str(pid)
        else:
            pid_str = 'Sin producto'
        print(f"orderpoint id={op['id']}, product={pid_str}, min={op.get('product_min_qty')}, max={op.get('product_max_qty')}")

    # Confirmación del usuario antes de escribir
    # answer = input("\nContinuar y asignar la ruta 'comprar' (ID {}) a estas reglas? (s/n): ".format(route_comprar)).strip().lower()
    # if answer not in ('s', 'si', 'y', 'yes'):
    #     print('Operación cancelada por el usuario.')
    #     return

    # Ejecutar la actualización
    updated = 0
    for op_id in orderpoint_ids:
        try:
            models.execute_kw(db, uid, pwd,
                              'stock.warehouse.orderpoint', 'write', [[op_id], {'route_id': route_comprar}])
            updated += 1
            print(f"Orderpoint {op_id} actualizado con la ruta 'comprar' (ID {route_comprar}).")
        except Exception as e:
            print(f"Error actualizando orderpoint {op_id}: {e}")

    print(f"\nTotal actualizados: {updated}")


if __name__ == '__main__':
    try:
        preview_and_update()
    except Exception as e:
        print('Error:', e)
        sys.exit(1)