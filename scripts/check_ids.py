"""
Script para verificar a qué corresponden location_id=22 y route_id=8 en Odoo.
"""
import os
import sys
from dotenv import load_dotenv
import xmlrpc.client

# Cargar .env desde la raíz del proyecto
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, '.env'))

url = os.getenv('ODOO_URL')
db = os.getenv('ODOO_DB')
username = os.getenv('ODOO_USER')
password = os.getenv('ODOO_PASSWORD')

print(f"Conectando a: {url}")
print(f"Base de datos: {db}")
print("-" * 50)

try:
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    uid = common.authenticate(db, username, password, {})
    if not uid:
        print("Error: No se pudo autenticar con Odoo")
        sys.exit(1)
    print(f"Autenticado con UID: {uid}\n")

    # Verificar location_id = 22
    print("=" * 50)
    print("LOCATION ID 22")
    print("=" * 50)
    location = models.execute_kw(db, uid, password,
        'stock.location', 'read', [[22]], 
        {'fields': ['id', 'name', 'complete_name', 'usage']}
    )
    if location:
        loc = location[0]
        print(f"  ID:            {loc.get('id')}")
        print(f"  Nombre:        {loc.get('name')}")
        print(f"  Nombre completo: {loc.get('complete_name')}")
        print(f"  Uso:           {loc.get('usage')}")
    else:
        print("  No encontrado")

    # Verificar route_id = 8
    print("\n" + "=" * 50)
    print("ROUTE ID 8")
    print("=" * 50)
    route = models.execute_kw(db, uid, password,
        'stock.route', 'read', [[8]], 
        {'fields': ['id', 'name', 'active']}
    )
    if route:
        r = route[0]
        print(f"  ID:     {r.get('id')}")
        print(f"  Nombre: {r.get('name')}")
        print(f"  Activa: {r.get('active')}")
    else:
        print("  No encontrada")

    # Verificar warehouse_id = 13
    print("\n" + "=" * 50)
    print("WAREHOUSE ID 13")
    print("=" * 50)
    warehouse = models.execute_kw(db, uid, password,
        'stock.warehouse', 'read', [[13]], 
        {'fields': ['id', 'name', 'code', 'active']}
    )
    if warehouse:
        w = warehouse[0]
        print(f"  ID:     {w.get('id')}")
        print(f"  Nombre: {w.get('name')}")
        print(f"  Código: {w.get('code')}")
        print(f"  Activo: {w.get('active')}")
    else:
        print("  No encontrado")

except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
