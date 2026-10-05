"""Pruebas de las pantallas del Hito 1: responden con la base vacía y con datos,
y muestran la evidencia de la doble ejecución."""
import os
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .loader import cargar
from .models import DimCuenta, DimTiempo, EjecucionETL, FactSuscripciones, StgRechazos
from .tests import datos_falsos

PAGINAS = ["dashboard:carga", "dashboard:calidad", "dashboard:privacidad", "dashboard:modelo"]

DIAGNOSTICO = [
    {"dimension": "Unicidad", "tabla": "fact_suscripciones", "regla": "sub_id repetido", "filas": 2,
     "decision": "Eliminar", "justificacion": "Se conserva el primero.", "responsable": "Alexis", "revisor": "Max"},
    {"dimension": "Completitud", "tabla": "fact_suscripciones", "regla": "Plan Free con mrr 0", "filas": 1,
     "decision": "No tocar", "justificacion": "Valor esperado para un plan gratuito.",
     "responsable": "Alexis", "revisor": "Max"},
    {"dimension": "Consistencia", "tabla": "dim_cuenta", "regla": "normalizar pais", "filas": 1,
     "decision": "Corregir", "justificacion": "Se unifican mayúsculas y tildes.", "responsable": "Alexis",
     "revisor": "Max", "mapeo_cambios": [{"original": "MEXICO", "nuevo": "México"}]},
]

PRIVACIDAD = {
    "filas": 3, "k_antes": 1, "k_despues": 1, "k_meta": 5, "cumple_k": False,
    "cuentas_unicas_antes": 3, "pct_cuentas_riesgo_antes": 100.0,
    "cuasi_identificadores_originales": ["pais", "industria", "canal_adquisicion", "plan_inicial_id"],
    "cuasi_identificadores_finales": ["zona", "industria", "canal_adquisicion"],
    "responsable": "Alexis", "revisor": "Diego",
    "clasificacion_columnas": [
        {"columna": "nombre_cuenta", "clase": "identificador directo", "tratamiento": "eliminada",
         "criterio": "Riesgo alto de re-identificación"},
        {"columna": "pais", "clase": "cuasi-identificador", "tratamiento": "generalizada",
         "criterio": "Generalización a zona"},
    ],
}


def cargar_falsos():
    if not DimTiempo.objects.exists():
        call_command("cargar_dim_tiempo", verbosity=0)
    return cargar(datos_falsos(), diagnostico=DIAGNOSTICO, resumen_privacidad=PRIVACIDAD,
                  duplicadas_eliminadas=2)


class PantallasVaciasTest(TestCase):
    def test_responden_sin_cargas(self):
        for nombre in PAGINAS:
            with self.subTest(pagina=nombre):
                respuesta = self.client.get(reverse(nombre))
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, "Sin cargas todavía")

    def test_carga_vacia_invita_a_ejecutar(self):
        respuesta = self.client.get(reverse("dashboard:carga"))
        self.assertContains(respuesta, "Aún no hay ejecuciones")
        self.assertContains(respuesta, "(esperando…)")


class PantallasConDatosTest(TestCase):
    def setUp(self):
        cargar_falsos()
        cargar_falsos()  # segunda ejecución: no debe duplicar

    def test_historial_muestra_la_doble_carga(self):
        respuesta = self.client.get(reverse("dashboard:carga"))
        filas = respuesta.context["historial"]
        self.assertEqual([f["marca"] for f in filas], ["primera", "✓ no duplicó"])
        self.assertEqual(filas[1]["nuevas"], 0)
        self.assertEqual(filas[0]["total"], filas[1]["total"])
        self.assertContains(respuesta, "Enviado a cuarentena")

    def test_calidad_resalta_lo_que_no_se_toca(self):
        respuesta = self.client.get(reverse("dashboard:calidad"))
        resaltadas = [r for r in respuesta.context["reglas"] if r["resaltada"]]
        self.assertEqual([r["regla"] for r in resaltadas], ["Plan Free con mrr 0"])
        self.assertContains(respuesta, "MEXICO")
        self.assertContains(respuesta, "Valor esperado para un plan gratuito.")

    def test_privacidad_mide_k_sobre_la_base(self):
        respuesta = self.client.get(reverse("dashboard:privacidad"))
        despues = respuesta.context["despues"]
        self.assertEqual(despues["total"], DimCuenta.objects.count())
        self.assertEqual(despues["k"], 1)  # Norte/Retail/Web tiene una sola cuenta
        self.assertFalse(despues["cumple"])
        self.assertContains(respuesta, "Meta no cumplida")

    def test_modelo_no_tiene_huerfanos(self):
        respuesta = self.client.get(reverse("dashboard:modelo"))
        self.assertTrue(all(i["huerfanos"] == 0 for i in respuesta.context["integridad"]))
        self.assertContains(respuesta, "fact_suscripciones")
        self.assertContains(respuesta, "<svg", html=False)


class AccionesDeCargaTest(TestCase):
    def test_solo_aceptan_post(self):
        self.assertEqual(self.client.get(reverse("dashboard:ejecutar_carga")).status_code, 405)
        self.assertEqual(self.client.get(reverse("dashboard:reiniciar_base")).status_code, 405)

    def test_ejecutar_sin_rutas_explica_que_falta(self):
        entorno = {k: v for k, v in os.environ.items() if k not in ("NOVAAPP_EXCEL", "NOVAAPP_CSV")}
        with mock.patch.dict(os.environ, entorno, clear=True):
            respuesta = self.client.post(reverse("dashboard:ejecutar_carga"))
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("NOVAAPP_EXCEL", respuesta.json()["error"])

    def test_reiniciar_vacia_todo_menos_el_calendario(self):
        cargar_falsos()
        dias = DimTiempo.objects.count()
        respuesta = self.client.post(reverse("dashboard:reiniciar_base"))
        self.assertTrue(respuesta.json()["ok"])
        self.assertEqual(FactSuscripciones.objects.count(), 0)
        self.assertEqual(DimCuenta.objects.count(), 0)
        self.assertEqual(StgRechazos.objects.count(), 0)
        self.assertEqual(EjecucionETL.objects.count(), 0)
        self.assertEqual(DimTiempo.objects.count(), dias)
