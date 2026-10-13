"""
Vistas del Hito 2 — "el sistema muestra resultados".

Tablero (tres niveles + segmentación), Explorar, Alertas, Métricas y Accesos.
Cada vista recibe request.perfil (lo pone acceso.requiere) y arma un Filtro con
la zona del rol: los números cambian según quién entra.
"""
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import Group
from django.db.models import Q

from . import graficos
from . import metricas as m
from .acceso import GRUPO_DIRECCION, GRUPO_SOPORTE, PREFIJO_LIDER, ROLES, requiere
from .models import RegistroAcceso
from .views import _base, _local
from django.shortcuts import render


# ───────────── formato ─────────────

def dinero(valor):
    if valor is None:
        return "—"
    if abs(valor) >= 1e6:
        return f"${m.dec(valor / 1e6)} M"
    return f"${m.n(valor)}"


def pct(valor, cifras=1, signo=False):
    if valor is None:
        return "—"
    texto = m.dec(abs(valor) * 100, cifras)
    if valor < 0:
        texto = "−" + texto
    elif signo and valor > 0:
        texto = "+" + texto
    return f"{texto} %"


NOMBRES_PANTALLA = {"tablero": "Tablero", "explorar": "Explorar", "alertas": "Alertas",
                    "metricas": "Métricas", "accesos": "Accesos"}


def barras(filas, formato, destacado=None, campo="valor"):
    """Filas para el componente de barras horizontales (ancho relativo al máximo)."""
    maximo = max((f[campo] or 0 for f in filas), default=0) or 1
    return [{
        "nombre": f["nombre"], "texto": formato(f[campo]),
        "ancho": max(0.5, round(100 * (f[campo] or 0) / maximo, 1)),
        "destacado": destacado is not None and f["nombre"] == destacado,
    } for f in filas]


# ───────────── filtro desde la URL ─────────────

def _entero(texto, validos):
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    return valor if valor in validos else None


def filtro_de(request):
    perfil = request.perfil
    zonas = m.zonas()
    if perfil.clave == "lider":
        zona = perfil.zona  # el rol manda; lo que venga en la URL se ignora
    else:
        zona = request.GET.get("zona") if request.GET.get("zona") in zonas else None
    planes = m.planes()
    plan_id = _entero(request.GET.get("plan"), {p["plan_id"] for p in planes})
    anio = _entero(request.GET.get("anio"), set(m.anios()))
    filtro = m.Filtro(zona=zona, plan_id=plan_id, anio=anio, ver_mrr=perfil.ver_mrr)
    controles = {
        "zonas": zonas, "planes": planes, "anios": m.anios(),
        "zona": zona, "plan_id": plan_id, "anio": anio,
        "zona_fija": perfil.clave == "lider",
    }
    return filtro, controles


def _contexto(request, pagina):
    ctx = _base(pagina)
    ctx["perfil"] = request.perfil
    return ctx


# ───────────── Tablero ─────────────

def _hallazgo_mrr(k):
    """Título con el hallazgo del gráfico de tendencia (E14)."""
    if k["desde_max"] is not None and k["desde_max"] < -0.05:
        return (f"Llegó a su máximo en {k['mrr_max']['etiqueta']} y el último mes está "
                f"{pct(-k['desde_max'])} por debajo.")
    if k["variacion"] is not None and k["variacion"] < 0:
        return f"Bajó {pct(-k['variacion'])} frente a {k['mes_previo']}."
    return f"El último mes ({k['mes']}) está en su máximo o cerca de él."


