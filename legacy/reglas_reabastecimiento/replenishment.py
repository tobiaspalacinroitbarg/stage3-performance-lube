import os
from dotenv import load_dotenv
import xmlrpc.client

def main():
    # Cargar variables desde odoo.env
    dotenv_path = os.path.join(os.path.dirname(__file__), 'odoo.env')
    load_dotenv(dotenv_path)
    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    username = os.getenv('ODOO_USERNAME')
    password = os.getenv('ODOO_PASSWORD')
    print(f"URL de Odoo: {url}")
    # Conectar a Odoo
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    uid = common.authenticate(db, username, password, {})
    if not uid:
        raise Exception("No se pudo autenticar con Odoo")
    print(f"Autenticado con UID: {uid}")

    # Obtener todos los productos
    product_ids = models.execute_kw(db, uid, password,
        'product.product', 'search', [[]])
    print(f"Total de IDs recuperados: {len(product_ids)}")

    products = models.execute_kw(db, uid, password,
        'product.product', 'read',
        [product_ids],
        {'fields': ['id', 'name', 'property_stock_inventory', 'uom_id', 'is_kits']}
    )
    print(f"Productos leídos: {len(products)}")

    # Obtener orderpoints existentes
    existing_orderpoints = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search_read',
        [[]],
        {'fields': ['product_id', 'location_id']}
    )
    existing_keys = {
        (op['product_id'][0], op['location_id'][0]) for op in existing_orderpoints
    }
    print(f"Orderpoints existentes: {len(existing_keys)}")

    # Crear reglas de abastecimiento en modo manual para productos válidos
    created_product_ids = []
    errored_products = []
    location_id = 22  # ubicación fija
    route_id = 8  # ID de la ruta "comprar"

    for product in products:
        product_id = product['id']
        is_kit = product.get('is_kits', False)
        uom = product['uom_id']
        uom_id = uom[0] if uom else None

        key = (product_id, location_id)
        if is_kit or key in existing_keys:
            continue

        try:
            models.execute_kw(db, uid, password,
                'stock.warehouse.orderpoint', 'create',
                [{
                    'product_id': product_id,
                    'location_id': location_id,
                    'product_min_qty': 0.0,
                    'product_max_qty': 0.0,
                    'product_uom': uom_id,
                    'trigger': 'manual',
                    'route_id': route_id  # Asignar la ruta "comprar"
                }]
            )
            created_product_ids.append(product_id)
            print(f"🆕 Creada regla para producto ID {product_id} en ubicación {location_id} con ruta {route_id} (manual)")
        except Exception as e:
            print(f"⚠️ Error creando regla para producto {product_id}: {e}")
            errored_products.append(product_id)
            continue

    print(f"\n✅ Total reglas creadas: {len(created_product_ids)}")
    print(f"⚠️ Total errores: {len(errored_products)}")

if __name__ == "__main__":
    main()