"""
Vistas del Hito 1 — "los datos entran y quedan guardados".

Este módulo es solo la capa de presentación: LEE lo que el ETL (etl_engine) y el
cargador (dashboard.loader) dejan en la base y lo entrega a las plantillas.
No limpia, no transforma y no escribe datos de negocio.

Las dos únicas acciones que escriben son los botones de la pantalla de carga:
  - "Ejecutar carga": llama a dashboard.orquestador.ejecutar_pipeline (el mismo
    que usa `python manage.py ejecutar_etl`), con las rutas NOVAAPP_EXCEL y
    NOVAAPP_CSV del entorno.
  - "Reiniciar base": vacía hechos, cuentas, planes, cuarentena e historial para
    poder demostrar la doble ejecución desde cero. No toca dim_tiempo.
"""
import os
import threading
import unicodedata
from collections import Counter
import tempfile
import shutil
import pandas as pd

from django.apps import apps
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils.text import get_valid_filename

from .contexto import FKS, contexto_modelo
from .models import (
    BitacoraCambios,
    DimCuenta,
    DimPlan,
    EjecucionETL,
    FactSuscripciones,
    FactUso,
    StgRechazos,
)

try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Bogota")
except Exception:  # sin base de zonas horarias: se usa la del proyecto
    ZONA = None

TABLAS_HECHOS = ("fact_suscripciones", "fact_uso")
NOMBRE_HECHO = {"fact_suscripciones": "Suscripción", "fact_uso": "Uso"}
HISTORIAL_VISIBLE = 8
CONSOLA_VISIBLE = 2

# Color de la etiqueta según la decisión tomada sobre cada regla de calidad.
ETIQUETA_DECISION = {
    "Eliminar": "verde",
    "Corregir": "verde",
    "Anonimizar": "verde",
    "Marcar": "ambar",
    "Cuarentena": "ambar",
    "No tocar": "teal",
}

# Columnas con las que se mide k después de anonimizar, si el reporte no las trae.
CUASI_FINALES = ["zona", "industria", "canal_adquisicion"]

# Palabra que identifica cada llave dentro del motivo de rechazo de la cuarentena.
PALABRA_LLAVE = {"cuenta_id": "cuenta", "fecha_id": "fecha", "plan_id": "plan"}

_carga_en_curso = threading.Lock()


# ───────────── utilidades ─────────────

def _local(fecha):
    """Hora de Colombia, sin zona, para que la plantilla la muestre tal cual."""
    if fecha is None:
        return None
    local = timezone.localtime(fecha, ZONA) if ZONA else timezone.localtime(fecha)
    return local.replace(tzinfo=None)


def _ultima_exitosa():
    return EjecucionETL.objects.filter(estado="EXITOSA").order_by("-fecha_inicio", "-id").first()


def _hechos(ejecucion, clave):
    """Suma un contador de filas_por_tabla solo sobre las tablas de hechos."""
    por_tabla = ejecucion.filas_por_tabla or {}
    return sum(int((por_tabla.get(t) or {}).get(clave) or 0) for t in TABLAS_HECHOS)


