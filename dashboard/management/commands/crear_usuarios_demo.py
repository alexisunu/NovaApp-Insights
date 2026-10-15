"""
Crea los grupos de roles y un usuario de demostración por rol.

    python manage.py crear_usuarios_demo --clave "una-clave-para-la-demo"

Usuarios: direccion, soporte y lider_<zona> (uno por cada zona que exista en
dim_cuenta; si la base está vacía, usa las cuatro zonas del ETL).
Se puede correr varias veces: actualiza la clave y los grupos sin duplicar.
La clave no se guarda en el repositorio: se pasa al ejecutar el comando.
"""
import unicodedata

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from dashboard.acceso import GRUPO_DIRECCION, GRUPO_SOPORTE, PREFIJO_LIDER
from dashboard.metricas import paises

PAISES_CANONICOS = [
    "Argentina", "Chile", "Colombia", "Ecuador", "España", "México", "Perú", "Uruguay",
]


def _slug(texto):
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return sin_tildes.lower().replace(" ", "_")


class Command(BaseCommand):
    help = "Crea los grupos Dirección, Soporte y Líder · <país>, con un usuario de prueba para cada uno."

    def add_arguments(self, parser):
        parser.add_argument("--clave", required=True, help="Contraseña para todos los usuarios de demostración")

    def handle(self, *args, **options):
        clave = options["clave"]
        if len(clave) < 8:
            raise CommandError("Use una clave de al menos 8 caracteres.")

        Usuario = get_user_model()
        lista_paises = paises() or PAISES_CANONICOS
        cuentas = [("direccion", GRUPO_DIRECCION), ("soporte", GRUPO_SOPORTE)]
        cuentas += [(f"lider_{_slug(p)}", f"{PREFIJO_LIDER}{p}") for p in lista_paises]

        for usuario, nombre_grupo in cuentas:
            grupo, _ = Group.objects.get_or_create(name=nombre_grupo)
            u, creado = Usuario.objects.get_or_create(username=usuario)
            u.set_password(clave)
            u.is_active = True
            u.save()
            u.groups.set([grupo])
            self.stdout.write(f"  {'creado' if creado else 'actualizado'}: {usuario:<18} → {nombre_grupo}")

        self.stdout.write(self.style.SUCCESS(f"Listo: {len(cuentas)} usuarios de demostración."))
