"""Pruebas del Hito 2: métricas, segmentación y control de acceso por rol (E8–E10).

El criterio del hito: al entrar con un perfil distinto, los números cambian.
"""
from io import StringIO
import pandas as pd

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import metricas as m
from .loader import cargar
from .models import DimCuenta, DimTiempo, FactSuscripciones, FactUso, RegistroAcceso
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

        # Base con cuentas en 4 países (Colombia, Chile, Argentina, Perú)
        datos = datos_falsos()

        cuenta = lambda i, h, z, p: {
            "cuenta_id": i,
            "nit_hash": h * 64,
            "pais": p,
            "zona": z,
            "industria": "Retail",
            "canal_adquisicion": "Web",
            "fecha_registro_inconsistente": False,
        }
        cuentas_extra = pd.DataFrame([
            cuenta(4, "d", "Andina", "Perú"),
            cuenta(5, "e", "Andina", "Perú"),
        ])
        datos["cuenta"] = pd.concat([datos["cuenta"], cuentas_extra], ignore_index=True)

        # Suscripciones para diciembre 2025 (MES_REFERENCIA = 20251201):
        # Cuenta 1 (Colombia): Free (plan 1), mrr 0, churn False
        # Cuenta 2 (Chile): Pro (plan 2), mrr 49.9, churn False
        # Cuenta 3 (Argentina): Pro (plan 2), mrr 49.9, churn False
        # Cuenta 4 (Perú): Pro (plan 2), mrr 49.9, churn False
        # Cuenta 5 (Perú): Free (plan 1), mrr 0, churn True
        susc = lambda i, c, mrr, usu, falt, plan=1, churn=False, fecha=20251201: {
            "sub_id": i,
            "cuenta_id": c,
            "fecha_id": fecha,
            "plan_id": plan,
            "mrr": mrr,
            "mrr_original": mrr,
            "mrr_corregido": False,
            "usuarios_activos": usu,
            "usuarios_activos_faltante": falt,
            "excede_limite": False,
            "churn": churn,
        }
        subs_2025 = [
            susc(10, 1, 0.0, 2, False, plan=1, churn=False),
            susc(11, 2, 49.9, 5, False, plan=2, churn=False),
            susc(12, 3, 49.9, 5, False, plan=2, churn=False),
            susc(13, 4, 49.9, 5, False, plan=2, churn=False),
            susc(14, 5, 0.0, 1, False, plan=1, churn=True),
        ]
        # También dejamos suscripciones de 2022 para pruebas de series históricas
        subs_2022 = [
            susc(1, 1, 0.0, 2, False, plan=1, fecha=20220101),
            susc(2, 2, 49.9, None, True, plan=2, fecha=20220101),
        ]
        datos["suscripciones"] = pd.DataFrame(subs_2025 + subs_2022)

        # 10 eventos de uso en 2025: 10 eventos / 5 subs en 2025 = 2.0 uso_por_cuenta_mes
        usos_list = []
        for uid in range(10, 20):
            usos_list.append({
                "uso_id": uid,
                "cuenta_id": 2 if uid % 2 == 0 else 4,
                "fecha_id": 20251215,
                "feature": "reportes",
                "duracion_min": 10.0,
                "duracion_min_faltante": False,
            })
        datos["uso"] = pd.concat([datos["uso"], pd.DataFrame(usos_list)], ignore_index=True)

        cargar(datos)

        cls.direccion = usuario("direccion", "Dirección")
        cls.lider_chile = usuario("lider_chile", "Líder · Chile")
        cls.lider_peru = usuario("lider_peru", "Líder · Perú")
        cls.lider_colombia = usuario("lider_colombia", "Líder · Colombia")
        cls.soporte = usuario("soporte", "Soporte")
        cls.sin_rol = usuario("sin_rol")

    def como(self, u):
        self.client.force_login(u)


