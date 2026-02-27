"""
Script para crear reglas de reabastecimiento (orderpoints) para todos los productos.

Condiciones:
- Excluye productos que son kits (is_kits = True)
- No crea duplicados si ya existe orderpoint para (product_id, location_id)

Configuración:
- location_id = 22 (VLANTE / Existencias)
- route_id = 8 (ruta "comprar")
- warehouse_id = 13
- trigger = 'manual'
- product_min_qty = 0
- product_max_qty = 0

Uso:
  python scripts/create_replenishment_rules.py           # Ejecutar
  python scripts/create_replenishment_rules.py --dry-run # Preview sin crear
"""
import os
import sys
import argparse
from dotenv import load_dotenv
import xmlrpc.client

# ============================================================
# CONFIGURACIÓN HARDCODEADA
# ============================================================
LOCATION_ID = 22    # VLANTE / Existencias
ROUTE_ID = 8        # Ruta "comprar"
WAREHOUSE_ID = 13   # Almacén
TRIGGER = 'manual'
PRODUCT_MIN_QTY = 0.0
PRODUCT_MAX_QTY = 0.0
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description='Crear reglas de reabastecimiento para todos los productos'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Modo preview: mostrar qué se crearía sin ejecutar cambios'
    )
    return parser.parse_args()


def connect_odoo():
    """Conectar a Odoo y retornar (models, uid, db, password)"""
    # Cargar .env desde la raíz del proyecto
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_root, '.env'))

    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    username = os.getenv('ODOO_USER')
    password = os.getenv('ODOO_PASSWORD')

    if not all([url, db, username, password]):
        print("Error: Faltan variables de entorno en .env")
        print("  Requeridas: ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD")
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


def get_all_products(models, uid, db, password):
    """Obtener todos los productos con campos necesarios"""
    print("\nObteniendo productos...")
    
    product_ids = models.execute_kw(db, uid, password,
        'product.product', 'search', [[]]
    )
    print(f"  Total IDs encontrados: {len(product_ids)}")

    products = models.execute_kw(db, uid, password,
        'product.product', 'read',
        [product_ids],
        {'fields': ['id', 'name', 'default_code', 'uom_id', 'is_kits']}
    )
    print(f"  Productos leídos: {len(products)}")
    return products


def get_existing_orderpoints(models, uid, db, password):
    """Obtener orderpoints existentes y crear set de claves (product_id, location_id)"""
    print("\nObteniendo orderpoints existentes...")
    
    existing = models.execute_kw(db, uid, password,
        'stock.warehouse.orderpoint', 'search_read',
        [[]],
        {'fields': ['product_id', 'location_id']}
    )
    
    existing_keys = set()
    for op in existing:
        product_id = op['product_id']
        location_id = op['location_id']
        # Los campos many2one vienen como [id, name] o False
        if product_id and location_id:
            existing_keys.add((product_id[0], location_id[0]))
    
    print(f"  Orderpoints existentes: {len(existing_keys)}")
    return existing_keys


def main():
    args = parse_args()
    
    if args.dry_run:
        print("=" * 60)
        print("MODO DRY-RUN: No se crearán reglas, solo preview")
        print("=" * 60)
    
    print(f"\nConfiguración:")
    print(f"  Location ID:  {LOCATION_ID}")
    print(f"  Route ID:     {ROUTE_ID}")
    print(f"  Warehouse ID: {WAREHOUSE_ID}")
    print(f"  Trigger:      {TRIGGER}")
    print(f"  Min qty:      {PRODUCT_MIN_QTY}")
    print(f"  Max qty:      {PRODUCT_MAX_QTY}")
    print("-" * 60)
    
    # Conectar a Odoo
    models, uid, db, password = connect_odoo()
    
    # Obtener datos
    products = get_all_products(models, uid, db, password)
    existing_keys = get_existing_orderpoints(models, uid, db, password)
    
    # Procesar productos
    print("\n" + "=" * 60)
    print("PROCESANDO PRODUCTOS")
    print("=" * 60)
    
    to_create = []
    skipped_kits = []
    skipped_existing = []
    
    for product in products:
        product_id = product['id']
        product_name = product.get('name', 'Sin nombre')
        default_code = product.get('default_code') or ''
        is_kit = product.get('is_kits', False)
        uom = product.get('uom_id')
        uom_id = uom[0] if uom else None
        
        key = (product_id, LOCATION_ID)
        
        # Verificar si es kit
        if is_kit:
            skipped_kits.append((product_id, default_code, product_name))
            continue
        
        # Verificar si ya existe
        if key in existing_keys:
            skipped_existing.append((product_id, default_code, product_name))
            continue
        
        # Agregar a lista para crear
        to_create.append({
            'product_id': product_id,
            'default_code': default_code,
            'name': product_name,
            'uom_id': uom_id
        })
    
    # Resumen pre-ejecución
    print(f"\nResumen:")
    print(f"  A crear:              {len(to_create)}")
    print(f"  Omitidos (kits):      {len(skipped_kits)}")
    print(f"  Omitidos (existentes): {len(skipped_existing)}")
    
    if not to_create:
        print("\nNo hay reglas nuevas para crear.")
        return
    
    # Dry run: mostrar qué se crearía
    if args.dry_run:
        print("\n" + "-" * 60)
        print("REGLAS QUE SE CREARÍAN:")
        print("-" * 60)
        for i, item in enumerate(to_create[:20], 1):  # Mostrar máx 20
            print(f"  {i}. [{item['default_code']}] {item['name'][:50]}")
        if len(to_create) > 20:
            print(f"  ... y {len(to_create) - 20} más")
        
        print("\n" + "-" * 60)
        print("PRODUCTOS OMITIDOS POR SER KITS:")
        print("-" * 60)
        for i, (pid, code, name) in enumerate(skipped_kits[:10], 1):
            print(f"  {i}. [{code}] {name[:50]}")
        if len(skipped_kits) > 10:
            print(f"  ... y {len(skipped_kits) - 10} más")
        
        print("\n[DRY-RUN] No se realizaron cambios.")
        return
    
    # Crear reglas
    print("\n" + "-" * 60)
    print("CREANDO REGLAS...")
    print("-" * 60)
    
    created = []
    errors = []
    
    for item in to_create:
        try:
            models.execute_kw(db, uid, password,
                'stock.warehouse.orderpoint', 'create',
                [{
                    'product_id': item['product_id'],
                    'location_id': LOCATION_ID,
                    'warehouse_id': WAREHOUSE_ID,
                    'product_min_qty': PRODUCT_MIN_QTY,
                    'product_max_qty': PRODUCT_MAX_QTY,
                    'product_uom': item['uom_id'],
                    'trigger': TRIGGER,
                    'route_id': ROUTE_ID
                }]
            )
            created.append(item)
            print(f"  Creada: [{item['default_code']}] {item['name'][:40]}")
        except Exception as e:
            errors.append((item, str(e)))
            print(f"  ERROR [{item['default_code']}]: {e}")
    
    # Resumen final
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Reglas creadas:        {len(created)}")
    print(f"  Errores:               {len(errors)}")
    print(f"  Omitidos (kits):       {len(skipped_kits)}")
    print(f"  Omitidos (existentes): {len(skipped_existing)}")
    
    if errors:
        print("\nErrores detallados:")
        for item, error in errors[:10]:
            print(f"  - [{item['default_code']}] {item['name'][:30]}: {error}")
        if len(errors) > 10:
            print(f"  ... y {len(errors) - 10} más")


if __name__ == "__main__":
    main()
