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
    paises = m.paises()
    if perfil.clave == "lider":
        pais = perfil.pais  # el rol manda; lo que venga en la URL se ignora
    else:
        pais = request.GET.get("pais") if request.GET.get("pais") in paises else None
    planes = m.planes()
    plan_id = _entero(request.GET.get("plan"), {p["plan_id"] for p in planes})
    anio = _entero(request.GET.get("anio"), set(m.anios()))
    filtro = m.Filtro(pais=pais, plan_id=plan_id, anio=anio, ver_mrr=perfil.ver_mrr)
    controles = {
        "paises": paises, "planes": planes, "anios": m.anios(),
        "pais": pais, "plan_id": plan_id, "anio": anio,
        "pais_fijo": perfil.clave == "lider",
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
    guia = m.indicadores_guia(f)
    ctx.update({"controles": controles, "k": k, "guia": guia})
    if not k and not guia:
        return render(request, "tablero.html", ctx)

    tarjetas = []
    if guia:
        if f.ver_mrr:
            tarjetas.append({
                "nombre": "MRR dic-2025",
                "valor": dinero(guia["mrr"]),
                "detalle": f"Suma {guia['mes_referencia']}",
                "tono": "",
            })
        tarjetas.append({
            "nombre": "Cuentas activas dic-2025",
            "valor": m.n(guia["cuentas"]),
            "detalle": f"Cuentas {guia['mes_referencia']}",
            "tono": "",
        })
        if f.ver_mrr:
            tarjetas.append({
                "nombre": "ARPA dic-2025",
                "valor": dinero(guia["arpa"]),
                "detalle": f"{m.n(guia['cuentas_pago'])} cuentas de pago · sin Free",
                "tono": "",
            })
        tarjetas.append({
            "nombre": "Cancelación mensual 2025",
            "valor": pct(guia["cancelacion"], 2),
            "detalle": f"Promedio año {guia['anio_referencia']}",
            "tono": "",
        })
        tarjetas.append({
            "nombre": "Cuentas pagas dic-2025",
            "valor": pct(guia["pct_cuentas_pago"], 1),
            "detalle": f"{m.n(guia['cuentas_pago'])} de {m.n(guia['cuentas'])} cuentas",
            "tono": "",
        })
        tarjetas.append({
            "nombre": "Uso por cuenta-mes 2025",
            "valor": m.dec(guia["uso_por_cuenta_mes"], 2),
            "detalle": f"{m.n(guia['eventos_2025'])} eventos ÷ {m.n(guia['subs_2025'])} suscripciones",
            "tono": "",
        })
    ctx["tarjetas"] = tarjetas

    if f.ver_mrr and k:
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
        campo, titulo = ("cuenta__industria", "Por industria") if f.pais else ("cuenta__pais", "Por país")
        ctx["por_grupo"] = {"titulo": titulo, "filas": barras(m.mrr_por(f, campo), dinero),
                            "sub": f"Solo {f.pais}" if f.pais else "Todas las cuentas por país"}
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
                             "detalle": "del total" + (f" de {f.pais}" if f.pais else "")})
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
    lista, uso = m.alertas(f, registro if not f.pais else 0)
    atipicos_claves = set()
    for a in lista:
        if a.get("tipo") == "atípico" and "claves" in a:
            atipicos_claves.update(a["claves"])
    ctx.update({
        "controles": controles, "alertas": lista,
        "svg": graficos.columnas([(u["clave"], u["etiqueta"], u["por_dia"]) for u in uso],
                                 lambda v: f"{m.dec(v)} eventos por día", atipicos_claves, "Eventos de uso por día"),
        "hay_atipicos": bool(atipicos_claves),
    })
    return render(request, "alertas.html", ctx)


# ───────────── Métricas ─────────────

