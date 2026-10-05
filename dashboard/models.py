from django.db import models


# ───────────── DIMENSIONES ─────────────

class DimPlan(models.Model):
    plan_id = models.IntegerField(primary_key=True)
    nombre_plan = models.CharField(max_length=100)
    precio_mensual = models.DecimalField(max_digits=10, decimal_places=2)
    limite_usuarios = models.IntegerField()

    class Meta:
        db_table = "dim_plan"


class DimTiempo(models.Model):
    fecha_id = models.IntegerField(primary_key=True)  # AAAAMMDD
    fecha = models.DateField()
    anio = models.IntegerField()
    mes = models.IntegerField()
    mes_orden = models.IntegerField()
    dia = models.IntegerField()
    dia_semana_orden = models.IntegerField()
    nombre_mes = models.CharField(max_length=20)
    dia_semana = models.CharField(max_length=20)
    trimestre = models.CharField(max_length=2)  # "T1"
    es_finde = models.BooleanField(default=False)

    class Meta:
        db_table = "dim_tiempo"


class DimCuenta(models.Model):
    cuenta_id = models.IntegerField(primary_key=True)
    nit_hash = models.CharField(max_length=64, unique=True)
    zona = models.CharField(max_length=100, blank=True, default="")
    industria = models.CharField(max_length=100, blank=True, default="")
    canal_adquisicion = models.CharField(max_length=100, blank=True, default="")
    fecha_registro_inconsistente = models.BooleanField(default=False)

    class Meta:
        db_table = "dim_cuenta"


# ───────────── HECHOS ─────────────

class FactSuscripciones(models.Model):
    sub_id = models.IntegerField(primary_key=True)
    cuenta = models.ForeignKey(DimCuenta, on_delete=models.PROTECT)
    fecha = models.ForeignKey(DimTiempo, on_delete=models.PROTECT)
    plan = models.ForeignKey(DimPlan, on_delete=models.PROTECT)
    mrr = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mrr_original = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    usuarios_activos = models.IntegerField(null=True, blank=True)
    mrr_corregido = models.BooleanField(default=False)
    usuarios_activos_faltante = models.BooleanField(default=False)
    excede_limite = models.BooleanField(default=False)
    churn = models.BooleanField(default=False)

    class Meta:
        db_table = "fact_suscripciones"
        constraints = [
            models.UniqueConstraint(fields=["cuenta", "fecha"], name="uq_suscripcion_cuenta_fecha")
        ]


class FactUso(models.Model):
    uso_id = models.IntegerField(primary_key=True)
    cuenta = models.ForeignKey(DimCuenta, on_delete=models.PROTECT)
    fecha = models.ForeignKey(DimTiempo, on_delete=models.PROTECT)
    feature = models.CharField(max_length=100)
    duracion_min = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    duracion_min_faltante = models.BooleanField(default=False)

    class Meta:
        db_table = "fact_uso"


# ───────────── AUDITORÍA Y CONTROL ─────────────

class EjecucionETL(models.Model):
    ESTADOS = [("EXITOSA", "Exitosa"), ("FALLIDA", "Fallida"), ("EN_CURSO", "En curso")]

    fecha_inicio = models.DateTimeField()
    fecha_fin = models.DateTimeField(null=True, blank=True)
    duracion_segundos = models.FloatField(null=True, blank=True)
    archivo_excel = models.CharField(max_length=255, blank=True, default="")
    archivo_csv = models.CharField(max_length=255, blank=True, default="")
    filas_extraidas = models.IntegerField(default=0)
    filas_duplicadas_eliminadas = models.IntegerField(default=0)
    filas_cuarentena = models.IntegerField(default=0)
    filas_validas = models.IntegerField(default=0)
    filas_insertadas = models.IntegerField(default=0)
    filas_actualizadas = models.IntegerField(default=0)
    filas_sin_cambio = models.IntegerField(default=0)
    estado = models.CharField(max_length=20, choices=ESTADOS, default="EN_CURSO")
    resumen_privacidad = models.JSONField(default=dict, blank=True)
    filas_por_tabla = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "ejecucion_etl"
        ordering = ["-fecha_inicio"]


class StgRechazos(models.Model):
    # Sin FKs a propósito: guarda registros que no son válidos
    ejecucion = models.ForeignKey(EjecucionETL, on_delete=models.SET_NULL, null=True, blank=True)
    tabla_origen = models.CharField(max_length=50)
    id_origen = models.CharField(max_length=50)
    cuenta_id = models.IntegerField(null=True, blank=True)
    fecha_id = models.IntegerField(null=True, blank=True)
    motivo_rechazo = models.CharField(max_length=255)
    fila_original = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "stg_rechazos"
        constraints = [
            models.UniqueConstraint(fields=["tabla_origen", "id_origen"], name="uq_rechazo_origen")
        ]


class BitacoraCambios(models.Model):
    DECISIONES = [
        ("Corregir", "Corregir"),
        ("Eliminar", "Eliminar"),
        ("Marcar", "Marcar"),
        ("Cuarentena", "Cuarentena"),
        ("No tocar", "No tocar"),
        ("Anonimizar", "Anonimizar"),
    ]

    ejecucion = models.ForeignKey(EjecucionETL, on_delete=models.CASCADE, related_name="bitacora")
    fecha_hora = models.DateTimeField(auto_now_add=True)
    dimension_calidad = models.CharField(max_length=50)
    tabla_afectada = models.CharField(max_length=50)
    regla_aplicada = models.TextField()
    filas_afectadas = models.IntegerField(default=0)
    decision = models.CharField(max_length=20, choices=DECISIONES)
    justificacion = models.TextField(blank=True, default="")
    responsable = models.CharField(max_length=50, blank=True, default="")
    revisor = models.CharField(max_length=50, blank=True, default="")
    detalle_cambios = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "bitacora_cambios"
        ordering = ["-fecha_hora"]