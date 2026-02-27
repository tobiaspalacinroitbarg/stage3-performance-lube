import os
from dotenv import load_dotenv
import xmlrpc.client
import math

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

# Buscar todos los orderpoints con rotation >= 0 y pedir min y max
orderpoint_ids = models.execute_kw(db, uid, password,
    'stock.warehouse.orderpoint', 'search',
    [[['rotation', '>=', 0]]]
)
print(f"Orderpoints encontrados: {len(orderpoint_ids)}")

orderpoints = models.execute_kw(db, uid, password,
    'stock.warehouse.orderpoint', 'read',
    [orderpoint_ids],
    {'fields': ['id', 'rotation', 'product_min_qty', 'product_max_qty']}
)

# Chequeo de correlación final
print("\nChequeando correlación entre rotation, min y max...")
incorrects = []
for op in orderpoints:
    rotation = op.get('rotation')
    min_qty = op.get('product_min_qty')
    max_qty = op.get('product_max_qty')
    op_id = op['id']
    if rotation is None or rotation <= 0:
        continue
    expected_min = float(math.ceil(rotation))
    expected_max = float(math.ceil(rotation) * 2)
    if min_qty != expected_min or max_qty != expected_max:
        incorrects.append({'id': op_id, 'rotation': rotation, 'min': min_qty, 'max': max_qty, 'expected_min': expected_min, 'expected_max': expected_max})
if incorrects:
    print(f"\nOrderpoints que NO cumplen correlación: {len(incorrects)}")
    for inc in incorrects[:20]:
        print(f"ID {inc['id']}: rotation={inc['rotation']} min={inc['min']} (esperado {inc['expected_min']}), max={inc['max']} (esperado {inc['expected_max']})")
    if len(incorrects) > 20:
        print(f"... y {len(incorrects)-20} más.")
else:
    print("Todos los orderpoints cumplen con la correlación rotation, min y max.")