@requiere("metricas")
def metricas(request):
    ctx = _contexto(request, "metricas")
    f, controles = filtro_de(request)
    k = m.indicadores(f) or {}
    guia = m.indicadores_guia(f) or {}

    valores_guia = {
        "mrr": dinero(guia.get("mrr")) if f.ver_mrr and guia.get("mrr") is not None else "no visible para su rol",
        "cuentas": m.n(guia["cuentas"]) if "cuentas" in guia else "—",
        "arpa": dinero(guia.get("arpa")) if f.ver_mrr and guia.get("arpa") is not None else "no visible para su rol",
        "cancelacion": pct(guia.get("cancelacion"), 2) if guia.get("cancelacion") is not None else "—",
        "pct_cuentas_pago": pct(guia.get("pct_cuentas_pago"), 2) if guia.get("pct_cuentas_pago") is not None else "—",
        "uso_por_cuenta_mes": m.dec(guia.get("uso_por_cuenta_mes"), 2) if guia.get("uso_por_cuenta_mes") is not None else "—",
    }
    fichas_guia = []
    for fg in m.FICHAS_GUIA:
        if not f.ver_mrr and fg["clave"] in ("mrr", "arpa"):
            continue
        v = valores_guia.get(fg["clave"], "—")
        fichas_guia.append({**fg, "valor": v})

    valores_comp = {
        "mrr_mes": dinero(k.get("mrr")) if f.ver_mrr and k.get("mrr") is not None else "no visible para su rol",
        "variacion": pct(k.get("variacion"), signo=True) if f.ver_mrr and k.get("variacion") is not None else "—",
        "cancelacion_ult12": pct(k.get("cancelacion"), 2) if k.get("cancelacion") is not None else "—",
        "sobre_limite": pct(k.get("sobre_limite")) if k.get("sobre_limite") is not None else "—",
    }
    fichas_comp = []
    for fc in m.FICHAS_COMPLEMENTARIAS:
        if not f.ver_mrr and fc["clave"] in ("mrr_mes", "variacion"):
            continue
        v = valores_comp.get(fc["clave"], "—")
        fichas_comp.append({**fc, "valor": v})

    ctx.update({
        "controles": controles,
        "fichas_guia": fichas_guia,
        "fichas_complementarias": fichas_comp,
        "fichas": fichas_guia + fichas_comp,
        "mes": k.get("mes"),
    })
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
    total_dic = 0.0
    for pais in m.paises():
        guia_p = m.indicadores_guia(m.Filtro(pais=pais))
        val = guia_p["mrr"] if (guia_p and guia_p["mrr"] is not None) else 0.0
        total_dic += val
        prueba.append({"rol": f"Líder · {pais}", "valor": dinero(val), "mrr_num": val})
    guia_dir = m.indicadores_guia(m.Filtro())
    mrr_dir = guia_dir["mrr"] if (guia_dir and guia_dir["mrr"] is not None) else total_dic
    prueba.append({"rol": "Dirección (todos los países)", "valor": dinero(mrr_dir), "total": True, "mrr_num": mrr_dir})
    prueba.append({"rol": "Soporte", "valor": "no se consulta (oculto)"})

    suma_coincide = abs(total_dic - mrr_dir) < 0.01

    grupos = Group.objects.filter(Q(name__in=[GRUPO_DIRECCION, GRUPO_SOPORTE]) | Q(name__startswith=PREFIJO_LIDER))
    usuarios = []
    for g in grupos.order_by("name"):
        for u in g.user_set.order_by("username"):
            usuarios.append({"usuario": u.username, "grupo": g.name, "activo": u.is_active})
    registro = [{"hora": _local(r.fecha_hora), **{k: getattr(r, k) for k in ("usuario", "rol", "zona", "pantalla")}}
                for r in RegistroAcceso.objects.all()[:20]]
    ctx.update({
        "roles": roles, "prueba": prueba, "usuarios": usuarios, "registro": registro,
        "total_dic_fmt": dinero(mrr_dir), "suma_paises_fmt": dinero(total_dic),
        "suma_coincide": suma_coincide,
    })
    return render(request, "accesos.html", ctx)


# ───────────── sesión ─────────────

class Entrar(auth_views.LoginView):
    template_name = "entrar.html"
    redirect_authenticated_user = True
    next_page = "dashboard:inicio"


class Salir(auth_views.LogoutView):
    next_page = "dashboard:entrar"