@requiere("tablero")
def tablero(request):
    ctx = _contexto(request, "tablero")
    f, controles = filtro_de(request)
    k = m.indicadores(f)
    ctx.update({"controles": controles, "k": k})
    if not k:
        return render(request, "tablero.html", ctx)

    tarjetas = []
    if f.ver_mrr:
        variacion = k["variacion"]
        tarjetas += [
            {"nombre": f"MRR {k['mes']}", "valor": dinero(k["mrr"]),
             "detalle": f"{pct(variacion, signo=True)} vs {k['mes_previo']}" if variacion is not None else "primer mes",
             "tono": "mal" if (variacion or 0) < 0 else "bien"},
            {"nombre": "Ingreso por cuenta de pago", "valor": dinero(k["arpa"]),
             "detalle": f"{m.n(k['cuentas_pago'])} cuentas de pago · sin Free", "tono": ""},
        ]
    tarjetas += [
        {"nombre": "Cuentas activas", "valor": m.n(k["cuentas"]),
         "detalle": f"{k['mes']} · máximo {m.n(k['cuentas_max']['cuentas'])} ({k['cuentas_max']['etiqueta']})",
         "tono": "mal" if k["cuentas"] < k["cuentas_max"]["cuentas"] else ""},
        {"nombre": "Cancelación mensual", "valor": pct(k["cancelacion"], 2),
         "detalle": f"promedio {k['cancelacion_desde']} a {k['mes']}", "tono": ""},
        {"nombre": "Uso por cuenta activa", "valor": m.dec(k["uso_por_cuenta"] or 0),
         "detalle": f"eventos en {k['mes']} ({m.n(k['eventos_mes'])} en total)", "tono": ""},
        {"nombre": "Sobre el límite de su plan", "valor": pct(k["sobre_limite"]),
         "detalle": (f"{m.n(k['sobre_limite_cuentas'])} cuenta{'s' if k['sobre_limite_cuentas'] != 1 else ''} "
                     f"candidata{'s' if k['sobre_limite_cuentas'] != 1 else ''} a subir de plan"), "tono": ""},
    ]
    ctx["tarjetas"] = tarjetas

    if f.ver_mrr:
        serie = m.serie_mensual(f)
        ctx["tendencia"] = {
            "titulo": "MRR por mes",
            "hallazgo": _hallazgo_mrr(k),
            "svg": graficos.linea([(s["fecha_id"], s["etiqueta"], s["mrr"]) for s in serie], dinero, "MRR"),
        }
        ctx["por_plan"] = barras(m.mrr_por(f, "plan__nombre_plan"), dinero)
        free = [p for p in m.por_plan(f) if p["plan__precio_mensual"] == 0]
        ctx["nota_free"] = (f"{free[0]['nombre']}: {m.n(free[0]['cuentas'])} cuentas y $0. No es un error."
                            if free else "")
        campo, titulo = ("cuenta__industria", "Por industria") if f.zona else ("cuenta__zona", "Por zona")
        ctx["por_grupo"] = {"titulo": titulo, "filas": barras(m.mrr_por(f, campo), dinero),
                            "sub": f"Solo {f.zona}" if f.zona else "País generalizado a zona por privacidad (E3)"}
    else:
        uso = m.uso_mensual(f)
        ctx["tendencia"] = {
            "titulo": "Eventos de uso por mes",
            "hallazgo": "Sin MRR: este rol solo ve el uso del producto.",
            "svg": graficos.columnas([(u["clave"], u["etiqueta"], u["eventos"]) for u in uso], m.n, (), "Eventos de uso"),
        }
    ctx["por_funcion"] = barras(m.uso_por_funcion(f), m.n)
    return render(request, "tablero.html", ctx)


# ───────────── Explorar ─────────────

@requiere("explorar")
def explorar(request):
    ctx = _contexto(request, "explorar")
    f, controles = filtro_de(request)
    planes = m.por_plan(f)
    elegido = next((p for p in planes if p["plan_id"] == f.plan_id), planes[0] if planes else None)
    ctx.update({"controles": controles, "planes_datos": planes, "elegido": elegido})
    if elegido:
        tarjetas = []
        if f.ver_mrr:
            tarjetas.append({"nombre": "MRR acumulado", "valor": dinero(elegido["mrr"]),
                             "detalle": f"{controles['anio'] or 'todo el periodo'}"})
        tarjetas += [
            {"nombre": "Cuentas", "valor": m.n(elegido["cuentas"]), "detalle": "que tuvieron este plan"},
            {"nombre": "Cancelación mensual", "valor": pct(elegido["cancelacion"], 2), "detalle": "promedio del plan"},
        ]
        if f.ver_mrr:
            tarjetas.append({"nombre": "Participación MRR", "valor": pct(elegido["participacion"]),
                             "detalle": "del total" + (f" de {f.zona}" if f.zona else "")})
        ordenados = sorted(planes, key=lambda p: -(p["cancelacion"] or 0))
        diferencia = ((ordenados[0]["cancelacion"] or 0) - (ordenados[-1]["cancelacion"] or 0)) * 100
        ctx.update({
            "tarjetas": tarjetas,
            "cancelacion": barras([{"nombre": p["nombre"], "valor": p["cancelacion"] or 0} for p in ordenados],
                                  lambda v: pct(v, 2), elegido["nombre"]),
            "cancelacion_sub": (f"% mensual. Entre el plan más alto y el más bajo hay {m.dec(diferencia)} puntos"
                                + (": no concluir sin más datos." if diferencia < 3 else ".")),
            "cuentas": barras([{"nombre": p["nombre"], "valor": p["cuentas"]}
                               for p in sorted(planes, key=lambda p: -p["cuentas"])], m.n, elegido["nombre"]),
        })
    return render(request, "explorar.html", ctx)


