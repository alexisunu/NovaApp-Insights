import json
from decimal import Decimal
import pandas as pd
from django.db import transaction
from django.utils import timezone
from .models import (
    BitacoraCambios,
    DimCuenta,
    DimPlan,
    DimTiempo,
    EjecucionETL,
    FactSuscripciones,
    FactUso,
    StgRechazos,
)

LOTE = 5000
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

REQUERIDAS = {
    "plan": ["plan_id", "nombre_plan", "precio_mensual", "limite_usuarios"],
    "tiempo": ["fecha_id", "fecha"],
    "cuenta": [
        "cuenta_id", "nit_hash", "zona", "industria",
        "canal_adquisicion", "fecha_registro_inconsistente"
    ],
    "suscripciones": [
        "sub_id", "cuenta_id", "fecha_id", "plan_id", "mrr",
        "mrr_original", "mrr_corregido", "usuarios_activos",
        "usuarios_activos_faltante", "excede_limite", "churn"
    ],
    "uso": [
        "uso_id", "cuenta_id", "fecha_id", "feature",
        "duracion_min", "duracion_min_faltante"
    ],
}

BOOLEANAS = {
    "cuenta": ["fecha_registro_inconsistente"],
    "suscripciones": [
        "mrr_corregido", "usuarios_activos_faltante", "excede_limite", "churn"
    ],
    "uso": ["duracion_min_faltante"],
}

DECISIONES = {d.lower(): d for d, _ in BitacoraCambios.DECISIONES}


# ───────────── utilidades ─────────────

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


def _id_texto(v):
    v = _norm(v)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _json_seguro(obj):
    return json.loads(json.dumps(obj, default=str))


# ───────────── preparación de los DataFrames ─────────────

def completar_tiempo(df):
    """Calcula las columnas derivadas desde 'fecha'."""
    df = df.copy()
    f = pd.to_datetime(df["fecha"])
    df["fecha"] = f.dt.date
    df["anio"] = f.dt.year
    df["mes"] = f.dt.month
    df["mes_orden"] = f.dt.month
    df["dia"] = f.dt.day
    df["dia_semana_orden"] = f.dt.weekday + 1  # lunes = 1
    df["nombre_mes"] = f.dt.month.map(lambda m: MESES[m - 1])
    df["dia_semana"] = f.dt.weekday.map(lambda w: DIAS[w])
    df["trimestre"] = f.dt.quarter.map(lambda q: f"T{q}")
    df["es_finde"] = f.dt.weekday >= 5
    return df


def preparar(dfs):
    """Valida columnas y deja los tipos listos."""
    faltan, out = [], {}
    for nombre, df in dfs.items():
        if nombre not in REQUERIDAS:
            raise ValueError(f"DataFrame desconocido: '{nombre}'")
        sin = [c for c in REQUERIDAS[nombre] if c not in df.columns]
        if sin:
            faltan.append(f"{nombre}: {', '.join(sin)}")
        out[nombre] = df.copy()

    for nombre in ("plan", "cuenta", "suscripciones", "uso"):
        if nombre not in out:
            faltan.append(f"{nombre}: DataFrame ausente")

    if faltan:
        raise ValueError("Faltan columnas -> " + " | ".join(faltan))

    out["plan"]["precio_mensual"] = pd.to_numeric(out["plan"]["precio_mensual"]).round(2)
    s = out["suscripciones"]
    s["mrr"] = pd.to_numeric(s["mrr"], errors="coerce").round(2)
    s["mrr_original"] = pd.to_numeric(s["mrr_original"], errors="coerce").round(2)
    s["usuarios_activos"] = pd.to_numeric(s["usuarios_activos"], errors="coerce").round().astype("Int64")
    u = out["uso"]
    u["duracion_min"] = pd.to_numeric(u["duracion_min"], errors="coerce").round(1)

    for nombre, cols in BOOLEANAS.items():
        if nombre in out:
            for c in cols:
                out[nombre][c] = out[nombre][c].fillna(False).astype(bool)

    if "tiempo" in out:
        out["tiempo"] = completar_tiempo(out["tiempo"])

    return out


