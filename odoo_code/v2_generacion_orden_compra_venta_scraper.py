
# =============================================================================
# Generación de O.C. por venta desde Scraper - V2 (Multi-proveedor)
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
# IMPORTANTE:
# -----------
# Este script crea UNA orden de compra por CADA proveedor de scraping involucrado.
# El proveedor se determina por la UBICACIÓN de origen, NO por el seller_ids del producto.
#
# =============================================================================

# --- CONFIGURACIÓN DE SCRAPERS ---
SCRAPING_LOCATIONS = {
    'PR - Scraping': 'PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ',
    'SV - Scraping': 'SERVICIOS VIALES DE SANTA FE S A',
    # 'Bluecar - Scraping': 'BLUECAR S.A',  # TODO: Habilitar cuando Bluecar esté listo
}
SCRAPING_SUFFIX = '- Scraping'
DEBUG_MODE = True

# --- EJECUCIÓN ---
for picking in records:
    try:
        if DEBUG_MODE:
            picking.message_post(body="🤖 <b>Iniciando Script de Reposición Multi-Scraper v2...</b>")

        # 1. VERIFICAR DUPLICADOS
        # Buscamos si ya hay POs creadas con este Picking como origen
        pos_existentes = env['purchase.order'].search([
            ('origin', 'ilike', picking.name),
            ('state', '!=', 'cancel')
        ])
        
        if pos_existentes:
            if DEBUG_MODE:
                nombres_po = ', '.join(pos_existentes.mapped('name'))
                picking.message_post(body=f"⚠️ Ya existen POs para este picking: {nombres_po}. Verificando si faltan proveedores...")
            # Obtener proveedores de POs existentes para no duplicar
            proveedores_ya_creados = set(pos_existentes.mapped('partner_id.name'))
        else:
            proveedores_ya_creados = set()

        # Diccionario: { proveedor_partner: {producto: cantidad} }
        compras_por_proveedor = {}
        hay_items = False

        # 2. RECORRER LÍNEAS DEL PICKING
        lines_to_check = picking.move_line_ids if picking.move_line_ids else picking.move_line_ids_without_package
        
        for line in lines_to_check:
            ubicacion_nombre = line.location_id.name if line.location_id else ''
            
            # Verificar si es una ubicación de scraping
            if SCRAPING_SUFFIX in ubicacion_nombre:
                
                # Obtener el nombre del proveedor desde la configuración
                proveedor_nombre = SCRAPING_LOCATIONS.get(ubicacion_nombre)
                
                if not proveedor_nombre:
                    if DEBUG_MODE:
                        picking.message_post(body=f"⚠️ Ubicación '{ubicacion_nombre}' no está configurada en SCRAPING_LOCATIONS. Se omite.")
                    continue
                
                # Verificar si ya existe PO para este proveedor
                if proveedor_nombre in proveedores_ya_creados:
                    if DEBUG_MODE:
                        picking.message_post(body=f"ℹ️ Ya existe PO para {proveedor_nombre}. Omitiendo línea de {line.product_id.name}.")
                    continue
                
                # Obtener cantidad
                qty = line.quantity
                if qty == 0:
                    if hasattr(line, 'reserved_uom_qty'):
                        qty = line.reserved_uom_qty
                    elif hasattr(line, 'product_uom_qty'):
                        qty = line.product_uom_qty
                
                if DEBUG_MODE:
                    picking.message_post(body=f"🔎 [{ubicacion_nombre}] {line.product_id.name} - Cantidad: {qty}")

                if qty > 0:
                    prod = line.product_id
                    
                    # Buscar el partner (proveedor) por nombre
                    partner = env['res.partner'].search([
                        ('name', '=', proveedor_nombre),
                        ('supplier_rank', '>', 0)
                    ], limit=1)
                    
                    if not partner:
                        # Intentar búsqueda parcial
                        partner = env['res.partner'].search([
                            ('name', 'ilike', proveedor_nombre),
                            ('supplier_rank', '>', 0)
                        ], limit=1)
                    
                    if partner:
                        hay_items = True
                        if partner not in compras_por_proveedor:
                            compras_por_proveedor[partner] = {}
                        
                        # Acumular cantidad
                        if prod in compras_por_proveedor[partner]:
                            compras_por_proveedor[partner][prod] += qty
                        else:
                            compras_por_proveedor[partner][prod] = qty
                    else:
                        picking.message_post(body=f"⚠️ No se encontró el proveedor '{proveedor_nombre}' en Odoo. Verificar configuración.")

        # 3. CREAR ÓRDENES DE COMPRA (una por proveedor)
        if hay_items:
            pos_creadas = []
            
            for partner, productos in compras_por_proveedor.items():
                
                # A. Crear Cabecera de PO
                # El origin incluye el picking + identificador del proveedor para tracking
                po = env['purchase.order'].create({
                    'partner_id': partner.id,
                    'origin': f"{picking.name}",
                    'date_order': datetime.datetime.now(),
                    'company_id': picking.company_id.id,
                })
                
                # B. Crear Líneas
                for product, cantidad in productos.items():
                    # Intentar obtener precio del proveedor
                    precio = product.standard_price
                    seller = product.seller_ids.filtered(lambda s: s.partner_id == partner)
                    if seller:
                        precio = seller[0].price or product.standard_price
                    
                    env['purchase.order.line'].create({
                        'order_id': po.id,
                        'product_id': product.id,
                        'product_qty': cantidad,
                        'price_unit': precio,
                        'date_planned': datetime.datetime.now(),
                        'name': product.name,
                        'product_uom': product.uom_id.id,
                    })
                
                pos_creadas.append({
                    'po': po,
                    'partner': partner,
                    'productos': len(productos),
                })

            # 4. LOGS Y LINKS
            mensaje = "✅ <b>REPOSICIÓN AUTOMÁTICA (Multi-Scraper v2):</b><br/><br/>"
            mensaje += f"Se generaron <b>{len(pos_creadas)}</b> órdenes de compra:<br/><ul>"
            
            for info in pos_creadas:
                link_po = f"<a href='#' data-oe-model='purchase.order' data-oe-id='{info['po'].id}'>{info['po'].name}</a>"
                mensaje += f"<li>{link_po} - <b>{info['partner'].name}</b> ({info['productos']} productos)</li>"
            
            mensaje += "</ul>"

            # A. Log en Picking
            picking.message_post(body=mensaje, message_type='comment', subtype_xmlid='mail.mt_note')

            # B. Log en Venta (búsqueda robusta)
            venta = picking.sale_id
            if not venta and picking.origin:
                venta = env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                if not venta:
                    venta = env['sale.order'].search([('name', 'ilike', picking.origin.split()[0])], limit=1)

            if venta:
                venta.message_post(body=mensaje, message_type='comment', subtype_xmlid='mail.mt_note')
                
        else:
            if DEBUG_MODE:
                picking.message_post(body="🏁 Finalizado sin crear PO (No se detectaron items de scraper válidos o proveedores).")
                
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        picking.message_post(body=f"🔥 <b>ERROR FATAL EN SCRIPT:</b> {str(e)}<br/><pre>{error_detail}</pre>")