# ───────────── Alertas ─────────────

@requiere("alertas")
def alertas(request):
    ctx = _contexto(request, "alertas")
    f, controles = filtro_de(request)
    ultima = ctx["ultima"]
    registro = 0
    if ultima:
        regla = ultima.bitacora.filter(regla_aplicada__icontains="posterior a la primera").first()
        registro = regla.filas_afectadas if regla else 0
    lista, uso = m.alertas(f, registro if not f.zona else 0)
    atipicos = {u["clave"] for u, _ in m.meses_atipicos(uso)}
    ctx.update({
        "controles": controles, "alertas": lista,
        "svg": graficos.columnas([(u["clave"], u["etiqueta"], u["por_dia"]) for u in uso],
                                 lambda v: f"{m.dec(v)} eventos por día", atipicos, "Eventos de uso por día"),
        "hay_atipicos": bool(atipicos),
    })
    return render(request, "alertas.html", ctx)


# ───────────── Métricas ─────────────

@requiere("metricas")
def metricas(request):
    ctx = _contexto(request, "metricas")
    f, controles = filtro_de(request)
    k = m.indicadores(f) or {}
    valores = {
        "mrr": dinero(k.get("mrr")) if f.ver_mrr else None,
        "variacion": pct(k.get("variacion"), signo=True) if f.ver_mrr else None,
        "cuentas": m.n(k["cuentas"]) if k else "—",
        "cancelacion": pct(k.get("cancelacion"), 2),
        "arpa": dinero(k.get("arpa")) if f.ver_mrr else None,
        "uso_por_cuenta": m.dec(k["uso_por_cuenta"]) if k.get("uso_por_cuenta") is not None else "—",
        "sobre_limite": pct(k.get("sobre_limite")),
    }
    fichas = []
    for ficha in m.FICHAS:
        valor = valores.get(ficha["clave"])
        fichas.append({**ficha, "valor": valor if valor is not None else "no visible para su rol"})
    ctx.update({"controles": controles, "fichas": fichas, "mes": k.get("mes")})
    return render(request, "metricas.html", ctx)


# ───────────── Accesos ─────────────

@requiere("accesos")
def accesos(request):
    ctx = _contexto(request, "accesos")
    roles = [{"clave": c, **r, "pantallas_txt": ", ".join(NOMBRES_PANTALLA[p] for p in r["pantallas"]
                                                       if p in NOMBRES_PANTALLA)
              + (" + Carga, Calidad, Privacidad y Modelo" if "carga" in r["pantallas"] else "")}
             for c, r in ROLES.items()]
    prueba = []
    total = 0.0
    for zona in m.zonas():
        valor = sum(x["valor"] for x in m.mrr_por(m.Filtro(zona=zona), "cuenta__zona"))
        total += valor
        prueba.append({"rol": f"Líder · {zona}", "valor": dinero(valor)})
    prueba.append({"rol": "Dirección (todas las zonas)", "valor": dinero(total), "total": True})
    prueba.append({"rol": "Soporte", "valor": "no se consulta"})

    grupos = Group.objects.filter(Q(name__in=[GRUPO_DIRECCION, GRUPO_SOPORTE]) | Q(name__startswith=PREFIJO_LIDER))
    usuarios = []
    for g in grupos.order_by("name"):
        for u in g.user_set.order_by("username"):
            usuarios.append({"usuario": u.username, "grupo": g.name, "activo": u.is_active})
    registro = [{"hora": _local(r.fecha_hora), **{k: getattr(r, k) for k in ("usuario", "rol", "zona", "pantalla")}}
                for r in RegistroAcceso.objects.all()[:20]]
    ctx.update({"roles": roles, "prueba": prueba, "usuarios": usuarios, "registro": registro})
    return render(request, "accesos.html", ctx)


# ───────────── sesión ─────────────

class Entrar(auth_views.LoginView):
    template_name = "entrar.html"
    redirect_authenticated_user = True
    next_page = "dashboard:inicio"


class Salir(auth_views.LogoutView):
    next_page = "dashboard:entrar"
