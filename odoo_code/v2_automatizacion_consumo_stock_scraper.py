
# =============================================================================
# Automatización de Consumo de stock Scraper - V2 (Multi-proveedor)
# =============================================================================
# 
# CONFIGURACIÓN EN ODOO:
# ----------------------
# Modelo: Transferir (stock.picking)
# Activar: Al actualizar (después de validar)
# 
# Dominio / Filtro:
#   - Tipo de operación = Recepción
#   - Estado = Hecho
#   - Contacto (partner_id) está en:
#       [PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ, 
#        SERVICIOS VIALES DE SANTA FE S A, 
#        BLUECAR S.A]
#
# NOTA: Este script se ejecuta cuando se valida una recepción de mercadería
#       de cualquiera de los proveedores de scraping.
#
# =============================================================================

# --- CONFIGURACIÓN DE SCRAPERS ---
SCRAPING_LOCATIONS = {
    'PR - Scraping': 'PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ',
    'SV - Scraping': 'SERVICIOS VIALES DE SANTA FE S A',
    # 'Bluecar - Scraping': 'BLUECAR S.A',  # TODO: Habilitar cuando Bluecar esté listo
}
SCRAPING_SUFFIX = '- Scraping'
SCRAPING_PROVIDERS = list(SCRAPING_LOCATIONS.values())
DEBUG_MODE = True

