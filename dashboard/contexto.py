from .models import (
    DimCuenta, DimPlan, DimTiempo, FactSuscripciones, FactUso, StgRechazos
)

TABLAS = [
    ("dim_plan", "Dimensión", "Un registro por plan comercial", "plan_id", "—", DimPlan),
    ("dim_tiempo", "Dimensión", "Un registro por día calendario", "fecha_id", "—", DimTiempo),
    ("dim_cuenta", "Dimensión", "Un registro por cuenta cliente", "cuenta_id", "—", DimCuenta),
    (
        "fact_suscripciones", "Hechos", "Una fila por cuenta y por mes (día 1)", "sub_id",
        "cuenta_id → dim_cuenta, fecha_id → dim_tiempo, plan_id → dim_plan", FactSuscripciones
    ),
    (
        "fact_uso", "Hechos", "Una fila por evento de uso", "uso_id",
        "cuenta_id → dim_cuenta, fecha_id → dim_tiempo", FactUso
    ),
]

FKS = [
    ("fact_suscripciones", "cuenta_id", "dim_cuenta"),
    ("fact_suscripciones", "fecha_id", "dim_tiempo"),
    ("fact_suscripciones", "plan_id", "dim_plan"),
    ("fact_uso", "cuenta_id", "dim_cuenta"),
    ("fact_uso", "fecha_id", "dim_tiempo"),
]


def contexto_modelo():
    tablas = [
        {
            "nombre": n,
            "tipo": t,
            "granularidad": g,
            "pk": pk,
            "fks": fks,
            "filas_cargadas": modelo.objects.count()
        }
        for n, t, g, pk, fks, modelo in TABLAS
    ]
    integridad = [
        {
            "tabla_hechos": tabla,
            "fk": fk,
            "dimension_destino": destino,
            "huerfanas": StgRechazos.objects.filter(
                tabla_origen=tabla,
                motivo_rechazo__icontains=fk
            ).count(),
            "tratamiento": "Cuarentena en stg_rechazos (no rompe la carga)"
        }
        for tabla, fk, destino in FKS
    ]
    return {"tablas": tablas, "integridad_referencial": integridad}