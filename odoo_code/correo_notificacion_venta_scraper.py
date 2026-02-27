
# Correo de notificación de venta desde Scraper
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
ID_PLANTILLA_MAIL = 59  # <--- ¡CAMBIA ESTE NÚMERO POR EL ID DE TU PLANTILLA!

for picking in records:
    hay_scraper = False
    items_scraper_texto = []

    # 1. DETECTAR ITEMS (Para el mensaje del Chatter)
    for move in picking.move_ids:
        for line in move.move_line_ids:
            if line.location_id and 'SCRAP' in line.location_id.complete_name.upper():
                qty = line.quantity
                if qty == 0 and hasattr(line, 'product_uom_qty'):
                    qty = line.product_uom_qty
                
                if qty > 0:
                    hay_scraper = True
                    items_scraper_texto.append(f"- {qty} u. de {line.product_id.name}")

    # 2. EJECUTAR ACCIONES
    if hay_scraper:
        
        # A. Enviar el Mail (Usando la plantilla bonita que creaste)
        template = env['mail.template'].browse(ID_PLANTILLA_MAIL)
        if template:
            template.send_mail(picking.id, force_send=True)

        # B. Dejar aviso en la VENTA (Sale Order)
        mensaje_chatter = "⚠️ **AVISO SCRAPER** <br/>Se enviaron correos de alerta. Items:<br/>" + "<br/>".join(items_scraper_texto)
        
        venta = picking.sale_id
        # Si no encuentra venta directa, busca por nombre
        if not venta and picking.origin:
            venta = env['sale.order'].search([('name', '=', picking.origin)], limit=1)
            
        if venta:
            venta.message_post(body=mensaje_chatter, message_type='comment', subtype_xmlid='mail.mt_note')
        else:
            picking.message_post(body=mensaje_chatter, message_type='comment', subtype_xmlid='mail.mt_note')