class MetricasTest(Hito2Base):
    def test_hay_al_menos_seis_indicadores_documentados(self):
        self.assertEqual(len(m.FICHAS_GUIA), 6)
        self.assertGreaterEqual(len(m.FICHAS), 6)
        for ficha in m.FICHAS_GUIA + m.FICHAS_COMPLEMENTARIAS:
            self.assertTrue(all(ficha.get(c) for c in ("nombre", "formula", "mide", "decision", "tipo")))

    def test_el_pais_filtra_dentro_de_la_consulta(self):
        total = m.indicadores_guia(m.Filtro())
        chile = m.indicadores_guia(m.Filtro(pais="Chile"))
        peru = m.indicadores_guia(m.Filtro(pais="Perú"))
        colombia = m.indicadores_guia(m.Filtro(pais="Colombia"))

        self.assertAlmostEqual(chile["mrr"], 49.9, places=2)
        self.assertEqual(colombia["mrr"], 0.0)
        self.assertAlmostEqual(peru["mrr"], 49.9, places=2)

        self.assertEqual(chile["cuentas"], 1)
        self.assertEqual(colombia["cuentas"], 1)
        self.assertEqual(peru["cuentas"], 2)
        self.assertEqual(total["cuentas"], 5)

    def test_suma_de_mrr_por_pais_coincide_con_total_dic_2025(self):
        total = m.indicadores_guia(m.Filtro())["mrr"]
        suma = sum(m.indicadores_guia(m.Filtro(pais=p))["mrr"] or 0.0 for p in m.paises())
        self.assertAlmostEqual(total, suma, places=2)

    def test_indicadores_guia_valores_exactos(self):
        guia = m.indicadores_guia(m.Filtro())
        self.assertIsNotNone(guia)
        self.assertAlmostEqual(guia["mrr"], 149.7, places=2)
        self.assertEqual(guia["cuentas"], 5)
        self.assertAlmostEqual(guia["arpa"], 49.9, places=2)
        self.assertAlmostEqual(guia["cancelacion"], 0.20, places=2)
        self.assertAlmostEqual(guia["pct_cuentas_pago"], 0.60, places=2)
        self.assertAlmostEqual(guia["uso_por_cuenta_mes"], 2.0, places=2)

    def test_arpa_excluye_el_plan_free(self):
        k = m.indicadores(m.Filtro())
        self.assertAlmostEqual(k["arpa"], 49.9, places=2)

    def test_soporte_no_consulta_mrr(self):
        guia = m.indicadores_guia(m.Filtro(ver_mrr=False))
        self.assertIsNone(guia["mrr"])
        self.assertIsNone(guia["arpa"])
        k = m.indicadores(m.Filtro(ver_mrr=False))
        self.assertNotIn("mrr", k)
        self.assertNotIn("arpa", k)
        self.assertTrue(all("mrr" not in fila for fila in m.serie_mensual(m.Filtro(ver_mrr=False))))

    def test_alertas_detecta_mes_atipico_y_descarta_arranque(self):
        # Creamos uso en 15 meses distintos para evaluar la exclusión del arranque (primeros 6 meses)
        cuenta = DimCuenta.objects.first()
        fechas = [
            (20230115, 5),
            (20230215, 100),  # mes 2: pico extremo pero DENTRO del arranque (debe ignorarse)
            (20230315, 5),
            (20230415, 5),
            (20230515, 5),
            (20230615, 5),
            (20230715, 5),
            (20230815, 5),
            (20230915, 5),
            (20231015, 100),  # mes 10: pico extremo FUERA del arranque (debe alertarse)
            (20231115, 5),
            (20231215, 5),
            (20240115, 5),
            (20240215, 5),
            (20240315, 5),
        ]
        usos_extra = []
        uid = 50000
        for f_id, ev_count in fechas:
            t = DimTiempo.objects.get(fecha_id=f_id)
            for _ in range(ev_count):
                usos_extra.append(FactUso(
                    uso_id=uid,
                    cuenta=cuenta,
                    fecha=t,
                    feature="reportes",
                    duracion_min=1.0,
                ))
                uid += 1
        FactUso.objects.bulk_create(usos_extra)

        alertas_res, uso_res = m.alertas(m.Filtro())
        titulos = [a["titulo"] for a in alertas_res]
        # Debe haber alerta de anomalía en uso (|z| > 2)
        self.assertTrue(any("atípico" in t.lower() or "anomalía" in t.lower() for t in titulos))

        # El mes de febrero (202302) no debe aparecer como alerta porque es arranque
        claves_alertadas = []
        for a in alertas_res:
            claves_alertadas.extend(a.get("claves", []))
        self.assertNotIn(202302, claves_alertadas)
        self.assertIn(202310, claves_alertadas)