def _sin_tildes(valor):
    texto = unicodedata.normalize("NFD", str(valor if valor is not None else "").strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _visible(texto):
    """Hace visibles los espacios sobrantes de un valor ('api ' -> 'api␣')."""
    texto = str(texto)
    limpio = texto.strip(" ")
    if limpio == texto:
        return texto
    izquierda = len(texto) - len(texto.lstrip(" "))
    derecha = len(texto) - len(texto.rstrip(" "))
    return "␣" * izquierda + limpio + "␣" * derecha


def _base(pagina):
    """Contexto común: página activa y la ejecución que respalda lo que se ve."""
    ultima = _ultima_exitosa()
    numero = None
    if ultima:
        numero = EjecucionETL.objects.filter(fecha_inicio__lte=ultima.fecha_inicio).count()
    return {
        "pagina": pagina,
        "ultima": ultima,
        "ultima_numero": numero,
        "ultima_fecha": _local(ultima.fecha_inicio) if ultima else None,
    }


# ───────────── carga ─────────────

def _privacidad_de(ejecucion):
    return (ejecucion.resumen_privacidad or {}) if ejecucion else {}


def _columnas_retiradas(resumen):
    """Identificadores directos que no llegan a la base (eliminados o seudonimizados)."""
    return [
        c for c in resumen.get("clasificacion_columnas") or []
        if str(c.get("clase", "")).lower() == "identificador directo"
        and str(c.get("tratamiento", "")).lower() != "conservada"
    ]


def _resumen_reglas(bitacora):
    return {
        "reglas": len(bitacora),
        "hallazgos": sum(1 for b in bitacora if b.filas_afectadas > 0),
        "mrr_corregidos": sum(
            b.filas_afectadas for b in bitacora
            if b.decision == "Corregir" and b.dimension_calidad == "Exactitud"
            and b.tabla_afectada == "fact_suscripciones"
        ),
        "funciones_normalizadas": sum(
            b.filas_afectadas for b in bitacora
            if b.decision == "Corregir" and b.dimension_calidad == "Consistencia"
            and b.tabla_afectada == "fact_uso"
        ),
    }


def _pasos(ultima, bitacora):
    """Las cinco etapas del proceso, con lo que hizo la última ejecución exitosa."""
    if not ultima:
        vacios = [
            ("Extraer", "filas (suscripciones + uso)"),
            ("Validar", "reglas de calidad revisadas"),
            ("Transformar", "MRR corregidos · funciones normalizadas"),
            ("Proteger", "columnas de datos personales retiradas"),
            ("Cargar", "filas en las tablas de hechos"),
        ]
        return [
            {"numero": i, "nombre": n, "valor": "—", "detalle": d, "listo": False}
            for i, (n, d) in enumerate(vacios, start=1)
        ]

    r = _resumen_reglas(bitacora)
    retiradas = len(_columnas_retiradas(_privacidad_de(ultima)))
    suscripciones = FactSuscripciones.objects.count()
    usos = FactUso.objects.count()
    miles = lambda n: f"{n:,}".replace(",", ".")
    return [
        {"numero": 1, "nombre": "Extraer", "valor": miles(ultima.filas_extraidas),
         "detalle": "filas (suscripciones + uso)", "listo": True},
        {"numero": 2, "nombre": "Validar", "valor": f"{r['reglas']} reglas",
         "detalle": f"{r['hallazgos']} con al menos un hallazgo", "listo": True},
        {"numero": 3, "nombre": "Transformar",
         "valor": f"{miles(r['mrr_corregidos'])} + {miles(r['funciones_normalizadas'])}",
         "detalle": "MRR corregidos · funciones normalizadas", "listo": True},
        {"numero": 4, "nombre": "Proteger",
         "valor": f"{retiradas} columna{'' if retiradas == 1 else 's'}",
         "detalle": "de datos personales retiradas", "listo": True},
        {"numero": 5, "nombre": "Cargar", "valor": miles(suscripciones + usos),
         "detalle": f"filas ({miles(suscripciones)} + {miles(usos)})", "listo": True},
    ]


def _historial():
    """Una fila por ejecución, de la más antigua a la más reciente."""
    filas, total_previo = [], None
    for numero, ej in enumerate(EjecucionETL.objects.order_by("fecha_inicio", "id"), start=1):
        nuevas = _hechos(ej, "insertadas")
        total = nuevas + _hechos(ej, "actualizadas") + _hechos(ej, "sin_cambio")
        if ej.estado == "FALLIDA":
            marca, color = "falló", "rojo"
        elif ej.estado != "EXITOSA":
            marca, color = "en curso", "neutro"
        elif total_previo is None:
            marca, color = "primera", "neutro"
        elif nuevas == 0 and total == total_previo:
            marca, color = "✓ no duplicó", "verde"
        else:
            marca, color = "el total cambió", "ambar"
        filas.append({
            "numero": numero, "ejecucion": ej, "hora": _local(ej.fecha_inicio),
            "nuevas": nuevas if ej.estado == "EXITOSA" else None,
            "total": total if ej.estado == "EXITOSA" else None,
            "marca": marca, "color": color,
        })
        if ej.estado == "EXITOSA":
            total_previo = total
    return filas


def _consola(historial):
    """Reconstruye, a partir de lo guardado, el registro de las últimas ejecuciones."""
    miles = lambda n: f"{n:,}".replace(",", ".")
    bloques = []
    for fila in historial[-CONSOLA_VISIBLE:]:
        ej = fila["ejecucion"]
        lineas = [{"texto": "$ python manage.py ejecutar_etl", "clase": "orden"}]
        if ej.estado != "EXITOSA":
            lineas.append({
                "texto": "✗ La ejecución no terminó: la transacción se revirtió y la base quedó como estaba.",
                "clase": "error",
            })
            bloques.append(lineas)
            continue
        r = _resumen_reglas(list(ej.bitacora.order_by("id")))
        retiradas = len(_columnas_retiradas(_privacidad_de(ej)))
        segundos = f"{ej.duracion_segundos or 0:.1f}".replace(".", ",")
        lineas += [
            {"texto": f"[1/5] Leyendo suscripciones y uso… {miles(ej.filas_extraidas)} filas"},
            {"texto": f"[2/5] Validando… {r['reglas']} reglas, {r['hallazgos']} con hallazgos"},
            {"texto": f"[3/5] Transformando… {miles(ej.filas_duplicadas_eliminadas)} duplicadas fuera, "
                      f"{miles(ej.filas_cuarentena)} a cuarentena"},
            {"texto": f"[4/5] Retirando datos personales… {retiradas} columnas"},
            {"texto": "[5/5] Cargando por llave primaria: lo idéntico no se vuelve a insertar…"},
            {"texto": f"      nuevas: {miles(fila['nuevas'])} · total: {miles(fila['total'])}",
             "clase": "resultado"},
            {"texto": f"Listo en {segundos} s", "clase": "ok"},
        ]
        bloques.append(lineas)
    return bloques


def _no_cargadas(bitacora):
    """Todo lo que se descartó queda contado y con motivo."""
    filas = []
    for b in bitacora:
        if b.dimension_calidad == "Unicidad" and b.decision == "Eliminar" and b.filas_afectadas > 0:
            nombre = NOMBRE_HECHO.get(b.tabla_afectada, b.tabla_afectada)
            filas.append({
                "motivo": f"{nombre} duplicad{'a' if nombre.endswith('n') else 'o'}",
                "filas": b.filas_afectadas,
                "destino": "Eliminado (se deja una copia)", "color": "rojo",
            })
    cuarentena = (
        StgRechazos.objects.values("tabla_origen", "motivo_rechazo")
        .annotate(total=Count("id")).order_by("tabla_origen", "-total")
    )
    for c in cuarentena:
        nombre = NOMBRE_HECHO.get(c["tabla_origen"], c["tabla_origen"])
        motivo = c["motivo_rechazo"]
        filas.append({
            "motivo": f"{nombre}: {motivo[:1].lower()}{motivo[1:]}",
            "filas": c["total"],
            "destino": "Enviado a cuarentena", "color": "ambar",
        })
    return filas


def carga(request):
    ctx = _base("carga")
    ultima = ctx["ultima"]
    bitacora = list(ultima.bitacora.order_by("id")) if ultima else []
    historial = _historial()
    orden_tablas = ["dim_plan", "dim_tiempo", "dim_cuenta", "fact_suscripciones", "fact_uso"]
    por_tabla_detalles = []
    if ultima and ultima.filas_por_tabla:
        for tbl in orden_tablas:
            datos = ultima.filas_por_tabla.get(tbl, {})
            por_tabla_detalles.append({
                "nombre": tbl,
                "insertadas": datos.get("insertadas", 0),
                "actualizadas": datos.get("actualizadas", 0),
                "sin_cambio": datos.get("sin_cambio", 0)
            })

    ctx.update({
        "pasos": _pasos(ultima, bitacora),
        "historial": historial[-HISTORIAL_VISIBLE:],
        "historial_ocultas": max(0, len(historial) - HISTORIAL_VISIBLE),
        "consola": _consola(historial),
        "no_cargadas": _no_cargadas(bitacora),
        "origen": (ultima.archivo_excel if ultima and ultima.archivo_excel else "-"),
        "origen_csv": (ultima.archivo_csv if ultima and ultima.archivo_csv else "-"),
        "rutas_configuradas": bool(os.environ.get("NOVAAPP_EXCEL") and os.environ.get("NOVAAPP_CSV")),
        "por_tabla_detalles": por_tabla_detalles,
    })
    return render(request, "carga.html", ctx)


@require_POST
def ejecutar_carga(request):
    if not _carga_en_curso.acquire(blocking=False):
        messages.error(request, "Ya hay una carga en curso.")
        return redirect("dashboard:carga")

    excel_temp = None
    csv_temp = None
    
    try:
        usar_servidor = request.POST.get("usar_servidor") == "1"
        if usar_servidor:
            ruta_excel = os.environ.get("NOVAAPP_EXCEL")
            ruta_csv = os.environ.get("NOVAAPP_CSV")
            if not ruta_excel or not ruta_csv:
                messages.error(request, "Faltan las variables de entorno NOVAAPP_EXCEL y NOVAAPP_CSV con las rutas de los archivos fuente. Defínalas antes de iniciar el servidor.")
                return redirect("dashboard:carga")
        else:
            archivo_excel = request.FILES.get("archivo_excel")
            archivo_csv = request.FILES.get("archivo_csv")
            
            if not archivo_excel:
                messages.error(request, "El archivo Excel es obligatorio.")
                return redirect("dashboard:carga")
                
            if not archivo_excel.name.lower().endswith('.xlsx'):
                messages.error(request, "El archivo Excel debe tener extensión .xlsx.")
                return redirect("dashboard:carga")
                
            if archivo_excel.size == 0 or archivo_excel.size > 50 * 1024 * 1024:
                messages.error(request, "El archivo Excel no es válido o supera los 50 MB.")
                return redirect("dashboard:carga")
                
            fd, excel_temp = tempfile.mkstemp(suffix=".xlsx", prefix=get_valid_filename(os.path.splitext(archivo_excel.name)[0]) + "_")
            with os.fdopen(fd, 'wb') as f:
                for chunk in archivo_excel.chunks():
                    f.write(chunk)
                    
            if archivo_csv:
                if not archivo_csv.name.lower().endswith('.csv'):
                    messages.error(request, "El archivo CSV debe tener extensión .csv.")
                    return redirect("dashboard:carga")
                if archivo_csv.size == 0 or archivo_csv.size > 50 * 1024 * 1024:
                    messages.error(request, "El archivo CSV no es válido o supera los 50 MB.")
                    return redirect("dashboard:carga")
                    
                fd, csv_temp = tempfile.mkstemp(suffix=".csv", prefix=get_valid_filename(os.path.splitext(archivo_csv.name)[0]) + "_")
                with os.fdopen(fd, 'wb') as f:
                    for chunk in archivo_csv.chunks():
                        f.write(chunk)
            else:
                try:
                    df = pd.read_excel(excel_temp, sheet_name=None)
                    hoja_fact_uso = None
                    for name in df.keys():
                        if name.strip().lower() == "fact_uso":
                            hoja_fact_uso = name
                            break
                    if not hoja_fact_uso:
                        messages.error(request, "El Excel no contiene la hoja fact_uso.")
                        return redirect("dashboard:carga")
                        
                    base_excel = get_valid_filename(os.path.splitext(archivo_excel.name)[0])
                    fd, csv_temp = tempfile.mkstemp(suffix="_fact_uso.csv", prefix=base_excel + "_")
                    df[hoja_fact_uso].to_csv(csv_temp, index=False, encoding='utf-8')
                except Exception as e:
                    messages.error(request, f"revisa que el Excel tenga las hojas indicadas.")
                    return redirect("dashboard:carga")

            ruta_excel = excel_temp
            ruta_csv = csv_temp

        from .orquestador import ejecutar_pipeline
        ejecucion, tiempos = ejecutar_pipeline(ruta_excel, ruta_csv)
        messages.success(request, f"Carga ejecutada exitosamente (ID: {ejecucion.id}).")
        return redirect("dashboard:carga")
        
    except Exception as error:
        tipo_error = error.__class__.__name__
        messages.error(request, f"Error en la ejecución del pipeline ({tipo_error}): revisa que el Excel tenga las hojas indicadas.")
        return redirect("dashboard:carga")
        
    finally:
        _carga_en_curso.release()
        if excel_temp and os.path.exists(excel_temp):
            try:
                os.remove(excel_temp)
            except:
                pass
        if csv_temp and os.path.exists(csv_temp):
            try:
                os.remove(csv_temp)
            except:
                pass


@require_POST
def reiniciar_base(request):
    """Deja la base como recién migrada (conserva dim_tiempo) para repetir la demostración."""
    if not _carga_en_curso.acquire(blocking=False):
        return JsonResponse({"ok": False, "error": "Hay una carga en curso; espere a que termine."},
                            status=409)
    try:
        with transaction.atomic():
            # Primero los hechos: las dimensiones están protegidas por sus llaves foráneas.
            for modelo in (FactUso, FactSuscripciones, StgRechazos, BitacoraCambios,
                           EjecucionETL, DimCuenta, DimPlan):
                modelo.objects.all().delete()
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error).strip()[:300]}, status=500)
    finally:
        _carga_en_curso.release()
    return JsonResponse({"ok": True})


