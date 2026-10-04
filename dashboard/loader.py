import json
from decimal import Decimal

import pandas as pd
from django.db import transaction
from django.utils import timezone

from .models import (BitacoraCambios, DimCuenta, DimPlan, DimTiempo,
                     EjecucionETL, FactSuscripciones, FactUso, StgRechazos)

LOTE = 5000


def _norm(v):
    """Normaliza un valor para compararlo entre pandas y la base."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    if pd.isna(v):
        return None
    if isinstance(v, Decimal):
        v = float(v)
    if hasattr(v, "item"):  # tipos numpy -> tipos Python
        v = v.item()
    if isinstance(v, float):
        return round(v, 4)
    return v


def _int(v):
    v = _norm(v)
    return None if v is None else int(v)


def upsert(modelo, df):
    """Inserta lo nuevo, ignora lo idéntico y actualiza lo modificado (por PK)."""
    pk = modelo._meta.pk.attname
    campos = [f.attname for f in modelo._meta.concrete_fields if f.attname != pk]
    cols = [pk] + campos

    if df[pk].duplicated().any():
        raise ValueError(f"{modelo.__name__}: hay PKs repetidas en el DataFrame de entrada")

    existentes = {
        fila[0]: fila[1:]
        for fila in modelo.objects.values_list(*cols).iterator(chunk_size=10000)
    }

    nuevos, cambiados, sin_cambio = [], [], 0
    for reg in df[cols].to_dict("records"):
        reg = {k: _norm(v) for k, v in reg.items()}
        clave = reg[pk]
        if clave not in existentes:
            nuevos.append(modelo(**reg))
        else:
            actual = tuple(_norm(x) for x in existentes[clave])
            nuevo = tuple(reg[c] for c in campos)
            if actual == nuevo:
                sin_cambio += 1
            else:
                cambiados.append(modelo(**reg))

    modelo.objects.bulk_create(nuevos, batch_size=LOTE)
    if cambiados:
        modelo.objects.bulk_update(cambiados, campos, batch_size=LOTE)
    return {"insertadas": len(nuevos), "actualizadas": len(cambiados), "sin_cambio": sin_cambio}


def separar_huerfanos(df, tabla, pk, validos):
    """Devuelve (filas buenas, lista de StgRechazos) según las FKs válidas."""
    ok = pd.Series(True, index=df.index)
    motivo = pd.Series("", index=df.index)
    for col, ids in validos.items():
        falla = ok & ~df[col].isin(ids)
        motivo[falla] = f"{col} sin registro en la dimensión"
        ok &= ~falla

    rechazos = []
    for i, fila in df[~ok].iterrows():
        rechazos.append(StgRechazos(
            tabla_origen=tabla,
            id_origen=str(_norm(fila[pk])),
            cuenta_id=_int(fila.get("cuenta_id")),
            fecha_id=_int(fila.get("fecha_id")),
            motivo_rechazo=motivo[i],
            fila_original=json.loads(fila.to_json()),
        ))
    return df[ok], rechazos


def cargar(dfs, duplicadas_eliminadas=0):
    """
    dfs: {"plan": df, "cuenta": df, "suscripciones": df, "uso": df}
    Las columnas deben llamarse igual que los campos del modelo
    (cuenta_id, fecha_id, plan_id, plan_inicial_id, ...).
    """
    if not DimTiempo.objects.exists():
        raise RuntimeError("dim_tiempo está vacía: corre 'python manage.py cargar_dim_tiempo'")

    ej = EjecucionETL.objects.create(fecha_inicio=timezone.now())
    tot = {"insertadas": 0, "actualizadas": 0, "sin_cambio": 0}
    rechazos_por_tabla = {}
    extraidas = sum(len(df) for df in dfs.values())

    try:
        with transaction.atomic():
            def acumular(r):
                for k in tot:
                    tot[k] += r[k]

            # 1) DimPlan
            acumular(upsert(DimPlan, dfs["plan"]))
            planes = set(DimPlan.objects.values_list("plan_id", flat=True))
            fechas = set(DimTiempo.objects.values_list("fecha_id", flat=True))

            # 2) DimCuenta (su plan_inicial debe existir)
            buenas, rech = separar_huerfanos(dfs["cuenta"], "dim_cuenta", "cuenta_id",
                                             {"plan_inicial_id": planes})
            rechazos_por_tabla["dim_cuenta"] = rech
            acumular(upsert(DimCuenta, buenas))
            cuentas = set(DimCuenta.objects.values_list("cuenta_id", flat=True))

            # 3) FactSuscripciones
            buenas, rech = separar_huerfanos(dfs["suscripciones"], "fact_suscripciones", "sub_id",
                                             {"cuenta_id": cuentas, "fecha_id": fechas, "plan_id": planes})
            rechazos_por_tabla["fact_suscripciones"] = rech
            acumular(upsert(FactSuscripciones, buenas))

            # 4) FactUso
            buenas, rech = separar_huerfanos(dfs["uso"], "fact_uso", "uso_id",
                                             {"cuenta_id": cuentas, "fecha_id": fechas})
            rechazos_por_tabla["fact_uso"] = rech
            acumular(upsert(FactUso, buenas))

            # Cuarentena (la llave única evita acumular repetidos)
            n_cuarentena = 0
            for tabla, rech in rechazos_por_tabla.items():
                for r in rech:
                    r.ejecucion = ej
                StgRechazos.objects.bulk_create(rech, ignore_conflicts=True, batch_size=LOTE)
                n_cuarentena += len(rech)
                if rech:
                    BitacoraCambios.objects.create(
                        ejecucion=ej, dimension_calidad="Integridad", tabla_afectada=tabla,
                        regla_aplicada="Llave foránea sin registro en la dimensión destino",
                        filas_afectadas=len(rech), decision="Cuarentena",
                        justificacion="Se envían a stg_rechazos para no romper la base.",
                        responsable="Diego", revisor="Alexis")

            if tot["actualizadas"]:
                BitacoraCambios.objects.create(
                    ejecucion=ej, dimension_calidad="Unicidad", tabla_afectada="varias",
                    regla_aplicada="Llave existente con valores distintos: se actualiza",
                    filas_afectadas=tot["actualizadas"], decision="Corregir",
                    justificacion="La carga compara por PK y actualiza lo modificado.",
                    responsable="Diego", revisor="Alexis")

        ej.fecha_fin = timezone.now()
        ej.duracion_segundos = (ej.fecha_fin - ej.fecha_inicio).total_seconds()
        ej.filas_extraidas = extraidas
        ej.filas_duplicadas_eliminadas = duplicadas_eliminadas
        ej.filas_cuarentena = n_cuarentena
        ej.filas_validas = extraidas - n_cuarentena
        ej.filas_insertadas = tot["insertadas"]
        ej.filas_actualizadas = tot["actualizadas"]
        ej.filas_sin_cambio = tot["sin_cambio"]
        ej.estado = "EXITOSA"
        ej.save()
    except Exception:
        ej.estado = "FALLIDA"
        ej.fecha_fin = timezone.now()
        ej.save()
        raise
    return ej