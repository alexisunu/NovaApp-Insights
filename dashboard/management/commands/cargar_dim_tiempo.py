from datetime import date, timedelta
from django.core.management.base import BaseCommand
from dashboard.models import DimTiempo

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class Command(BaseCommand):
    help = "Genera dim_tiempo (2022-01-01 a 2025-12-31). Se puede correr varias veces."

    def handle(self, *args, **options):
        d, fin, filas = date(2022, 1, 1), date(2025, 12, 31), []
        while d <= fin:
            wd = d.weekday()  # lunes = 0
            filas.append(DimTiempo(
                fecha_id=int(d.strftime("%Y%m%d")),
                fecha=d,
                anio=d.year,
                mes=d.month,
                mes_orden=d.month,
                dia=d.day,
                dia_semana_orden=wd + 1,  # lunes = 1
                nombre_mes=MESES[d.month - 1],
                dia_semana=DIAS[wd],
                trimestre=f"T{(d.month - 1) // 3 + 1}",
                es_finde=wd >= 5,
            ))
            d += timedelta(days=1)
        DimTiempo.objects.bulk_create(filas, ignore_conflicts=True, batch_size=1000)
        self.stdout.write(f"dim_tiempo: {DimTiempo.objects.count()} filas")