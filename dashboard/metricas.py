"""
Indicadores del Hito 2 (E8). Se calculan en cada consulta; ninguno se guarda.

Todas las funciones reciben un Filtro con el alcance del rol (zona) y la
segmentación elegida en pantalla (plan, año). La zona del rol se aplica dentro
de la consulta: lo que el rol no puede ver nunca sale de la base.

Definiciones (también en FICHAS, que alimenta la pantalla "Métricas"):
  - Mes activo de una cuenta: tiene una fila en fact_suscripciones ese mes.
  - Cancelación: fila con churn = verdadero (la cuenta se va ese mes).
  - Plan de pago: plan con precio_mensual > 0 (excluye Free).
"""
import calendar
from dataclasses import dataclass
from statistics import mean, pstdev

from django.db.models import Count, Q, Sum

from .models import DimCuenta, DimPlan, FactSuscripciones, FactUso

MES_REFERENCIA = 20251201
ANIO_REFERENCIA = 2025

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
PAGO = Q(plan__precio_mensual__gt=0)
UMBRAL_Z = 2.0

FICHAS_GUIA = [
    {
        "clave": "mrr",
        "nombre": "MRR dic-2025",
        "formula": "SUMA(mrr) de fact_suscripciones en dic-2025",
        "mide": "Ingreso mensual recurrente al cierre de 2025.",
        "decision": "Base de proyección de flujo de caja y valoración del negocio.",
        "tipo": "Guía",
    },
    {
        "clave": "cuentas",
        "nombre": "Cuentas activas dic-2025",
        "formula": "CONTAR DISTINTOS(cuenta_id) con suscripción en dic-2025",
        "mide": "Clientes activos con servicio vigente en dic-2025.",
        "decision": "Dimensionar operaciones, soporte y metas comerciales.",
        "tipo": "Guía",
    },
    {
        "clave": "arpa",
        "nombre": "ARPA dic-2025",
        "formula": "MRR dic-2025 ÷ cuentas con plan pago (precio > 0) en dic-2025",
        "mide": "Cuánto paga en promedio una cuenta que no está en plan Free.",
        "decision": "Evaluar pricing, monetización y venta cruzada.",
        "tipo": "Guía (excluye Free)",
    },
    {
        "clave": "cancelacion",
        "nombre": "Cancelación mensual 2025 (%)",
        "formula": "Suscripciones de 2025 con churn verdadero ÷ suscripciones de 2025",
        "mide": "Tasa mensual promedio de pérdida de clientes durante el año 2025.",
        "decision": "Priorizar retención y detectar fugas de clientes por segmento.",
        "tipo": "Guía",
    },
    {
        "clave": "pct_cuentas_pago",
        "nombre": "Cuentas pagas dic-2025 (%)",
        "formula": "Cuentas con plan pago ÷ cuentas activas, en dic-2025",
        "mide": "Porcentaje de clientes que pagan frente al total de activos en dic-2025.",
        "decision": "Monitorear la efectividad de la conversión de Free a pago.",
        "tipo": "Guía",
    },
    {
        "clave": "uso_por_cuenta_mes",
        "nombre": "Uso por cuenta-mes 2025",
        "formula": "Eventos de uso de 2025 ÷ suscripciones de 2025",
        "mide": "Intensidad promedio mensual de uso del software por cada suscripción.",
        "decision": "Detectar adopción y engagement del producto frente a la facturación.",
        "tipo": "Guía",
    },
]

FICHAS_COMPLEMENTARIAS = [
    {
        "clave": "mrr_mes", "nombre": "MRR del último mes con datos",
        "formula": "SUMA(mrr) de fact_suscripciones en el último mes registrado",
        "mide": "Cuánto dinero entra en el mes más reciente.",
        "decision": "Monitoreo del ingreso mensual recurrente actual.",
        "tipo": "Base",
    },
    {
        "clave": "variacion", "nombre": "Variación del MRR vs. mes anterior",
        "formula": "(MRR mes − MRR mes anterior) / MRR mes anterior",
        "mide": "Si este mes entró más o menos que el anterior.",
        "decision": "Detectar a tiempo una caída y revisar cancelaciones o precios.",
        "tipo": "Contra periodo anterior",
    },
    {
        "clave": "cancelacion_ult12", "nombre": "Cancelación últimos 12 meses",
        "formula": "SUMA(churn) / CONTAR(filas) de los últimos 12 meses",
        "mide": "Qué parte de las cuentas se va cada mes, en promedio móvil.",
        "decision": "Quién se está yendo: priorizar retención por plan o país.",
        "tipo": "Base",
    },
    {
        "clave": "sobre_limite", "nombre": "Cuentas sobre el límite de su plan",
        "formula": "CONTAR DISTINTOS(cuenta_id con excede_limite) / Cuentas activas, último mes",
        "mide": "Clientes con más usuarios de los que su plan permite.",
        "decision": "Lista de candidatos para ofrecer un plan superior.",
        "tipo": "Base",
    },
]

