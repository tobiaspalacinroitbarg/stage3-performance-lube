#!/usr/bin/env python3
"""
Gestor de archivos CSV del scraper PrAutoParte
Utilidades para manejar archivos CSV con fechas
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import pandas as pd
from typing import List, Optional

class CSVManager:
    """Gestor de archivos CSV del scraper"""
    
    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        self.csv_pattern = "articulos_*.csv"
    
    def get_csv_files(self) -> List[Path]:
        """Obtener lista de archivos CSV del scraper ordenados por fecha"""
        csv_files = list(self.base_dir.glob(self.csv_pattern))
        return sorted(csv_files, key=lambda x: x.stat().st_mtime, reverse=True)
    
    def get_latest_csv(self) -> Optional[Path]:
        """Obtener el archivo CSV más reciente"""
        csv_files = self.get_csv_files()
        return csv_files[0] if csv_files else None
    
    def get_csv_by_date(self, date_str: str) -> Optional[Path]:
        """Obtener CSV por fecha específica (YYYY-MM-DD)"""
        filename = f"articulos_{date_str}.csv"
        filepath = self.base_dir / filename
        return filepath if filepath.exists() else None
    
    def list_csv_files(self) -> None:
        """Listar todos los archivos CSV disponibles"""
        csv_files = self.get_csv_files()
        
        if not csv_files:
            print("❌ No se encontraron archivos CSV")
            return
        
        print(f"📄 Archivos CSV encontrados ({len(csv_files)}):")
        print("-" * 60)
        
        for i, csv_file in enumerate(csv_files, 1):
            # Obtener información del archivo
            stat = csv_file.stat()
            size_mb = stat.st_size / (1024 * 1024)
            mod_time = datetime.fromtimestamp(stat.st_mtime)
            
            # Contar líneas (aproximado)
            try:
                with open(csv_file, 'r', encoding='utf-8') as f:
                    line_count = sum(1 for _ in f) - 1  # -1 para excluir header
            except:
                line_count = "?"
            
            print(f"{i:2d}. {csv_file.name}")
            print(f"    📅 Modificado: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    📊 Registros: {line_count}")
            print(f"    💾 Tamaño: {size_mb:.2f} MB")
            print()
    
    def compare_csv_files(self, date1: str, date2: str) -> None:
        """Comparar dos archivos CSV por fecha"""
        csv1 = self.get_csv_by_date(date1)
        csv2 = self.get_csv_by_date(date2)
        
        if not csv1:
            print(f"❌ Archivo no encontrado: articulos_{date1}.csv")
            return
            
        if not csv2:
            print(f"❌ Archivo no encontrado: articulos_{date2}.csv")
            return
        
        try:
            df1 = pd.read_csv(csv1)
            df2 = pd.read_csv(csv2)
            
            print(f"📊 Comparación entre {date1} y {date2}:")
            print("-" * 50)
            print(f"📄 {date1}: {len(df1)} registros")
            print(f"📄 {date2}: {len(df2)} registros")
            print(f"📈 Diferencia: {len(df2) - len(df1):+d} registros")
            
            # Comparar por marca si existe la columna
            if 'marca' in df1.columns and 'marca' in df2.columns:
                marcas1 = df1['marca'].value_counts()
                marcas2 = df2['marca'].value_counts()
                
                print(f"\n🏷️  Marcas en {date1}: {len(marcas1)}")
                print(f"🏷️  Marcas en {date2}: {len(marcas2)}")
                print(f"🏷️  Diferencia: {len(marcas2) - len(marcas1):+d} marcas")
                
        except Exception as e:
            print(f"❌ Error comparando archivos: {e}")
    
    def cleanup_old_csv(self, days_to_keep: int = 7) -> None:
        """Limpiar archivos CSV antiguos"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        csv_files = self.get_csv_files()
        
        deleted_count = 0
        
        for csv_file in csv_files:
            mod_time = datetime.fromtimestamp(csv_file.stat().st_mtime)
            
            if mod_time < cutoff_date:
                try:
                    csv_file.unlink()
                    print(f"🗑️  Eliminado: {csv_file.name}")
                    deleted_count += 1
                except Exception as e:
                    print(f"❌ Error eliminando {csv_file.name}: {e}")
        
        if deleted_count == 0:
            print(f"✅ No hay archivos CSV antiguos (>{days_to_keep} días) para eliminar")
        else:
            print(f"✅ Eliminados {deleted_count} archivos CSV antiguos")
    
    def get_csv_info(self, date_str: Optional[str] = None) -> None:
        """Obtener información detallada de un CSV"""
        if date_str:
            csv_file = self.get_csv_by_date(date_str)
        else:
            csv_file = self.get_latest_csv()
        
        if not csv_file:
            print("❌ Archivo CSV no encontrado")
            return
        
        try:
            df = pd.read_csv(csv_file)
            
            print(f"📄 Información de {csv_file.name}:")
            print("-" * 50)
            print(f"📊 Total registros: {len(df):,}")
            print(f"📋 Columnas: {len(df.columns)}")
            print(f"💾 Tamaño archivo: {csv_file.stat().st_size / (1024*1024):.2f} MB")
            
            # Información por columna
            print(f"\n📝 Columnas disponibles:")
            for col in df.columns:
                non_null = df[col].notna().sum()
                print(f"   • {col}: {non_null:,} valores ({non_null/len(df)*100:.1f}%)")
            
            # Top marcas
            if 'marca' in df.columns:
                top_marcas = df['marca'].value_counts().head(10)
                print(f"\n🏷️  Top 10 marcas:")
                for marca, count in top_marcas.items():
                    print(f"   • {marca}: {count:,} productos")
            
        except Exception as e:
            print(f"❌ Error leyendo archivo: {e}")

def main():
    parser = argparse.ArgumentParser(description="Gestor de archivos CSV del scraper PrAutoParte")
    parser.add_argument("--dir", "-d", default=".", help="Directorio base (default: .)")
    
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponibles")
    
    # Comando list
    subparsers.add_parser("list", help="Listar archivos CSV")
    
    # Comando info
    info_parser = subparsers.add_parser("info", help="Información de un CSV")
    info_parser.add_argument("date", nargs="?", help="Fecha (YYYY-MM-DD) o último si no se especifica")
    
    # Comando compare
    compare_parser = subparsers.add_parser("compare", help="Comparar dos archivos CSV")
    compare_parser.add_argument("date1", help="Primera fecha (YYYY-MM-DD)")
    compare_parser.add_argument("date2", help="Segunda fecha (YYYY-MM-DD)")
    
    # Comando cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="Limpiar archivos antiguos")
    cleanup_parser.add_argument("--days", "-d", type=int, default=7, help="Días a mantener (default: 7)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    csv_manager = CSVManager(args.dir)
    
    if args.command == "list":
        csv_manager.list_csv_files()
    elif args.command == "info":
        csv_manager.get_csv_info(args.date)
    elif args.command == "compare":
        csv_manager.compare_csv_files(args.date1, args.date2)
    elif args.command == "cleanup":
        csv_manager.cleanup_old_csv(args.days)

if __name__ == "__main__":
    main()