# --- EJECUCIÓN ---
# Iteramos sobre la Recepción de mercadería que se acaba de validar
for picking_in in records:
    try:
        # 0. VERIFICAR QUE ES UN PROVEEDOR DE SCRAPING
        proveedor_nombre = picking_in.partner_id.name if picking_in.partner_id else ''
        
        # Buscar si el proveedor está en nuestra lista de scrapers
        es_proveedor_scraper = False
        ubicacion_scraper = None
        
        for ubicacion, proveedor in SCRAPING_LOCATIONS.items():
            if proveedor == proveedor_nombre or proveedor in proveedor_nombre or proveedor_nombre in proveedor:
                es_proveedor_scraper = True
                ubicacion_scraper = ubicacion
                break
        
        if not es_proveedor_scraper:
            if DEBUG_MODE:
                picking_in.message_post(body=f"ℹ️ Proveedor '{proveedor_nombre}' no es de scraping. Script no aplica.")
            continue
            
        if DEBUG_MODE:
            picking_in.message_post(body=f"🤖 <b>Iniciando Cross-Docking Multi-Scraper v2...</b><br/>Proveedor detectado: {proveedor_nombre}<br/>Ubicación scraper asociada: {ubicacion_scraper}")

        # 1. OBTENER EL PICKING DE SALIDA ORIGINAL
        if not picking_in.origin:
            if DEBUG_MODE: 
                picking_in.message_post(body="⚠️ La recepción no tiene documento origen (PO).")
            continue
            
        po_name = picking_in.origin
        po = env['purchase.order'].search([('name', '=', po_name)], limit=1)
        
        if not po:
            if DEBUG_MODE: 
                picking_in.message_post(body=f"⚠️ No se encontró la PO {po_name}.")
            continue
            
        picking_out_name = po.origin
        
        if not picking_out_name:
            if DEBUG_MODE: 
                picking_in.message_post(body="⚠️ La PO no indica a qué Picking de salida pertenece.")
            continue
        
        # Limpiar el nombre (puede venir con espacios o caracteres extra)
        picking_out_name = picking_out_name.strip()
            
        # Buscamos el Picking de Salida real (que no esté done ni cancelado)
        picking_out = env['stock.picking'].search([
            ('name', '=', picking_out_name),
            ('state', 'not in', ['done', 'cancel']) 
        ], limit=1)
        
        if not picking_out:
            # Intentar búsqueda más flexible
            picking_out = env['stock.picking'].search([
                ('name', 'ilike', picking_out_name),
                ('state', 'not in', ['done', 'cancel']) 
            ], limit=1)
        
        if not picking_out:
            if DEBUG_MODE: 
                picking_in.message_post(body=f"ℹ️ No se encontró picking salida pendiente '{picking_out_name}'. Puede que ya esté procesado.")
            continue

        if DEBUG_MODE:
            link_picking = f"<a href='#' data-oe-model='stock.picking' data-oe-id='{picking_out.id}'>{picking_out.name}</a>"
            picking_in.message_post(body=f"🔎 Picking de salida detectado: {link_picking}")

        # 2. BUSCAR Y CORREGIR LÍNEAS DE SCRAPER
        lines_fixed = []
        
        # Iteramos las líneas del Picking de SALIDA
        for line in picking_out.move_line_ids_without_package:
            
            # Verificamos si la línea está reservada en una ubicación de scraping
            if line.location_id and SCRAPING_SUFFIX in line.location_id.name:
                
                ubicacion_actual = line.location_id.name
                prod_name = line.product_id.name
                prod_code = line.product_id.default_code or ''
                
                # Obtener cantidad
                qty_real = line.quantity
                if qty_real == 0 and hasattr(line, 'reserved_uom_qty'):
                    qty_real = line.reserved_uom_qty
                elif qty_real == 0 and hasattr(line, 'product_uom_qty'):
                    qty_real = line.product_uom_qty

                # --- LA MAGIA: CAMBIAR LA UBICACIÓN ---
                # Tomamos la ubicación donde entró la mercadería (normalmente DEPO Existencias)
                nueva_ubicacion = picking_in.location_dest_id
                
                if DEBUG_MODE:
                    picking_in.message_post(body=f"🔄 Moviendo [{prod_code}] {prod_name}: {ubicacion_actual} → {nueva_ubicacion.name}")
                
                # Actualizamos la ubicación de origen de la línea de salida
                line.write({'location_id': nueva_ubicacion.id})
                
                lines_fixed.append({
                    'producto': prod_name,
                    'codigo': prod_code,
                    'cantidad': qty_real,
                    'desde': ubicacion_actual,
                    'hacia': nueva_ubicacion.name,
                })

        # 3. REPORTE FINAL
        if lines_fixed:
            msg = f"""
            ✅ <b>STOCK ASIGNADO AUTOMÁTICAMENTE (Multi-Scraper v2)</b><br/><br/>
            Al recibir la mercadería de <b>{proveedor_nombre}</b>, se detectó que el pedido 
            <a href='#' data-oe-model='stock.picking' data-oe-id='{picking_out.id}'>{picking_out.name}</a> 
            la estaba esperando.<br/><br/>
            <b>Se ha actualizado la reserva:</b><br/>
            <ul>
            """
            
            for item in lines_fixed:
                msg += f"<li>{item['cantidad']} u. de [{item['codigo']}] {item['producto']} ({item['desde']} → {item['hacia']})</li>"
            
            msg += "</ul>"
            
            # Avisamos en la Recepción
            picking_in.message_post(body=msg, message_type='comment', subtype_xmlid='mail.mt_note')
            
            # Avisamos en la Salida
            picking_out.message_post(body=msg, message_type='comment', subtype_xmlid='mail.mt_note')
            
            # También avisar en la venta si existe
            venta = picking_out.sale_id
            if not venta and picking_out.origin:
                venta = env['sale.order'].search([('name', '=', picking_out.origin)], limit=1)
            
            if venta:
                venta.message_post(body=msg, message_type='comment', subtype_xmlid='mail.mt_note')
            
        else:
            if DEBUG_MODE:
                picking_in.message_post(body=f"🏁 No se encontraron líneas en ubicaciones de scraping dentro del pedido de salida {picking_out.name}.")

    except Exception as e:
        picking_in.message_post(body=f"🔥 <b>ERROR CRÍTICO:</b> {str(e)}")