# ───────────── calidad ─────────────

def _mapeos(bitacora):
    """Tablas 'antes y después' de cada normalización que guardó su mapeo de cambios."""
    tarjetas = []
    for b in bitacora:
        cambios = b.detalle_cambios
        if not isinstance(cambios, list) or not cambios:
            continue
        grupos = {}
        for cambio in cambios:
            if not isinstance(cambio, dict) or "nuevo" not in cambio:
                continue
            grupos.setdefault(str(cambio["nuevo"]), set()).add(str(cambio.get("original", "")))
        if not grupos:
            continue
        columna = b.regla_aplicada.replace("normalizar", "").strip() or b.regla_aplicada
        tarjetas.append({
            "columna": columna,
            "tabla": b.tabla_afectada,
            "filas_cambiadas": b.filas_afectadas,
            "variantes": sum(len(v) for v in grupos.values()),
            "valores": len(grupos),
            "filas": [
                {"originales": [nuevo] + [_visible(o) for o in sorted(originales) if o != nuevo],
                 "nuevo": nuevo}
                for nuevo, originales in sorted(grupos.items())
            ],
        })
    return tarjetas


def calidad(request):
    ctx = _base("calidad")
    ultima = ctx["ultima"]
    bitacora = list(ultima.bitacora.order_by("id")) if ultima else []

    reglas = [{
        "dimension": b.dimension_calidad,
        "tabla": b.tabla_afectada,
        "regla": b.regla_aplicada,
        "filas": b.filas_afectadas,
        "decision": "NO tocar" if b.decision == "No tocar" else b.decision,
        "color": ETIQUETA_DECISION.get(b.decision, "neutro"),
        "justificacion": b.justificacion,
        "responsable": b.responsable,
        "revisor": b.revisor,
        # La "trampa": parece un error y no lo es; se documenta y no se toca.
        "resaltada": b.decision == "No tocar" and b.filas_afectadas > 0,
    } for b in bitacora]

    por_dimension = Counter(r["dimension"] for r in reglas)
    maximo = max(por_dimension.values(), default=1)
    legitimos = [r for r in reglas if r["resaltada"] and r["dimension"] == "Completitud"]

    ctx.update({
        "reglas": reglas,
        "con_hallazgos": sum(1 for r in reglas if r["filas"] > 0),
        "sin_hallazgos": sum(1 for r in reglas if r["filas"] == 0),
        "dimensiones": [
            {"nombre": d, "reglas": n, "ancho": round(100 * n / maximo)}
            for d, n in por_dimension.items()
        ],
        "duplicados": ultima.filas_duplicadas_eliminadas if ultima else None,
        "vacios_legitimos": FactSuscripciones.objects.filter(plan__nombre_plan="Free", mrr=0, mrr_corregido=False).count() if ultima else None,
        "vacios_detalle": legitimos[0]["regla"] if legitimos else "ausencias que no son un error",
        "cuarentena": StgRechazos.objects.count() if ultima else None,
        "mapeos": _mapeos(bitacora),
    })
    return render(request, "calidad.html", ctx)


