
# Generación de O.C. por venta desde Scraper
# Modelo?
# Transferir 
# Activar?

# El estado está establecido como
# Disponible
# Antes de actualizar el dominio?
# Conciliar todos los registros
# Aplicar en?
# Conciliar
# todas
# de las siguientes reglas:
# Tipo de operación
# =
# TODO: Recolectar
# Estado
# =
# Disponible
# Operaciones ➔ Desde ➔ Nombre completo de la ubicación
# contiene
# StockSCRAP
# --- CONFIGURACIÓN ---
# Si quieres depurar, deja esto en True. Escribirá pasos en el chatter.
DEBUG_MODE = True

for picking in records:
    try:
        if DEBUG_MODE:
            picking.message_post(body="🤖 <b>Iniciando Script de Reposición (Scrap)...</b>")

        # 1. VERIFICAR DUPLICADOS
        # Buscamos si ya hay una PO creada con este Picking como origen
        po_existe = env['purchase.order'].search([
            ('origin', '=', picking.name),
            ('state', '!=', 'cancel')
        ], limit=1)
        
        if po_existe:
            if DEBUG_MODE:
                picking.message_post(body=f"⚠️ Ya existe la PO {po_existe.name}. Cancelando ejecución.")
            continue

        # Diccionario: { Proveedor: {Producto: Cantidad} }
        compras_por_proveedor = {}
        hay_items = False

        # 2. RECORRER LÍNEAS DEL PICKING (stock.move.line)
        # Usamos move_line_ids_without_package para ir a lo seguro en la vista detallada
        lines_to_check = picking.move_line_ids if picking.move_line_ids else picking.move_line_ids_without_package
        
        for line in lines_to_check:
            # Filtro: Que salga de una ubicación que contenga "SCRAP"
            # Usamos location_id.complete_name para ver la ruta entera (TODO/Stock/Scraper...)
            if line.location_id and 'SCRAP' in line.location_id.complete_name.upper():
                
                # --- CORRECCIÓN CRÍTICA DE CANTIDAD ---
                # 1. Intentamos leer lo que ya se hizo (Done)
                qty = line.quantity
                
                # 2. Si es 0 (común en estado Disponible), leemos lo RESERVADO
                if qty == 0:
                    # Intentamos campo estándar de reserva
                    if hasattr(line, 'reserved_uom_qty'):
                        qty = line.reserved_uom_qty
                    # Fallback para versiones viejas o raras
                    elif hasattr(line, 'product_uom_qty'):
                        qty = line.product_uom_qty
                
                if DEBUG_MODE:
                    picking.message_post(body=f"🔎 Revisando {line.product_id.name}. Origen: {line.location_id.name}. Cantidad detectada: {qty}")

                if qty > 0:
                    prod = line.product_id
                    
                    # --- PROVEEDOR ---
                    # Tomamos el primer proveedor configurado en el producto
                    if prod.seller_ids:
                        partner = prod.seller_ids[0].partner_id
                        
                        if partner:
                            hay_items = True
                            if partner not in compras_por_proveedor:
                                compras_por_proveedor[partner] = {}
                            
                            # Acumular
                            if prod in compras_por_proveedor[partner]:
                                compras_por_proveedor[partner][prod] += qty
                            else:
                                compras_por_proveedor[partner][prod] = qty
                    else:
                        if DEBUG_MODE:
                            picking.message_post(body=f"⚠️ El producto {prod.name} no tiene proveedor configurado. Se omite.")

        # 3. CREAR ÓRDENES DE COMPRA
        if hay_items:
            for partner, productos in compras_por_proveedor.items():
                
                # A. Crear Cabecera
                po = env['purchase.order'].create({
                    'partner_id': partner.id,
                    'origin': picking.name, # Enlace clave con el Picking
                    'date_order': datetime.datetime.now(),
                    'company_id': picking.company_id.id,
                })
                
                # B. Crear Líneas
                for product, cantidad in productos.items():
                    env['purchase.order.line'].create({
                        'order_id': po.id,
                        'product_id': product.id,
                        'product_qty': cantidad,
                        'price_unit': product.standard_price, 
                        'date_planned': datetime.datetime.now(),
                        'name': product.name,
                        'product_uom': product.uom_id.id,
                    })
                
                # 4. LOGS Y LINKS
                link_po = f"<a href='#' data-oe-model='purchase.order' data-oe-id='{po.id}'>{po.name}</a>"
                mensaje = f"✅ <b>Reposición Automática (Scrap):</b> Se generó la {link_po} asociada a este movimiento."

                # A. Log en Picking
                picking.message_post(body=mensaje, message_type='comment', subtype_xmlid='mail.mt_note')

                # B. Log en Venta (Búsqueda robusta)
                venta = picking.sale_id
                if not venta and picking.origin:
                     # Intentar buscar venta por nombre exacto
                     venta = env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                     
                     # Si falla, intentar buscar si el origen contiene el nombre (ej: V001 Retorno)
                     if not venta:
                         venta = env['sale.order'].search([('name', 'ilike', picking.origin.split()[0])], limit=1)

                if venta:
                    venta.message_post(body=mensaje, message_type='comment', subtype_xmlid='mail.mt_note')
        else:
            if DEBUG_MODE:
                picking.message_post(body="🏁 Finalizado sin crear PO (No se detectaron items válidos o proveedores).")
                
    except Exception as e:
        picking.message_post(body=f"🔥 <b>ERROR FATAL EN SCRIPT:</b> {str(e)}")