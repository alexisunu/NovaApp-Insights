"""
Control de acceso por rol (Hito 2 · E10).

Los roles son grupos de Django; no hay tablas propias para permisos:
  - "Dirección"            → todas las pantallas y todos los datos.
  - "Líder · <zona>"       → Tablero, Explorar, Alertas y Métricas, solo con las
                             cuentas de su zona (filtro de filas en el servidor).
  - "Soporte"              → Tablero y Métricas, solo uso del producto; el MRR no
                             se calcula para este rol (columna oculta en el servidor).
Un superusuario cuenta como Dirección.

El filtro se aplica al consultar la base, no al dibujar la pantalla: la vista
nunca recibe las filas que el rol no puede ver.
"""
from dataclasses import dataclass, field
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import RegistroAcceso

GRUPO_DIRECCION = "Dirección"
GRUPO_SOPORTE = "Soporte"
PREFIJO_LIDER = "Líder · "

PANTALLAS_HITO1 = ("carga", "calidad", "privacidad", "modelo")
PANTALLAS_HITO2 = ("tablero", "explorar", "alertas", "metricas", "accesos")

ROLES = {
    "direccion": {
        "nombre": "Dirección",
        "pantallas": PANTALLAS_HITO2 + PANTALLAS_HITO1,
        "datos": "Todas las zonas",
        "regla": "—",
    },
    "lider": {
        "nombre": "Líder de zona",
        "pantallas": ("tablero", "explorar", "alertas", "metricas"),
        "datos": "Solo su zona",
        "regla": "cuenta.zona = zona del usuario",
    },
    "soporte": {
        "nombre": "Soporte",
        "pantallas": ("tablero", "metricas"),
        "datos": "Solo uso, sin MRR",
        "regla": "mrr no se consulta",
    },
}


@dataclass
class Perfil:
    clave: str | None          # direccion / lider / soporte / None (sin rol)
    nombre: str
    usuario: str
    zona: str | None = None     # solo para líderes
    pantallas: tuple = field(default_factory=tuple)

    @property
    def ver_mrr(self):
        return self.clave in ("direccion", "lider")

    @property
    def puede_cargar(self):
        return self.clave == "direccion"

    @property
    def etiqueta(self):
        return f"{self.nombre} · {self.zona}" if self.zona else self.nombre

    def puede(self, pantalla):
        return pantalla in self.pantallas

    @property
    def inicio(self):
        return self.pantallas[0] if self.pantallas else None


def perfil_de(usuario):
    """Traduce los grupos del usuario a un perfil. Gana el rol con más permisos."""
    nombre_usuario = usuario.get_username()
    grupos = set(usuario.groups.values_list("name", flat=True))
    if usuario.is_superuser or GRUPO_DIRECCION in grupos:
        clave, zona = "direccion", None
    else:
        lideres = sorted(g[len(PREFIJO_LIDER):].strip() for g in grupos if g.startswith(PREFIJO_LIDER))
        if lideres:
            clave, zona = "lider", lideres[0]
        elif GRUPO_SOPORTE in grupos:
            clave, zona = "soporte", None
        else:
            return Perfil(None, "Sin rol", nombre_usuario)
    rol = ROLES[clave]
    return Perfil(clave, rol["nombre"], nombre_usuario, zona, rol["pantallas"])


def registrar(perfil, pantalla):
    RegistroAcceso.objects.create(
        usuario=perfil.usuario, rol=perfil.nombre, zona=perfil.zona or "", pantalla=pantalla,
    )


def requiere(pantalla):
    """Exige sesión y que el rol tenga la pantalla; deja constancia del acceso."""
    def decorador(vista):
        @login_required(login_url="dashboard:entrar")
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            perfil = perfil_de(request.user)
            request.perfil = perfil
            if not perfil.puede(pantalla):
                # 403 real: la vista ni siquiera se ejecuta, no solo se oculta el enlace.
                return render(request, "sin_permiso.html",
                              {"perfil": perfil, "pantalla": pantalla, "ocultar_estado": True}, status=403)
            if request.method == "GET":
                registrar(perfil, pantalla)
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


@login_required(login_url="dashboard:entrar")
def inicio(request):
    """La raíz lleva a la primera pantalla que el rol puede ver."""
    perfil = perfil_de(request.user)
    request.perfil = perfil
    if not perfil.inicio:
        return render(request, "sin_permiso.html", {"perfil": perfil, "pantalla": None, "ocultar_estado": True}, status=403)
    return redirect(f"dashboard:{perfil.inicio}")
