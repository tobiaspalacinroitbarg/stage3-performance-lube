
# Automatización de Consumo de stock Scraper
# Modelo?
# Transferir 
# Activar?

# El estado está establecido como
# Hecho
# Antes de actualizar el dominio?
# Conciliar todos los registros
# Aplicar en?
# Conciliar
# todas
# de las siguientes reglas:
# Tipo de operación
# =
# Recepción
# Contacto ➔ Nombre
# =
# PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ
# Estado
# =
# Hecho

# --- CONFIGURACIÓN ---
DEBUG_MODE = True

# Iteramos sobre la Recepción de mercadería que se acaba de validar
for picking_in in records:
    try:
        if DEBUG_MODE:
            picking_in.message_post(body="🤖 <b>Iniciando Cross-Docking (Corrección de Salida)...</b>")

        # 1. OBTENER EL PICKING DE SALIDA ORIGINAL
        if not picking_in.origin:
            if DEBUG_MODE: picking_in.message_post(body="⚠️ La recepción no tiene documento origen (PO).")
            continue
            
        po_name = picking_in.origin
        po = env['purchase.order'].search([('name', '=', po_name)], limit=1)
        
        if not po:
            if DEBUG_MODE: picking_in.message_post(body=f"⚠️ No se encontró la PO {po_name}.")
            continue
            
        picking_out_name = po.origin
        
        if not picking_out_name:
            if DEBUG_MODE: picking_in.message_post(body="⚠️ La PO no indica a qué Picking de salida pertenece.")
            continue
            
        # Buscamos el Picking de Salida real
        picking_out = env['stock.picking'].search([
            ('name', '=', picking_out_name.strip()),
            ('state', 'not in', ['done', 'cancel']) 
        ], limit=1)
        
        if not picking_out:
            if DEBUG_MODE: 
                picking_in.message_post(body=f"ℹ️ No se encontró picking salida pendiente '{picking_out_name}'.")
            continue

        if DEBUG_MODE:
             picking_in.message_post(body=f"🔎 Picking de salida detectado: <a href='#' data-oe-model='stock.picking' data-oe-id='{picking_out.id}'>{picking_out.name}</a>")

        # 2. BUSCAR Y CORREGIR LÍNEAS DE SCRAPER
        lines_fixed = []
        
        # Iteramos las líneas del Picking de SALIDA
        for line in picking_out.move_line_ids_without_package:
            
            # Verificamos si la línea está reservada en SCRAP
            if line.location_id and 'SCRAP' in line.location_id.complete_name.upper():
                
                prod_name = line.product_id.name
                
                # --- CORRECCIÓN AQUÍ: Usamos line.quantity ---
                # Si quantity es 0 (puede pasar en reservas), intentamos leer reserved_uom_qty si existe, sino 0
                qty_real = line.quantity
                if qty_real == 0 and hasattr(line, 'reserved_uom_qty'):
                    qty_real = line.reserved_uom_qty

                # --- LA MAGIA: CAMBIAR LA UBICACIÓN ---
                # Tomamos la ubicación donde entró la mercadería (normalmente DEPO)
                nueva_ubicacion = picking_in.location_dest_id.id
                
                # Actualizamos la ubicación de origen de la línea de salida
                line.write({'location_id': nueva_ubicacion})
                
                lines_fixed.append(f"<li>{qty_real} u. de {prod_name} (Movido de Scrap -> Depósito)</li>")

        # 3. REPORTE FINAL
        if lines_fixed:
            msg = f"""
            ✅ <b>STOCK ASIGNADO AUTOMÁTICAMENTE</b><br/>
            Al recibir la mercadería, se detectó que el pedido {picking_out.name} la estaba esperando.<br/>
            Se ha actualizado la reserva de Scrap a Depósito:<br/>
            <ul>{''.join(lines_fixed)}</ul>
            """
            # Avisamos en la Recepción
            picking_in.message_post(body=msg, message_type='comment', subtype_xmlid='mail.mt_note')
            
            # Avisamos en la Salida
            picking_out.message_post(body=msg, message_type='comment', subtype_xmlid='mail.mt_note')
            
        else:
            if DEBUG_MODE:
                picking_in.message_post(body="🏁 No se encontraron líneas en 'Scrap' dentro del pedido de salida para corregir.")

    except Exception as e:
        picking_in.message_post(body=f"🔥 <b>ERROR CRÍTICO:</b> {str(e)}")