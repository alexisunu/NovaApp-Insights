"""Pruebas del Hito 2: métricas, segmentación y control de acceso por rol (E8–E10).

El criterio del hito: al entrar con un perfil distinto, los números cambian.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import metricas as m
from .loader import cargar
from .models import RegistroAcceso
from .tests import datos_falsos

CLAVE = "clave-de-prueba-123"


def usuario(nombre, grupo=None):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    if grupo:
        u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class Hito2Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_dim_tiempo", verbosity=0)
        # Cuentas: 1 en "Norte" (plan Free, mrr 0) y 2 en "Sur" (una con Pro, mrr 49,9).
        datos = datos_falsos()
        datos["suscripciones"].loc[datos["suscripciones"]["sub_id"] == 2, "plan_id"] = 2  # Pro, de pago
        cargar(datos)
        cls.direccion = usuario("direccion", "Dirección")
        cls.lider_sur = usuario("lider_sur", "Líder · Sur")
        cls.lider_norte = usuario("lider_norte", "Líder · Norte")
        cls.soporte = usuario("soporte", "Soporte")
        cls.sin_rol = usuario("sin_rol")

    def como(self, u):
        self.client.force_login(u)


class MetricasTest(Hito2Base):
    def test_hay_al_menos_seis_indicadores_documentados(self):
        self.assertGreaterEqual(len(m.FICHAS), 6)
        tipos = " ".join(f["tipo"] for f in m.FICHAS)
        self.assertIn("Filtro modificado", tipos)
        self.assertIn("periodo anterior", tipos)
        for ficha in m.FICHAS:
            self.assertTrue(all(ficha[c] for c in ("nombre", "formula", "mide", "decision")))

    def test_la_zona_filtra_dentro_de_la_consulta(self):
        total = m.indicadores(m.Filtro())
        sur = m.indicadores(m.Filtro(zona="Sur"))
        norte = m.indicadores(m.Filtro(zona="Norte"))
        self.assertAlmostEqual(total["mrr"], 49.9, places=2)
        self.assertAlmostEqual(sur["mrr"], 49.9, places=2)
        self.assertEqual(norte["mrr"], 0)
        self.assertEqual(total["cuentas"], sur["cuentas"] + norte["cuentas"])

    def test_arpa_excluye_el_plan_free(self):
        k = m.indicadores(m.Filtro())
        self.assertEqual(k["cuentas_pago"], 1)
        self.assertAlmostEqual(k["arpa"], 49.9, places=2)

    def test_soporte_no_consulta_mrr(self):
        k = m.indicadores(m.Filtro(ver_mrr=False))
        self.assertNotIn("mrr", k)
        self.assertNotIn("arpa", k)
        self.assertTrue(all("mrr" not in fila for fila in m.serie_mensual(m.Filtro(ver_mrr=False))))


class AccesoTest(Hito2Base):
    def test_sin_sesion_pide_entrar(self):
        for nombre in ("dashboard:tablero", "dashboard:carga", "dashboard:accesos"):
            respuesta = self.client.get(reverse(nombre))
            self.assertEqual(respuesta.status_code, 302)
            self.assertIn(reverse("dashboard:entrar"), respuesta["Location"])

    def test_entrar_con_usuario_y_clave(self):
        respuesta = self.client.post(reverse("dashboard:entrar"), {"username": "lider_sur", "password": CLAVE})
        self.assertRedirects(respuesta, reverse("dashboard:inicio"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("dashboard:inicio")), reverse("dashboard:tablero"))

    def test_los_numeros_cambian_segun_el_rol(self):
        self.como(self.direccion)
        total = self.client.get(reverse("dashboard:tablero")).context["k"]["mrr"]
        self.como(self.lider_norte)
        norte = self.client.get(reverse("dashboard:tablero")).context["k"]["mrr"]
        self.assertNotEqual(total, norte)

    def test_el_lider_no_puede_cambiar_de_zona_por_la_url(self):
        self.como(self.lider_norte)
        respuesta = self.client.get(reverse("dashboard:tablero"), {"zona": "Sur"})
        self.assertEqual(respuesta.context["controles"]["zona"], "Norte")
        self.assertEqual(respuesta.context["k"]["mrr"], 0)

    def test_pantallas_prohibidas_responden_403(self):
        self.como(self.lider_sur)
        for nombre in ("dashboard:carga", "dashboard:calidad", "dashboard:accesos"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:reiniciar_base")).status_code, 403)
        self.como(self.soporte)
        self.assertEqual(self.client.get(reverse("dashboard:explorar")).status_code, 403)
        self.como(self.sin_rol)
        self.assertEqual(self.client.get(reverse("dashboard:tablero")).status_code, 403)

    def test_soporte_no_recibe_mrr_en_la_pagina(self):
        self.como(self.soporte)
        respuesta = self.client.get(reverse("dashboard:tablero"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("mrr", respuesta.context["k"])
        self.assertNotContains(respuesta, "MRR por plan")

    def test_cada_acceso_queda_registrado(self):
        self.como(self.lider_sur)
        self.client.get(reverse("dashboard:tablero"))
        self.client.get(reverse("dashboard:carga"))  # prohibida: no se registra como acceso
        registros = list(RegistroAcceso.objects.values_list("usuario", "zona", "pantalla"))
        self.assertEqual(registros, [("lider_sur", "Sur", "tablero")])


class PantallasHito2Test(Hito2Base):
    def test_todas_responden_para_direccion(self):
        self.como(self.direccion)
        for nombre in ("tablero", "explorar", "alertas", "metricas", "accesos"):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f"dashboard:{nombre}")).status_code, 200)

    def test_segmentacion_por_plan(self):
        self.como(self.direccion)
        free = m.planes()[-1]  # el de menor precio
        respuesta = self.client.get(reverse("dashboard:tablero"), {"plan": free["plan_id"]})
        self.assertEqual(respuesta.context["k"]["mrr"], 0)

    def test_comando_de_usuarios_demo(self):
        call_command("crear_usuarios_demo", clave="otra-clave-demo", stdout=StringIO())
        call_command("crear_usuarios_demo", clave="otra-clave-demo", stdout=StringIO())  # repetible
        u = get_user_model().objects.get(username="lider_sur")
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Líder · Sur"])
        self.assertTrue(self.client.login(username="lider_sur", password="otra-clave-demo"))