# ───────────── privacidad ─────────────

def _medir_k(columnas, meta):
    """Prueba de re-identificación en vivo sobre dim_cuenta, tal como quedó guardada."""
    grupos, muestra = Counter(), {}
    for fila in DimCuenta.objects.values_list(*columnas):
        clave = tuple(_sin_tildes(v) for v in fila)  # misma comparación que usa el ETL
        grupos[clave] += 1
        muestra.setdefault(clave, tuple(str(v) for v in fila))
    if not grupos:
        return None
    pequenos = sorted((n, muestra[clave]) for clave, n in grupos.items() if n < meta)
    return {
        "k": min(grupos.values()),
        "unicas": sum(1 for n in grupos.values() if n == 1),
        "en_riesgo": sum(n for n, _ in pequenos),
        "grupos": len(grupos),
        "total": sum(grupos.values()),
        "combinaciones": [{"valores": " · ".join(combo), "cuentas": n} for n, combo in pequenos[:5]],
        "combinaciones_mas": max(0, len(pequenos) - 5),
    }


def privacidad(request):
    ctx = _base("privacidad")
    resumen = _privacidad_de(ctx["ultima"])
    meta = int(resumen.get("k_meta") or 5)

    campos = {f.attname for f in DimCuenta._meta.concrete_fields}
    finales = [c for c in (resumen.get("cuasi_identificadores_finales") or CUASI_FINALES) if c in campos]
    despues = _medir_k(finales, meta) if finales else None

    antes = None
    if resumen.get("k_antes") is not None:
        antes = {
            "k": int(resumen["k_antes"]),
            "columnas": resumen.get("cuasi_identificadores_originales") or [],
            "unicas": resumen.get("cuentas_unicas_antes"),
            "total": resumen.get("filas"),
            "pct_riesgo": resumen.get("pct_cuentas_riesgo_antes"),
        }

    escala = 2 * meta  # la meta queda marcada en la mitad de la barra
    for medida in (antes, despues):
        if medida:
            medida["ancho"] = max(3, min(100, round(100 * medida["k"] / escala)))
            medida["cumple"] = medida["k"] >= meta

    clasificacion = []
    for c in resumen.get("clasificacion_columnas") or []:
        clase = str(c.get("clase", "")).strip()
        clasificacion.append({
            "columna": c.get("columna", ""),
            "clase": clase[:1].upper() + clase[1:],
            "color": {"identificador directo": "rojo", "cuasi-identificador": "ambar"}.get(clase.lower(), "neutro"),
            "tratamiento": str(c.get("tratamiento", "")).capitalize(),
            "criterio": c.get("criterio", ""),
        })
    perdido = [
        c["columna"] for c in clasificacion
        if c["color"] == "ambar" and c["tratamiento"].lower() != "conservada"
    ]

    ctx.update({
        "meta": meta,
        "antes": antes,
        "despues": despues,
        "columnas_despues": finales,
        "clasificacion": clasificacion,
        "perdido": perdido,
        "responsable": resumen.get("responsable", ""),
        "revisor": resumen.get("revisor", ""),
    })
    return render(request, "privacidad.html", ctx)


