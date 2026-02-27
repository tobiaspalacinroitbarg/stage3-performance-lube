
# =============================================================================
# Correo de notificación de venta desde Scraper - V2 (Multi-proveedor)
# =============================================================================
# 
# CONFIGURACIÓN EN ODOO:
# ----------------------
# Modelo: Transferir (stock.picking)
# Activar: Antes de actualizar
# 
# Dominio / Filtro:
#   - Tipo de operación = TODO: Recolectar
#   - Estado = Disponible
#   - Operaciones -> Desde -> Nombre de la ubicación contiene "- Scraping"
#     (O alternativamente: está en [PR - Scraping, SV - Scraping, Bluecar - Scraping])
#
# =============================================================================

# --- CONFIGURACIÓN DE SCRAPERS ---
SCRAPING_LOCATIONS = {
    'PR - Scraping': 'PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ',
    'SV - Scraping': 'SERVICIOS VIALES DE SANTA FE S A',
    # 'Bluecar - Scraping': 'BLUECAR S.A',  # TODO: Habilitar cuando Bluecar esté listo
}
SCRAPING_SUFFIX = '- Scraping'
ID_PLANTILLA_MAIL = 59  # ID de la plantilla de correo en Odoo
DEBUG_MODE = True

# --- EJECUCIÓN ---
for picking in records:
    try:
        # Diccionario para agrupar items por proveedor
        # { 'nombre_proveedor': [lista de items] }
        items_por_proveedor = {}
        hay_scraper = False

        # 1. DETECTAR ITEMS DE SCRAPER Y AGRUPAR POR PROVEEDOR
        for move in picking.move_ids:
            for line in move.move_line_ids:
                if line.location_id and SCRAPING_SUFFIX in line.location_id.name:
                    ubicacion_nombre = line.location_id.name
                    
                    # Obtener el proveedor asociado a esta ubicación
                    proveedor_nombre = SCRAPING_LOCATIONS.get(ubicacion_nombre, ubicacion_nombre)
                    
                    # Obtener cantidad
                    qty = line.quantity
                    if qty == 0 and hasattr(line, 'reserved_uom_qty'):
                        qty = line.reserved_uom_qty
                    elif qty == 0 and hasattr(line, 'product_uom_qty'):
                        qty = line.product_uom_qty
                    
                    if qty > 0:
                        hay_scraper = True
                        
                        if proveedor_nombre not in items_por_proveedor:
                            items_por_proveedor[proveedor_nombre] = []
                        
                        items_por_proveedor[proveedor_nombre].append({
                            'producto': line.product_id.name,
                            'codigo': line.product_id.default_code or '',
                            'cantidad': qty,
                            'ubicacion': ubicacion_nombre,
                        })

        # 2. EJECUTAR ACCIONES SI HAY ITEMS DE SCRAPER
        if hay_scraper:
            
            # A. Construir mensaje detallado para el chatter
            mensaje_chatter = "⚠️ <b>AVISO SCRAPER - VENTA CON STOCK DE PROVEEDORES EXTERNOS</b><br/><br/>"
            
            for proveedor, items in items_por_proveedor.items():
                mensaje_chatter += f"<b>📦 {proveedor}:</b><br/>"
                for item in items:
                    mensaje_chatter += f"&nbsp;&nbsp;- {item['cantidad']} u. de [{item['codigo']}] {item['producto']}<br/>"
                mensaje_chatter += "<br/>"
            
            mensaje_chatter += f"<i>Total de proveedores a contactar: {len(items_por_proveedor)}</i>"
            
            # B. Enviar el Mail usando la plantilla
            template = env['mail.template'].browse(ID_PLANTILLA_MAIL)
            if template:
                template.send_mail(picking.id, force_send=True)
                if DEBUG_MODE:
                    picking.message_post(body="📧 Correo de notificación enviado.", message_type='comment', subtype_xmlid='mail.mt_note')

            # C. Dejar aviso en la VENTA (Sale Order)
            venta = picking.sale_id
            if not venta and picking.origin:
                venta = env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                
            if venta:
                venta.message_post(body=mensaje_chatter, message_type='comment', subtype_xmlid='mail.mt_note')
            else:
                # Si no hay venta, dejar el mensaje en el picking
                picking.message_post(body=mensaje_chatter, message_type='comment', subtype_xmlid='mail.mt_note')
                
        elif DEBUG_MODE:
            picking.message_post(body="🏁 No se detectaron items de scraper en este picking.", message_type='comment', subtype_xmlid='mail.mt_note')
            
    except Exception as e:
        picking.message_post(body=f"🔥 <b>ERROR EN SCRIPT DE NOTIFICACIÓN:</b> {str(e)}", message_type='comment', subtype_xmlid='mail.mt_note')
