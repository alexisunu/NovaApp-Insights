import os
import sys
from django.core.management.base import BaseCommand
from dashboard.orquestador import ejecutar_pipeline
from dashboard.models import StgRechazos
from django.db.models import Count

class Command(BaseCommand):
    help = "Ejecuta el pipeline completo de ETL e inserta los datos en la base de datos."

    def add_arguments(self, parser):
        parser.add_argument('--excel', type=str, help='Ruta al archivo Excel de origen')
        parser.add_argument('--csv', type=str, help='Ruta al archivo CSV de origen')

    def handle(self, *args, **options):
        ruta_excel = options.get('excel') or os.environ.get('NOVAAPP_EXCEL')
        ruta_csv = options.get('csv') or os.environ.get('NOVAAPP_CSV')

        if not ruta_excel or not ruta_csv:
            self.stderr.write(
                "Error: Debes proporcionar las rutas de los archivos mediante --excel y --csv, "
                "o usando las variables de entorno NOVAAPP_EXCEL y NOVAAPP_CSV."
            )
            sys.exit(1)

        try:
            ejecucion, tiempos = ejecutar_pipeline(ruta_excel, ruta_csv)
            
            # Print results (NO PII)
            self.stdout.write(f"\n--- Ejecución {ejecucion.id} completada con estado: {ejecucion.estado} ---")
            self.stdout.write("\nResumen por tabla:")
            
            filas_por_tabla = ejecucion.filas_por_tabla or {}
            for tabla in ["dim_plan", "dim_tiempo", "dim_cuenta", "fact_suscripciones", "fact_uso"]:
                datos = filas_por_tabla.get(tabla, {})
                ext = datos.get('extraidas', 0)
                ins = datos.get('insertadas', 0)
                act = datos.get('actualizadas', 0)
                sin = datos.get('sin_cambio', 0)
                self.stdout.write(f"  {tabla}: {ext} extraídas | {ins} insertadas | {act} actualizadas | {sin} sin cambio")
                
            self.stdout.write("\nTotales de inserción:")
            self.stdout.write(f"  Insertadas: {ejecucion.filas_insertadas}")
            self.stdout.write(f"  Actualizadas: {ejecucion.filas_actualizadas}")
            self.stdout.write(f"  Sin cambio: {ejecucion.filas_sin_cambio}")
            
            self.stdout.write("\nConciliación:")
            self.stdout.write(
                f"  {ejecucion.filas_extraidas} extraídas - "
                f"{ejecucion.filas_duplicadas_eliminadas} duplicadas - "
                f"{ejecucion.filas_cuarentena} cuarentena = "
                f"{ejecucion.filas_validas} válidas"
            )
            
            self.stdout.write("\nCuarentena actual en base de datos:")
            cuarentena = StgRechazos.objects.values('tabla_origen', 'motivo_rechazo').annotate(total=Count('id')).order_by('tabla_origen', '-total')
            for c in cuarentena:
                self.stdout.write(f"  {c['tabla_origen']} | {c['motivo_rechazo']}: {c['total']} filas")
                
            self.stdout.write("\nTiempos de ejecución:")
            for etapa, segs in tiempos.items():
                if etapa != 'total':
                    self.stdout.write(f"  {etapa.capitalize()}: {segs:.2f}s")
            self.stdout.write(f"  TOTAL: {tiempos.get('total', 0):.2f}s\n")
            
        except Exception as e:
            self.stderr.write(f"Error en el pipeline ETL: {str(e)}")
            sys.exit(1)