# ───────────── modelo ─────────────

# Posición de cada tabla en el diagrama (px del lienzo SVG). Las dimensiones que
# comparten los dos hechos (cuenta y tiempo) van en el centro.
LIENZO = {
    "dim_plan": {"x": 10, "ancho": 150},
    "fact_suscripciones": {"x": 215, "ancho": 200},
    "dim_cuenta": {"x": 480, "ancho": 150, "y": 50},
    "dim_tiempo": {"x": 480, "ancho": 150, "y": 140},
    "fact_uso": {"x": 695, "ancho": 200},
}
ALTO_FILA, ALTO_TITULO, ALTO_DIM = 15, 26, 54


def _diagrama(filas_por_tabla, modelos):
    cajas = {}
    for nombre, pos in LIENZO.items():
        modelo = modelos.get(nombre)
        if modelo is None:
            continue
        es_hecho = nombre in TABLAS_HECHOS
        columnas = [
            {"nombre": f.attname, "llave": "PK" if f.primary_key else ("FK" if f.is_relation else "")}
            for f in modelo._meta.concrete_fields
        ] if es_hecho else []
        alto = ALTO_TITULO + 12 + ALTO_FILA * len(columnas) if es_hecho else ALTO_DIM
        cajas[nombre] = {
            "nombre": nombre, "hecho": es_hecho, "x": pos["x"], "ancho": pos["ancho"],
            "alto": alto, "filas": filas_por_tabla.get(nombre), "y": pos.get("y"),
            "columnas": columnas,
        }

    alto_lienzo = max([c["alto"] for c in cajas.values()] + [220]) + 40
    for caja in cajas.values():
        if caja["y"] is None:
            caja["y"] = round((alto_lienzo - caja["alto"]) / 2)
        for i, columna in enumerate(caja["columnas"]):
            columna["y"] = caja["y"] + ALTO_TITULO + 18 + ALTO_FILA * i
        caja["medio"] = caja["y"] + caja["alto"] / 2

    enlaces = []
    for tabla, _fk, destino in FKS:
        hecho, dim = cajas.get(tabla), cajas.get(destino)
        if not hecho or not dim:
            continue
        a_la_derecha = dim["x"] > hecho["x"]  # la dimensión queda a la derecha del hecho
        signo = 1 if a_la_derecha else -1
        x_hecho = hecho["x"] + hecho["ancho"] if a_la_derecha else hecho["x"]
        x_dim = dim["x"] if a_la_derecha else dim["x"] + dim["ancho"]
        # Sale del hecho a la altura de la dimensión; si no alcanza, dobla en ángulo recto.
        y_hecho = min(max(dim["medio"], hecho["y"] + ALTO_TITULO + 10), hecho["y"] + hecho["alto"] - 10)
        x_medio = (x_hecho + x_dim) / 2
        enlaces.append({
            "trazo": f"M{x_hecho} {y_hecho}H{x_medio}V{dim['medio']}H{x_dim}",
            "n_x": x_hecho + 10 * signo, "n_y": y_hecho - 5,
            "uno_x": x_dim - 10 * signo, "uno_y": dim["medio"] - 5,
        })
    return {"cajas": list(cajas.values()), "enlaces": enlaces,
            "ancho": max(c["x"] + c["ancho"] for c in cajas.values()) + 10 if cajas else 900,
            "alto": alto_lienzo}