FICHAS = FICHAS_GUIA + FICHAS_COMPLEMENTARIAS


@dataclass
class Filtro:
    pais: str | None = None        # alcance del rol o segmentación elegida
    zona: str | None = None        # agrupación opcional para Dirección
    plan_id: int | None = None     # segmentación (no aplica al uso: fact_uso no tiene plan)
    anio: int | None = None
    ver_mrr: bool = True

    def suscripciones(self):
        qs = FactSuscripciones.objects.all()
        if self.pais:
            qs = qs.filter(cuenta__pais=self.pais)
        elif self.zona:
            qs = qs.filter(cuenta__zona=self.zona)
        if self.plan_id:
            qs = qs.filter(plan_id=self.plan_id)
        if self.anio:
            qs = qs.filter(fecha__anio=self.anio)
        return qs

    def usos(self):
        qs = FactUso.objects.all()
        if self.pais:
            qs = qs.filter(cuenta__pais=self.pais)
        elif self.zona:
            qs = qs.filter(cuenta__zona=self.zona)
        if self.anio:
            qs = qs.filter(fecha__anio=self.anio)
        return qs

    def sin_plan(self):
        return Filtro(pais=self.pais, zona=self.zona, plan_id=None, anio=self.anio, ver_mrr=self.ver_mrr)


# ───────────── utilidades ─────────────