# ───────────── carga por PK ─────────────

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


# ───────────── cuarentena ─────────────

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
            id_origen=_id_texto(fila[pk]),
            cuenta_id=_int(fila.get("cuenta_id")),
            fecha_id=_int(fila.get("fecha_id")),
            motivo_rechazo=motivo[i],
            fila_original=json.loads(fila.to_json()),
        ))
    return df[ok], rechazos


def rechazos_externos(df_rechazos):
    """Convierte el df_rechazos del ETL en filas de StgRechazos, tal cual."""
    if df_rechazos is None or len(df_rechazos) == 0:
        return []

    salida = []
    for fila in df_rechazos.to_dict("records"):
        orig = fila.get("fila_original")
        if isinstance(orig, str):
            try:
                orig = json.loads(orig)
            except ValueError:
                orig = {"valor": orig}
        elif not isinstance(orig, (dict, list)):
            orig = _json_seguro({k: _norm(v) for k, v in fila.items()})

        salida.append(StgRechazos(
            tabla_origen=str(fila.get("tabla_origen", "")),
            id_origen=_id_texto(fila.get("id_origen")),
            cuenta_id=_int(fila.get("cuenta_id")),
            fecha_id=_int(fila.get("fecha_id")),
            motivo_rechazo=str(fila.get("motivo_rechazo", ""))[:255],
            fila_original=orig,
        ))
    return salida


# ───────────── bitácora ─────────────

def guardar_diagnostico(ej, diagnostico):
    """Guarda en BitacoraCambios las filas del diagnóstico de calidad."""
    if diagnostico is None:
        return 0

    filas = diagnostico.to_dict("records") if isinstance(diagnostico, pd.DataFrame) else list(diagnostico)
    objs = []
    for r in filas:
        decision = DECISIONES.get(str(r.get("decision", "")).strip().lower())
        if decision is None:
            raise ValueError(f"Decisión no válida en el diagnóstico: {r.get('decision')!r}")

        detalle = r.get("mapeo_cambios")
        if detalle is not None and not isinstance(detalle, (dict, list, str)):
            detalle = None

        objs.append(BitacoraCambios(
            ejecucion=ej,
            dimension_calidad=str(r.get("dimension", "")),
            tabla_afectada=str(r.get("tabla", "")),
            regla_aplicada=str(r.get("regla", "")),
            filas_afectadas=_int(r.get("filas")) or 0,
            decision=decision,
            justificacion=str(r.get("justificacion", "")),
            responsable=str(r.get("responsable", "")),
            revisor=str(r.get("revisor", "")),
            detalle_cambios=_json_seguro(detalle) if detalle is not None else None,
        ))
    BitacoraCambios.objects.bulk_create(objs)
    return len(objs)


# ───────────── carga completa ─────────────

