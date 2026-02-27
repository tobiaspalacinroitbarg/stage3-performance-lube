#!/usr/bin/env python3
"""
Script automático para actualizar orderpoints de Odoo.
Se ejecuta automáticamente cada sábado a las 14:00.
Corre en loop continuo bajo pm2.
"""
import os
from dotenv import load_dotenv
import xmlrpc.client
from collections import defaultdict
import math
import logging
import time
from datetime import datetime, timedelta

# Configurar logging
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f'replenishment_update_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

def next_saturday_1410_utc(now=None):
    """
    Calcula el próximo sábado a las 17:10 UTC (= 14:10 Argentina UTC-3)
    Servidor está en UTC, así que usamos hora del servidor directamente.
    """
    now = now or datetime.now()
    # weekday(): Monday=0 ... Saturday=5
    days_ahead = (5 - now.weekday()) % 7
    
    # 14:10 Argentina = 17:10 UTC
    target = (now + timedelta(days=days_ahead)).replace(hour=17, minute=10, second=0, microsecond=0)
    
    if target <= now:
        target += timedelta(days=7)
    return target

def run_update():
    logging.info("="*60)
    logging.info("Iniciando actualización automática de orderpoints")
    logging.info("="*60)
    
    # Cargar variables desde odoo_real.env
    dotenv_path = os.path.join(os.path.dirname(__file__), 'odoo_real.env')
    load_dotenv(dotenv_path)
    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    username = os.getenv('ODOO_USERNAME')
    password = os.getenv('ODOO_PASSWORD')

    if not all([url, db, username, password]):
        logging.error("Faltan variables de entorno. Verificar odoo_real.env")
        return 1

    # Conectar a Odoo
    try:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        uid = common.authenticate(db, username, password, {})
        if not uid:
            raise Exception("No se pudo autenticar con Odoo")
        logging.info(f"Autenticado con UID: {uid}")
    except Exception as e:
        logging.error(f"Error al conectar con Odoo: {e}")
        return 1

    # Leer los orderpoints en lotes para evitar timeout
    batch_size = 1000
    offset = 0
    updates = defaultdict(list)
    skipped = 0

    try:
        # Buscar todos los IDs primero usando warehouse_rotation
        orderpoint_ids = models.execute_kw(db, uid, password,
            'stock.warehouse.orderpoint', 'search',
            [[['warehouse_rotation', '>=', 0]]]
        )
        total_orderpoints = len(orderpoint_ids)
        logging.info(f"Orderpoints encontrados: {total_orderpoints}")

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
            logging.info(f"Procesados {min(offset, total_orderpoints)}/{total_orderpoints} orderpoints...")

        logging.info(f"Orderpoints a actualizar: {sum(len(ids) for ids in updates.values())}")
        logging.info(f"Orderpoints ya correctos: {skipped}")

        # Hacer los updates agrupados con manejo de errores
        update_errors = []
        total_batches = len(updates)
        processed_batches = 0
        total_to_update = sum(len(ids) for ids in updates.values())
        updated_so_far = 0
        logging.info(f"Procesando {total_batches} grupos de updates...")
        
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
                    logging.info(f"Orderpoint {op['id']} actualizado: Producto='{product_name}', Location='{location_name}', min={min_val}, max={max_val}")
                logging.info(f"[{processed_batches}/{total_batches}] Actualizados {len(op_ids)} orderpoints a min={min_val}, max={max_val} (Total actualizados: {updated_so_far}/{total_to_update})")
            except Exception as e:
                logging.error(f"[{processed_batches}/{total_batches}] Error actualizando orderpoints {[op['id'] for op in ops]} a min={min_val}, max={max_val}: {e}")
                update_errors.append({'ids': op_ids, 'min': min_val, 'max': max_val, 'error': str(e)})
            if processed_batches % 10 == 0:
                logging.info(f"Progreso: {processed_batches}/{total_batches} grupos procesados...")

        logging.info("Proceso finalizado.")
        if update_errors:
            logging.warning(f"Errores en {len(update_errors)} grupos de updates. Detalle de productos no modificados:")
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
                            logging.warning(f"  No modificado: Orderpoint {op_id}, Producto='{product_name}', Location='{location_name}', min={min_val}, max={max_val}")
                    except Exception as e:
                        logging.error(f"  No modificado: Orderpoint {op_id}, error al obtener info: {e}")
        
        logging.info(f"Log guardado en: {log_file}")
        logging.info("="*60)
        return 0
        
    except Exception as e:
        logging.error(f"Error general durante la ejecución: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    import sys
    
    # Mostrar hora actual del servidor para debugging
    server_time = datetime.now()
    logging.info("="*60)
    logging.info(f"Hora actual del servidor: {server_time.strftime('%Y-%m-%d %H:%M:%S %A')}")
    logging.info("Iniciando scheduler - Ejecución cada sábado a las 17:10 UTC (14:10 Argentina)")
    logging.info("="*60)
    
    while True:
        now = datetime.now()
        target = next_saturday_1410_utc(now)
        sleep_seconds = (target - now).total_seconds()
        
        logging.info(f"Hora actual: {now.strftime('%Y-%m-%d %H:%M:%S %A')}")
        logging.info(f"Próxima ejecución: {target.strftime('%Y-%m-%d %H:%M:%S %A')}")
        logging.info(f"Durmiendo {sleep_seconds/3600:.2f} horas ({sleep_seconds/60:.1f} minutos)...")
        
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        
        # Ejecutar el update
        logging.info("Iniciando ejecución programada...")
        exit_code = run_update()
        
        if exit_code != 0:
            logging.error(f"Ejecución terminó con errores (código {exit_code})")
        else:
            logging.info("Ejecución completada exitosamente")
        
        # Esperar un poco antes de calcular la próxima ejecución
        time.sleep(60)
