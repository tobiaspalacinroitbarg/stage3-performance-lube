#!/usr/bin/env python3
"""
Runner que ejecuta todos los scrapers en serie.
Esto evita rate limiting de Odoo al no hacer requests en paralelo.

Orden de ejecución:
1. PR Scraper (PR Autopartes)
2. SV Scraper (Servicios Viales)
3. Replenishment Min/Max Update

Uso:
    python scripts/run_all_scrapers.py
    python scripts/run_all_scrapers.py --dry-run  # No escribe a Odoo
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Configuración
DELAY_BETWEEN_SCRAPERS = 60  # segundos de espera entre scrapers
SCRIPTS = [
    {
        "name": "PR Scraper",
        "command": ["python", "main.py"],
        "description": "Sincroniza stock de PR Autopartes",
    },
    {
        "name": "SV Scraper", 
        "command": ["python", "sv_scraper_v2.py"],
        "description": "Sincroniza stock de Servicios Viales",
    },
    {
        "name": "Replenishment Min/Max",
        "command": ["python", "scripts/update_replenishment_minmax.py"],
        "description": "Actualiza min/max de reabastecimiento",
    },
]


def log(msg: str):
    """Log con timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def run_script(script: dict, dry_run: bool = False) -> bool:
    """Ejecutar un script y retornar si fue exitoso"""
    name = script["name"]
    command = script["command"].copy()
    
    if dry_run and name != "PR Scraper":  # PR no tiene --dry-run
        command.append("--dry-run")
    
    log(f"=" * 70)
    log(f"INICIANDO: {name}")
    log(f"Descripción: {script['description']}")
    log(f"Comando: {' '.join(command)}")
    log(f"=" * 70)
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            command,
            cwd=Path(__file__).parent.parent,  # Directorio raíz del proyecto
            capture_output=False,  # Mostrar output en tiempo real
        )
        
        elapsed = time.time() - start_time
        elapsed_min = elapsed / 60
        
        if result.returncode == 0:
            log(f"COMPLETADO: {name} (tiempo: {elapsed_min:.1f} min)")
            return True
        else:
            log(f"ERROR: {name} terminó con código {result.returncode} (tiempo: {elapsed_min:.1f} min)")
            return False
            
    except Exception as e:
        elapsed = time.time() - start_time
        log(f"EXCEPCIÓN en {name}: {e} (tiempo: {elapsed/60:.1f} min)")
        return False


def main():
    dry_run = "--dry-run" in sys.argv
    
    log("=" * 70)
    log("SCRAPER RUNNER - Ejecución en serie")
    log(f"Modo: {'DRY-RUN (sin escribir a Odoo)' if dry_run else 'PRODUCCIÓN'}")
    log(f"Delay entre scrapers: {DELAY_BETWEEN_SCRAPERS}s")
    log("=" * 70)
    
    total_start = time.time()
    results = []
    
    for i, script in enumerate(SCRIPTS):
        success = run_script(script, dry_run)
        results.append((script["name"], success))
        
        # Delay entre scrapers (excepto después del último)
        if i < len(SCRIPTS) - 1:
            log(f"Esperando {DELAY_BETWEEN_SCRAPERS}s antes del siguiente scraper...")
            time.sleep(DELAY_BETWEEN_SCRAPERS)
    
    # Resumen final
    total_elapsed = time.time() - total_start
    total_min = total_elapsed / 60
    
    log("")
    log("=" * 70)
    log("RESUMEN DE EJECUCIÓN")
    log("=" * 70)
    
    all_success = True
    for name, success in results:
        status = "OK" if success else "FALLÓ"
        log(f"  {name}: {status}")
        if not success:
            all_success = False
    
    log(f"Tiempo total: {total_min:.1f} minutos")
    log("=" * 70)
    
    sys.exit(0 if all_success else 1)


if __name__ == "__main__":
    main()