def modelo(request):
    ctx = _base("modelo")
    estrella = contexto_modelo()  # lo entrega la capa de datos (dashboard/contexto.py)
    modelos = {m._meta.db_table: m for m in apps.get_app_config("dashboard").get_models()}
    filas_por_tabla = {t["nombre"]: t["filas_cargadas"] for t in estrella["tablas"]}

    integridad = []
    for tabla, fk, destino in FKS:
        hecho, dim = modelos[tabla], modelos[destino]
        # Anti-join: filas del hecho cuya llave no existe en la dimensión.
        huerfanos = hecho.objects.exclude(**{f"{fk}__in": dim.objects.values("pk")}).count()
        en_cuarentena = StgRechazos.objects.filter(
            tabla_origen=tabla, motivo_rechazo__icontains=PALABRA_LLAVE.get(fk, fk)
        ).count()
        integridad.append({
            "hecho": tabla, "fk": fk, "dimension": destino,
            "huerfanos": huerfanos, "cuarentena": en_cuarentena,
        })

    ctx.update({
        "tablas": estrella["tablas"],
        "hechos": [t for t in estrella["tablas"] if t["nombre"] in TABLAS_HECHOS],
        "integridad": integridad,
        "total_cuarentena": StgRechazos.objects.count(),
        "diagrama": _diagrama(filas_por_tabla, modelos),
    })
    return render(request, "modelo.html", ctx)
