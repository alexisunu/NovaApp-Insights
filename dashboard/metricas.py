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

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
PAGO = Q(plan__precio_mensual__gt=0)
UMBRAL_Z = 2.0

FICHAS = [
    {
        "clave": "mrr", "nombre": "MRR del mes",
        "formula": "SUMA(mrr) de fact_suscripciones en el último mes",
        "mide": "Cuánto dinero entra cada mes por suscripciones.",
        "decision": "Si el negocio crece o se encoge; base de toda proyección de caja.",
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
        "clave": "cuentas", "nombre": "Cuentas activas",
        "formula": "CONTAR DISTINTOS(cuenta_id) con suscripción en el último mes",
        "mide": "Cuántas empresas usan y pagan (o tienen plan Free) hoy.",
        "decision": "Dimensionar soporte y metas comerciales.",
        "tipo": "Base",
    },
    {
        "clave": "cancelacion", "nombre": "Cancelación mensual",
        "formula": "SUMA(churn) / CONTAR(filas) de los últimos 12 meses",
        "mide": "Qué parte de las cuentas se va cada mes, en promedio.",
        "decision": "Quién se está yendo: priorizar retención por plan o zona.",
        "tipo": "Base",
    },
    {
        "clave": "arpa", "nombre": "Ingreso promedio por cuenta de pago (ARPA)",
        "formula": "SUMA(mrr) / CONTAR DISTINTOS(cuenta_id) del último mes, FILTRANDO precio_mensual > 0",
        "mide": "Cuánto paga en promedio una cuenta que sí paga.",
        "decision": "Evaluar si conviene subir de plan a los clientes o cambiar precios.",
        "tipo": "Filtro modificado (excluye Free)",
    },
    {
        "clave": "uso_por_cuenta", "nombre": "Eventos de uso por cuenta activa",
        "formula": "CONTAR(fact_uso del último mes) / Cuentas activas",
        "mide": "Qué tanto usan el producto las cuentas que lo tienen.",
        "decision": "Alertar cuentas que pagan pero no usan (riesgo de cancelación).",
        "tipo": "Combina los dos hechos",
    },
    {
        "clave": "sobre_limite", "nombre": "Cuentas por encima del límite de su plan",
        "formula": "CONTAR DISTINTOS(cuenta_id con excede_limite) / Cuentas activas, último mes",
        "mide": "Clientes con más usuarios de los que su plan permite.",
        "decision": "Lista de candidatos para ofrecer un plan superior.",
        "tipo": "Base",
    },
]


@dataclass
class Filtro:
    zona: str | None = None        # alcance del rol o segmentación elegida
    plan_id: int | None = None     # segmentación (no aplica al uso: fact_uso no tiene plan)
    anio: int | None = None
    ver_mrr: bool = True

    def suscripciones(self):
        qs = FactSuscripciones.objects.all()
        if self.zona:
            qs = qs.filter(cuenta__zona=self.zona)
        if self.plan_id:
            qs = qs.filter(plan_id=self.plan_id)
        if self.anio:
            qs = qs.filter(fecha__anio=self.anio)
        return qs

    def usos(self):
        qs = FactUso.objects.all()
        if self.zona:
            qs = qs.filter(cuenta__zona=self.zona)
        if self.anio:
            qs = qs.filter(fecha__anio=self.anio)
        return qs

    def sin_plan(self):
        return Filtro(self.zona, None, self.anio, self.ver_mrr)


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


def zonas():
    return list(DimCuenta.objects.exclude(zona="").values_list("zona", flat=True).distinct().order_by("zona"))


def planes():
    return list(DimPlan.objects.order_by("-precio_mensual").values("plan_id", "nombre_plan", "precio_mensual"))


def anios():
    return list(FactSuscripciones.objects.values_list("fecha__anio", flat=True).distinct().order_by("fecha__anio"))


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

    # 3. Meses atípicos en el uso: |z| > 2 sobre eventos por día.
    if uso:
        atipicos = meses_atipicos(uso)
        crudos = puntajes_z([u["eventos"] for u in uso])
        solo_calendario = sorted({u["etiqueta"][:3] for u, z in zip(uso, crudos) if abs(z) > UMBRAL_Z}
                                 - {u["etiqueta"][:3] for u, _ in atipicos})
        diarios = [u["por_dia"] for u in uso]
        if atipicos:
            plural = len(atipicos) > 1
            salida.append({
                "titulo": f"{len(atipicos)} mes{'es' if plural else ''} atípico{'s' if plural else ''} en el uso",
                "texto": "Se sale" + ("n" if plural else "") + " de lo normal aun comparando por día (|z| > 2): "
                         + ", ".join(f"{u['etiqueta']} ({n(u['eventos'])} eventos, z = {dec(z)})" for u, z in atipicos[:4])
                         + ". Es una pregunta: revisar qué pasó ese mes antes de concluir.",
                "tipo": "atípico", "color": "rojo",
            })
        else:
            texto = (f"Comparando por día, todos los meses están entre {dec(min(diarios))} y {dec(max(diarios))} "
                     "eventos diarios. El sistema no inventa alertas.")
            if solo_calendario:
                texto += (f" Contando eventos totales, {', '.join(solo_calendario)} parecería bajo, pero es "
                          "estacionalidad de calendario: tiene menos días.")
            salida.append({
                "titulo": "Ningún mes atípico en el uso",
                "texto": texto, "tipo": "sin alerta", "color": "teal",
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