def cargar(dfs, df_rechazos=None, diagnostico=None, resumen_privacidad=None,
           duplicadas_eliminadas=0, archivo_excel="", archivo_csv=""):
    dfs = preparar(dfs)

    if "tiempo" not in dfs and not DimTiempo.objects.exists():
        raise RuntimeError("dim_tiempo está vacía: entrega el DataFrame 'tiempo' "
                           "o corre 'python manage.py cargar_dim_tiempo'")

    externos = rechazos_externos(df_rechazos)
    ej = EjecucionETL.objects.create(
        fecha_inicio=timezone.now(),
        archivo_excel=archivo_excel,
        archivo_csv=archivo_csv
    )
    tot = {"insertadas": 0, "actualizadas": 0, "sin_cambio": 0}
    por_tabla, rechazos_por_tabla = {}, {}
    extraidas = sum(len(df) for df in dfs.values()) + len(externos)

    def registrar(nombre, original, cargado, modelo):
        r = upsert(modelo, cargado)
        por_tabla[nombre] = {"extraidas": len(original), **r}
        for k in tot:
            tot[k] += r[k]

    try:
        with transaction.atomic():
            registrar("dim_plan", dfs["plan"], dfs["plan"], DimPlan)
            if "tiempo" in dfs:
                registrar("dim_tiempo", dfs["tiempo"], dfs["tiempo"], DimTiempo)

            planes = set(DimPlan.objects.values_list("plan_id", flat=True))
            fechas = set(DimTiempo.objects.values_list("fecha_id", flat=True))

            registrar("dim_cuenta", dfs["cuenta"], dfs["cuenta"], DimCuenta)
            cuentas = set(DimCuenta.objects.values_list("cuenta_id", flat=True))

            buenas, rech = separar_huerfanos(
                dfs["suscripciones"], "fact_suscripciones", "sub_id",
                {"cuenta_id": cuentas, "fecha_id": fechas, "plan_id": planes}
            )
            rechazos_por_tabla["fact_suscripciones"] = rech
            registrar("fact_suscripciones", dfs["suscripciones"], buenas, FactSuscripciones)

            buenas, rech = separar_huerfanos(
                dfs["uso"], "fact_uso", "uso_id",
                {"cuenta_id": cuentas, "fecha_id": fechas}
            )
            rechazos_por_tabla["fact_uso"] = rech
            registrar("fact_uso", dfs["uso"], buenas, FactUso)

            todos = {}
            for rech in rechazos_por_tabla.values():
                for r in rech:
                    todos[(r.tabla_origen, r.id_origen)] = r
            for r in externos:
                todos.setdefault((r.tabla_origen, r.id_origen), r)

            for r in todos.values():
                r.ejecucion = ej

            StgRechazos.objects.bulk_create(list(todos.values()), ignore_conflicts=True, batch_size=LOTE)
            n_cuarentena = len(todos)

            for tabla, rech in rechazos_por_tabla.items():
                if rech:
                    BitacoraCambios.objects.create(
                        ejecucion=ej, dimension_calidad="Integridad", tabla_afectada=tabla,
                        regla_aplicada="Llave foránea sin registro en la dimensión destino",
                        filas_afectadas=len(rech), decision="Cuarentena",
                        justificacion="Se envían a stg_rechazos para no romper la base.",
                        responsable="Diego", revisor="Alexis"
                    )

            if tot["actualizadas"]:
                BitacoraCambios.objects.create(
                    ejecucion=ej, dimension_calidad="Unicidad", tabla_afectada="varias",
                    regla_aplicada="Llave existente con valores distintos: se actualiza",
                    filas_afectadas=tot["actualizadas"], decision="Corregir",
                    justificacion="La carga compara por PK y actualiza lo modificado.",
                    responsable="Diego", revisor="Alexis"
                )

            guardar_diagnostico(ej, diagnostico)

        ej.fecha_fin = timezone.now()
        ej.duracion_segundos = (ej.fecha_fin - ej.fecha_inicio).total_seconds()
        ej.filas_extraidas = extraidas
        ej.filas_duplicadas_eliminadas = duplicadas_eliminadas
        ej.filas_cuarentena = n_cuarentena
        ej.filas_validas = extraidas - n_cuarentena
        ej.filas_insertadas = tot["insertadas"]
        ej.filas_actualizadas = tot["actualizadas"]
        ej.filas_sin_cambio = tot["sin_cambio"]
        ej.filas_por_tabla = por_tabla
        ej.resumen_privacidad = _json_seguro(resumen_privacidad) if resumen_privacidad else {}
        ej.estado = "EXITOSA"
        ej.save()
    except Exception:
        ej.estado = "FALLIDA"
        ej.fecha_fin = timezone.now()
        ej.save()
        raise

    return ej