def etiqueta_mes(fecha_id):
    anio, mes = divmod(int(fecha_id) // 100, 100)
    return f"{MESES[mes - 1]}-{str(anio)[2:]}"


def _tasa(parte, total):
    return (parte / total) if total else None


def n(valor):
    """1234567 -> '1.234.567'"""
    return f"{int(round(valor)):,}".replace(",", ".")


def dec(valor, cifras=1):
    """3.456 -> '3,5'"""
    return f"{valor:,.{cifras}f}".replace(",", "·").replace(".", ",").replace("·", ".")


def paises():
    return list(DimCuenta.objects.exclude(pais="").values_list("pais", flat=True).distinct().order_by("pais"))


def zonas():
    return list(DimCuenta.objects.exclude(zona="").values_list("zona", flat=True).distinct().order_by("zona"))


def planes():
    return list(DimPlan.objects.order_by("-precio_mensual").values("plan_id", "nombre_plan", "precio_mensual"))


def anios():
    return list(FactSuscripciones.objects.values_list("fecha__anio", flat=True).distinct().order_by("fecha__anio"))


def indicadores_guia(f):
    """
    Calcula los seis indicadores de la guía para el periodo de referencia (dic-2025 y año 2025).
    Respeta el Filtro f (país del líder, ver_mrr para soporte).
    Si no hay datos en el periodo, devuelve None.

    1. MRR dic-2025: suma del mrr de las suscripciones de dic-2025.
    2. Cuentas activas dic-2025: cuentas distintas con suscripción en dic-2025.
    3. ARPA dic-2025: MRR de dic-2025 ÷ cuentas distintas con plan pago (precio_mensual > 0) en dic-2025.
    4. Cancelación mensual 2025 (%): suscripciones de 2025 con churn verdadero ÷ suscripciones de 2025.
    5. Cuentas pagas dic-2025 (%): cuentas con plan pago ÷ cuentas activas, en dic-2025.
    6. Uso por cuenta-mes 2025: eventos de uso de 2025 ÷ suscripciones de 2025.
    """
    subs_dic = f.suscripciones().filter(fecha_id=MES_REFERENCIA)
    subs_anio = f.suscripciones().filter(fecha__anio=ANIO_REFERENCIA)
    usos_anio = f.usos().filter(fecha__anio=ANIO_REFERENCIA)

    agg_dic = subs_dic.aggregate(
        mrr_total=Sum("mrr"),
        cuentas=Count("cuenta_id", distinct=True),
        mrr_pago=Sum("mrr", filter=PAGO),
        cuentas_pago=Count("cuenta_id", filter=PAGO, distinct=True),
    )
    agg_anio = subs_anio.aggregate(
        total_filas=Count("sub_id"),
        churn_filas=Count("sub_id", filter=Q(churn=True)),
    )
    total_eventos_anio = usos_anio.count()

    cuentas_activas = agg_dic["cuentas"] or 0
    total_subs_anio = agg_anio["total_filas"] or 0

    if cuentas_activas == 0 and total_subs_anio == 0:
        return None

    cuentas_pago = agg_dic["cuentas_pago"] or 0
    mrr_dic = float(agg_dic["mrr_total"] or 0) if f.ver_mrr else None
    arpa_dic = (mrr_dic / cuentas_pago) if (f.ver_mrr and cuentas_pago > 0) else None

    churn_subs_anio = agg_anio["churn_filas"] or 0
    cancelacion_2025 = (churn_subs_anio / total_subs_anio) if total_subs_anio > 0 else None

    pct_pago_dic = (cuentas_pago / cuentas_activas) if cuentas_activas > 0 else None
    uso_por_cuenta_mes = (total_eventos_anio / total_subs_anio) if total_subs_anio > 0 else None

    return {
        "mes_referencia": "dic-2025",
        "anio_referencia": ANIO_REFERENCIA,
        "mrr": mrr_dic,
        "cuentas": cuentas_activas,
        "arpa": arpa_dic,
        "cancelacion": cancelacion_2025,
        "cuentas_pago": cuentas_pago,
        "pct_cuentas_pago": pct_pago_dic,
        "uso_por_cuenta_mes": uso_por_cuenta_mes,
        "eventos_2025": total_eventos_anio,
        "subs_2025": total_subs_anio,
    }


# ───────────── series ─────────────

def serie_mensual(f):
    """Una fila por mes con los conteos que necesitan los indicadores."""
    agregados = {
        "cuentas": Count("cuenta_id", distinct=True),
        "filas": Count("sub_id"),
        "cancelaciones": Count("sub_id", filter=Q(churn=True)),
        "sobre_limite": Count("cuenta_id", filter=Q(excede_limite=True), distinct=True),
    }
    if f.ver_mrr:  # para Soporte el MRR ni siquiera se consulta
        agregados.update({
            "suma_mrr": Sum("mrr"),
            "suma_mrr_pago": Sum("mrr", filter=PAGO),
            "cuentas_pago": Count("cuenta_id", filter=PAGO, distinct=True),
        })
    filas = f.suscripciones().values("fecha_id").annotate(**agregados).order_by("fecha_id")
    serie = []
    for fila in filas:
        fila["etiqueta"] = etiqueta_mes(fila["fecha_id"])
        if f.ver_mrr:
            fila["mrr"] = float(fila.pop("suma_mrr") or 0)
            fila["mrr_pago"] = float(fila.pop("suma_mrr_pago") or 0)
        serie.append(fila)
    return serie


def uso_mensual(f):
    filas = (f.usos().values("fecha__anio", "fecha__mes")
             .annotate(eventos=Count("uso_id")).order_by("fecha__anio", "fecha__mes"))
    return [{
        "clave": r["fecha__anio"] * 100 + r["fecha__mes"],
        "etiqueta": f"{MESES[r['fecha__mes'] - 1]}-{str(r['fecha__anio'])[2:]}",
        "eventos": r["eventos"],
        # Por día: febrero tiene menos días y no por eso es una anomalía.
        "por_dia": r["eventos"] / calendar.monthrange(r["fecha__anio"], r["fecha__mes"])[1],
    } for r in filas]


def meses_atipicos(uso):
    """Meses con |z| > 2 sobre los eventos POR DÍA (descuenta el efecto del calendario)."""
    zs = puntajes_z([u["por_dia"] for u in uso])
    return [(u, z) for u, z in zip(uso, zs) if abs(z) > UMBRAL_Z]


# ───────────── indicadores principales ─────────────

def indicadores(f):
    """Los siete indicadores del Tablero, para el último mes con datos."""
    serie = serie_mensual(f)
    if not serie:
        return None
    ult = serie[-1]
    previo = serie[-2] if len(serie) > 1 else None
    ultimos12 = serie[-12:]
    uso = {u["clave"]: u["eventos"] for u in uso_mensual(f)}
    eventos_mes = uso.get(ult["fecha_id"] // 100, 0)

    r = {
        "mes": ult["etiqueta"],
        "cuentas": ult["cuentas"],
        "cuentas_max": max(serie, key=lambda s: s["cuentas"]),
        "cancelacion": _tasa(sum(s["cancelaciones"] for s in ultimos12), sum(s["filas"] for s in ultimos12)),
        "cancelacion_desde": ultimos12[0]["etiqueta"],
        "uso_por_cuenta": _tasa(eventos_mes, ult["cuentas"]),
        "eventos_mes": eventos_mes,
        "sobre_limite": _tasa(ult["sobre_limite"], ult["cuentas"]),
        "sobre_limite_cuentas": ult["sobre_limite"],
    }
    if f.ver_mrr:
        maximo = max(serie, key=lambda s: s["mrr"])
        r.update({
            "mrr": ult["mrr"],
            "mrr_previo": previo["mrr"] if previo else None,
            "mes_previo": previo["etiqueta"] if previo else None,
            "variacion": _tasa(ult["mrr"] - previo["mrr"], previo["mrr"]) if previo else None,
            "mrr_max": maximo,
            "desde_max": _tasa(ult["mrr"] - maximo["mrr"], maximo["mrr"]),
            "arpa": _tasa(ult["mrr_pago"], ult["cuentas_pago"]),
            "cuentas_pago": ult["cuentas_pago"],
        })
    return r


# ───────────── detalle (tercer nivel) ─────────────

def mrr_por(f, campo):
    """MRR acumulado del periodo agrupado por 'plan__nombre_plan' o 'cuenta__zona'."""
    filas = (f.suscripciones().values(campo).annotate(total=Sum("mrr"), cuentas=Count("cuenta_id", distinct=True))
             .order_by("-total"))
    return [{"nombre": r[campo] or "Sin dato", "valor": float(r["total"] or 0), "cuentas": r["cuentas"]}
            for r in filas]


def uso_por_funcion(f):
    return [{"nombre": r["feature"], "valor": r["n"]}
            for r in f.usos().values("feature").annotate(n=Count("uso_id")).order_by("-n")]


def por_plan(f):
    """Para Explorar: MRR, cuentas y cancelación de cada plan (sin filtrar por plan)."""
    base = f.sin_plan().suscripciones()
    agregados = {
        "cuentas": Count("cuenta_id", distinct=True),
        "filas": Count("sub_id"),
        "cancelaciones": Count("sub_id", filter=Q(churn=True)),
    }
    if f.ver_mrr:
        agregados["suma_mrr"] = Sum("mrr")
    filas = list(base.values("plan_id", "plan__nombre_plan", "plan__precio_mensual").annotate(**agregados)
                 .order_by("-plan__precio_mensual"))
    for r in filas:
        if f.ver_mrr:
            r["mrr"] = float(r.pop("suma_mrr") or 0)
    total_mrr = sum(r.get("mrr", 0) for r in filas)
    for r in filas:
        r["nombre"] = r["plan__nombre_plan"]
        r["cancelacion"] = _tasa(r["cancelaciones"], r["filas"])
        if f.ver_mrr:
            r["participacion"] = _tasa(r["mrr"], total_mrr)
    return filas


# ───────────── alertas (E11) ─────────────

def _racha_de_caida(serie, campo):
    """Cuántos meses seguidos, contando hacia atrás desde el último, bajó el valor."""
    racha = 0
    for i in range(len(serie) - 1, 0, -1):
        if serie[i][campo] < serie[i - 1][campo]:
            racha += 1
        else:
            break
    return racha


def puntajes_z(valores):
    if len(valores) < 3:
        return [0.0] * len(valores)
    promedio, desv = mean(valores), pstdev(valores)
    return [((v - promedio) / desv) if desv else 0.0 for v in valores]


def alertas(f, registro_inconsistente=0):
    """Reglas simples y explicables. Una alerta es una pregunta, no una conclusión."""
    salida = []
    serie = serie_mensual(f)
    uso = uso_mensual(f)

    # 1. Coherencia: cuentas activas frente a eventos de uso, primer y último mes.
    if serie and uso:
        eventos = {u["clave"]: u["eventos"] for u in uso}
        a, b = serie[0], serie[-1]
        ea, eb = eventos.get(a["fecha_id"] // 100, 0), eventos.get(b["fecha_id"] // 100, 0)
        crec_cuentas = _tasa(b["cuentas"], a["cuentas"]) or 0
        crec_uso = _tasa(eb, ea) or 0
        incoherente = crec_cuentas >= 2 and crec_uso < crec_cuentas / 2
        texto = (f"{a['etiqueta']}: {n(a['cuentas'])} cuentas y {n(ea)} eventos. "
                 f"{b['etiqueta']}: {n(b['cuentas'])} cuentas y {n(eb)} eventos.")
        if incoherente:
            texto += (f" Las cuentas se multiplicaron por {dec(crec_cuentas)} y el uso por {dec(crec_uso)}: "
                      "no es creíble, hay que revisar las fechas de fact_uso.")
        if registro_inconsistente:
            texto += (f" Además, {n(registro_inconsistente)} cuentas tienen fecha de registro posterior "
                      "a su primera suscripción.")
        salida.append({
            "titulo": "El uso no crece aunque las cuentas sí" if incoherente else "Uso y cuentas crecen juntos",
            "texto": texto, "tipo": "coherencia" if incoherente else "sin alerta",
            "color": "rojo" if incoherente else "teal",
        })

    # 2. Tendencia del MRR (o de las cuentas, si el rol no ve MRR).
    campo = "mrr" if f.ver_mrr else "cuentas"
    nombre = "MRR" if f.ver_mrr else "Cuentas activas"
    if len(serie) > 2:
        racha = _racha_de_caida(serie, campo)
        if racha >= 2:
            desde, hasta = serie[-1 - racha], serie[-1]
            cambio = _tasa(hasta[campo] - desde[campo], desde[campo]) or 0
            salida.append({
                "titulo": f"{nombre}: {racha} meses seguidos de caída",
                "texto": f"De {desde['etiqueta']} a {hasta['etiqueta']} cambió {'−' if cambio < 0 else '+'}{dec(abs(cambio) * 100)} %. "
                         f"Cuentas activas: de {n(desde['cuentas'])} a {n(hasta['cuentas'])}.",
                "tipo": "tendencia", "color": "ambar",
            })
        else:
            salida.append({
                "titulo": f"{nombre}: sin caída sostenida",
                "texto": "El último mes no viene de dos o más caídas seguidas.",
                "tipo": "sin alerta", "color": "teal",
            })

    # 3. Detección de meses atípicos en las 5 series clave (|z| > 2.0)
    ARRANQUE = 6  # Excluir primeros 6 meses en series temporales

    # Serie A: Eventos de uso por día
    if uso:
        # Excluir arranque si hay suficientes meses
        uso_eval = uso[ARRANQUE:] if len(uso) > ARRANQUE else uso
        zs_uso = puntajes_z([u["por_dia"] for u in uso_eval])
        atipicos_uso = [(u, z) for u, z in zip(uso_eval, zs_uso) if abs(z) > UMBRAL_Z]
        if atipicos_uso:
            plural = len(atipicos_uso) > 1
            meses_txt = ", ".join(f"{u['etiqueta']} ({dec(u['por_dia'])} eventos/día, z={dec(z)})" for u, z in atipicos_uso[:3])
            salida.append({
                "titulo": f"¿Mes{'es' if plural else ''} atípico{'s' if plural else ''} en eventos de uso por día?",
                "texto": f"¿Por qué en {meses_txt} el uso diario se desvió significativamente (|z| > 2)? Conviene revisar campañas o caídas técnicas.",
                "tipo": "atípico", "color": "rojo", "claves": [u["clave"] for u, _ in atipicos_uso],
            })
        else:
            salida.append({
                "titulo": "Sin meses atípicos en eventos de uso por día",
                "texto": "Todos los meses se mantienen dentro del rango de variación esperado (|z| ≤ 2).",
                "tipo": "sin alerta", "color": "teal", "claves": [],
            })

    # Serie B: MRR (variación mensual, solo si ver_mrr y excluyendo primeros 6 meses)
    if f.ver_mrr and len(serie) > 1:
        # Variación porcentual mensual: (mrr[i] - mrr[i-1]) / mrr[i-1]
        variaciones_mrr = []
        for i in range(1, len(serie)):
            prev, curr = serie[i - 1]["mrr"], serie[i]["mrr"]
            var = ((curr - prev) / prev) if prev > 0 else 0.0
            variaciones_mrr.append((serie[i], var, i))
        # Excluir arranque (meses de índice 1 a 5 inclusive, es decir los primeros 6 meses del negocio)
        eval_mrr = [item for item in variaciones_mrr if item[2] >= ARRANQUE]
        if len(eval_mrr) >= 3:
            zs_mrr = puntajes_z([item[1] for item in eval_mrr])
            atipicos_mrr = [(item[0], z, item[1]) for item, z in zip(eval_mrr, zs_mrr) if abs(z) > UMBRAL_Z]
            if atipicos_mrr:
                plural = len(atipicos_mrr) > 1
                meses_txt = ", ".join(f"{s['etiqueta']} ({'+' if v>=0 else ''}{dec(v*100)}%, z={dec(z)})" for s, z, v in atipicos_mrr[:3])
                salida.append({
                    "titulo": f"¿Variación atípica en MRR ({len(atipicos_mrr)} mes{'es' if plural else ''})?",
                    "texto": f"¿Por qué en {meses_txt} el MRR tuvo una variación inusual respecto a la tendencia (|z| > 2, fase post-arranque)?",
                    "tipo": "atípico", "color": "rojo", "claves": [s["fecha_id"] // 100 for s, _, _ in atipicos_mrr],
                })
            else:
                salida.append({
                    "titulo": "Sin meses atípicos en MRR",
                    "texto": "La variación mensual del MRR se mantiene en rangos estadísticamente esperados (|z| ≤ 2).",
                    "tipo": "sin alerta", "color": "teal", "claves": [],
                })

    # Serie C: Cuentas activas (variación mensual, excluyendo primeros 6 meses)
    if len(serie) > 1:
        variaciones_cuentas = []
        for i in range(1, len(serie)):
            prev, curr = serie[i - 1]["cuentas"], serie[i]["cuentas"]
            var = ((curr - prev) / prev) if prev > 0 else 0.0
            variaciones_cuentas.append((serie[i], var, i))
        eval_cuentas = [item for item in variaciones_cuentas if item[2] >= ARRANQUE]
        if len(eval_cuentas) >= 3:
            zs_cuentas = puntajes_z([item[1] for item in eval_cuentas])
            atipicos_cuentas = [(item[0], z, item[1]) for item, z in zip(eval_cuentas, zs_cuentas) if abs(z) > UMBRAL_Z]
            if atipicos_cuentas:
                plural = len(atipicos_cuentas) > 1
                meses_txt = ", ".join(f"{s['etiqueta']} ({'+' if v>=0 else ''}{dec(v*100)}%, z={dec(z)})" for s, z, v in atipicos_cuentas[:3])
                salida.append({
                    "titulo": f"¿Crecimiento/caída atípica de cuentas activas ({len(atipicos_cuentas)} mes{'es' if plural else ''})?",
                    "texto": f"¿Qué impulsó la alteración de cuentas en {meses_txt} (|z| > 2)?",
                    "tipo": "atípico", "color": "rojo", "claves": [s["fecha_id"] // 100 for s, _, _ in atipicos_cuentas],
                })
            else:
                salida.append({
                    "titulo": "Sin meses atípicos en cuentas activas",
                    "texto": "El ritmo de altas y bajas de cuentas se mantiene estable y predecible.",
                    "tipo": "sin alerta", "color": "teal", "claves": [],
                })

    # Serie D: Cancelación mensual (%)
    if len(serie) >= 3:
        churn_rates = []
        for i, s in enumerate(serie):
            tasa = (s["cancelaciones"] / s["filas"]) if s["filas"] > 0 else 0.0
            churn_rates.append((s, tasa, i))
        eval_churn = [item for item in churn_rates if item[2] >= ARRANQUE] if len(churn_rates) > ARRANQUE else churn_rates
        if len(eval_churn) >= 3:
            zs_churn = puntajes_z([item[1] for item in eval_churn])
            atipicos_churn = [(item[0], z, item[1]) for item, z in zip(eval_churn, zs_churn) if abs(z) > UMBRAL_Z]
            if atipicos_churn:
                plural = len(atipicos_churn) > 1
                meses_txt = ", ".join(f"{s['etiqueta']} ({dec(v*100, 2)}%, z={dec(z)})" for s, z, v in atipicos_churn[:3])
                salida.append({
                    "titulo": f"¿Pico atípico en cancelación mensual ({len(atipicos_churn)} mes{'es' if plural else ''})?",
                    "texto": f"¿Por qué en {meses_txt} la tasa de cancelación mensual se disparó (|z| > 2)?",
                    "tipo": "atípico", "color": "rojo", "claves": [s["fecha_id"] // 100 for s, _, _ in atipicos_churn],
                })
            else:
                salida.append({
                    "titulo": "Sin meses atípicos en cancelación mensual",
                    "texto": "La tasa mensual de cancelación no muestra picos anómalos (|z| ≤ 2).",
                    "tipo": "sin alerta", "color": "teal", "claves": [],
                })

    # Serie E: Uso por suscripción (eventos del mes ÷ suscripciones del mes)
    if serie and uso:
        mapa_eventos = {u["clave"]: u["eventos"] for u in uso}
        uso_por_sub = []
        for i, s in enumerate(serie):
            clave_m = s["fecha_id"] // 100
            ev = mapa_eventos.get(clave_m, 0)
            ratio = (ev / s["filas"]) if s["filas"] > 0 else 0.0
            uso_por_sub.append((s, ratio, i))
        eval_uso_sub = [item for item in uso_por_sub if item[2] >= ARRANQUE] if len(uso_por_sub) > ARRANQUE else uso_por_sub
        if len(eval_uso_sub) >= 3:
            zs_usosub = puntajes_z([item[1] for item in eval_uso_sub])
            atipicos_usosub = [(item[0], z, item[1]) for item, z in zip(eval_uso_sub, zs_usosub) if abs(z) > UMBRAL_Z]
            if atipicos_usosub:
                plural = len(atipicos_usosub) > 1
                meses_txt = ", ".join(f"{s['etiqueta']} ({dec(v, 2)} eventos/sub, z={dec(z)})" for s, z, v in atipicos_usosub[:3])
                salida.append({
                    "titulo": f"¿Anomalía en uso por suscripción ({len(atipicos_usosub)} mes{'es' if plural else ''})?",
                    "texto": f"¿Qué motivó el comportamiento de uso en {meses_txt} (|z| > 2)?",
                    "tipo": "atípico", "color": "rojo", "claves": [s["fecha_id"] // 100 for s, _, _ in atipicos_usosub],
                })
            else:
                salida.append({
                    "titulo": "Sin meses atípicos en uso por suscripción",
                    "texto": "La relación de eventos por cliente activo se mantiene dentro de los márgenes normales.",
                    "tipo": "sin alerta", "color": "teal", "claves": [],
                })

    # 4. Diferencias de cancelación entre planes.
    planes_ = [p for p in por_plan(f) if p["cancelacion"] is not None]
    if len(planes_) >= 2:
        alto = max(planes_, key=lambda p: p["cancelacion"])
        bajo = min(planes_, key=lambda p: p["cancelacion"])
        puntos = (alto["cancelacion"] - bajo["cancelacion"]) * 100
        fuerte = puntos >= 3
        salida.append({
            "titulo": f"Cancelación {bajo['nombre']} vs {alto['nombre']}",
            "texto": f"{dec(bajo['cancelacion'] * 100, 2)} % vs {dec(alto['cancelacion'] * 100, 2)} %. "
                     f"Diferencia de {dec(puntos)} puntos: "
                     + ("revisar qué pasa con ese plan." if fuerte else "vigilar, todavía no concluir."),
            "tipo": "alerta" if fuerte else "vigilar", "color": "ambar" if fuerte else "teal",
        })
    return salida, uso
