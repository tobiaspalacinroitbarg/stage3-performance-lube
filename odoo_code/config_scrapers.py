# -*- coding: utf-8 -*-
"""
Configuración centralizada para automatizaciones de Scrapers
=============================================================
Este archivo contiene la configuración de ubicaciones y proveedores
para todas las automatizaciones relacionadas con scrapers.

IMPORTANTE: Este archivo es solo de REFERENCIA. 
La configuración debe copiarse al inicio de cada script de automatización en Odoo.
"""

# --- CONFIGURACIÓN DE SCRAPERS ---
# Mapeo: Nombre de ubicación en Odoo -> Nombre del proveedor en Odoo
SCRAPING_LOCATIONS = {
    'PR - Scraping': 'PR SH DE OLIVEIRA ROBERTO Y JUAN QUIROZ',
    'SV - Scraping': 'SERVICIOS VIALES DE SANTA FE S A',
    'Bluecar - Scraping': 'BLUECAR S.A',
}

# Sufijo común para detectar ubicaciones de scraping
SCRAPING_SUFFIX = '- Scraping'

# Lista de nombres de proveedores de scraping (para filtros de Odoo)
SCRAPING_PROVIDERS = list(SCRAPING_LOCATIONS.values())

# --- CONFIGURACIÓN DE PLANTILLAS ---
ID_PLANTILLA_MAIL = 59  # ID de la plantilla de correo en Odoo

# --- MODO DEBUG ---
DEBUG_MODE = True  # Cambiar a False en producción