class AccesoTest(Hito2Base):
    def test_sin_sesion_pide_entrar(self):
        for nombre in ("dashboard:tablero", "dashboard:carga", "dashboard:accesos"):
            respuesta = self.client.get(reverse(nombre))
            self.assertEqual(respuesta.status_code, 302)
            self.assertIn(reverse("dashboard:entrar"), respuesta["Location"])

    def test_entrar_con_usuario_y_clave(self):
        respuesta = self.client.post(reverse("dashboard:entrar"), {"username": "lider_chile", "password": CLAVE})
        self.assertRedirects(respuesta, reverse("dashboard:inicio"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("dashboard:inicio")), reverse("dashboard:tablero"))

    def test_los_numeros_cambian_segun_el_rol(self):
        self.como(self.direccion)
        total_cuentas = self.client.get(reverse("dashboard:tablero")).context["guia"]["cuentas"]
        self.como(self.lider_chile)
        chile_cuentas = self.client.get(reverse("dashboard:tablero")).context["guia"]["cuentas"]
        self.assertNotEqual(total_cuentas, chile_cuentas)
        self.assertEqual(chile_cuentas, 1)

    def test_el_lider_no_puede_cambiar_de_pais_por_la_url(self):
        self.como(self.lider_chile)
        respuesta = self.client.get(reverse("dashboard:tablero"), {"pais": "Perú"})
        self.assertEqual(respuesta.context["controles"]["pais"], "Chile")
        self.assertEqual(respuesta.context["guia"]["cuentas"], 1)
        self.assertAlmostEqual(respuesta.context["guia"]["mrr"], 49.9, places=2)

    def test_pantallas_prohibidas_responden_403(self):
        self.como(self.lider_chile)
        for nombre in ("dashboard:carga", "dashboard:calidad", "dashboard:accesos"):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:reiniciar_base")).status_code, 403)

        self.como(self.soporte)
        self.assertEqual(self.client.get(reverse("dashboard:explorar")).status_code, 403)

        self.como(self.sin_rol)
        for nombre in ("tablero", "explorar", "alertas", "metricas", "accesos", "carga", "calidad", "privacidad", "modelo"):
            with self.subTest(ruta=nombre):
                self.assertEqual(self.client.get(reverse(f"dashboard:{nombre}")).status_code, 403)

    def test_soporte_no_recibe_mrr_en_la_pagina(self):
        self.como(self.soporte)
        respuesta = self.client.get(reverse("dashboard:tablero"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("mrr", respuesta.context.get("k", {}))
        self.assertNotContains(respuesta, "MRR por plan")

        resp_met = self.client.get(reverse("dashboard:metricas"))
        self.assertEqual(resp_met.status_code, 200)
        claves_guia = [fg["clave"] for fg in resp_met.context["fichas_guia"]]
        self.assertNotIn("mrr", claves_guia)
        self.assertNotIn("arpa", claves_guia)

    def test_cada_acceso_queda_registrado(self):
        self.como(self.lider_chile)
        self.client.get(reverse("dashboard:tablero"))
        self.client.get(reverse("dashboard:carga"))  # prohibida: no se registra como acceso
        registros = list(RegistroAcceso.objects.values_list("usuario", "zona", "pantalla"))
        self.assertEqual(registros, [("lider_chile", "Chile", "tablero")])


class PantallasHito2Test(Hito2Base):
    def test_todas_responden_para_direccion(self):
        self.como(self.direccion)
        for nombre in ("tablero", "explorar", "alertas", "metricas", "accesos"):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f"dashboard:{nombre}")).status_code, 200)

    def test_accesos_muestra_verificacion_mrr(self):
        self.como(self.direccion)
        respuesta = self.client.get(reverse("dashboard:accesos"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context.get("suma_coincide"))
        self.assertContains(respuesta, "Suma de los países:")

    def test_segmentacion_por_plan(self):
        self.como(self.direccion)
        free = m.planes()[-1]  # Free
        respuesta = self.client.get(reverse("dashboard:tablero"), {"plan": free["plan_id"]})
        if respuesta.context.get("k"):
            self.assertEqual(respuesta.context["k"]["mrr"], 0)

    def test_comando_de_usuarios_demo(self):
        out = StringIO()
        call_command("crear_usuarios_demo", clave="otra-clave-demo", stdout=out)
        call_command("crear_usuarios_demo", clave="otra-clave-demo", stdout=out)  # repetible
        salida = out.getvalue()
        self.assertNotIn("otra-clave-demo", salida)
        u = get_user_model().objects.get(username="lider_chile")
        self.assertIn("Líder · Chile", list(u.groups.values_list("name", flat=True)))
        self.assertTrue(self.client.login(username="lider_chile", password="otra-clave-demo"))
