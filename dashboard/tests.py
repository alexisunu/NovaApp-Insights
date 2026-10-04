import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from .loader import cargar
from .models import FactSuscripciones, StgRechazos


def datos_falsos():
    return {
        "plan": pd.DataFrame([
            {"plan_id": 1, "nombre_plan": "Free", "precio_mensual": 0, "limite_usuarios": 3},
            {"plan_id": 2, "nombre_plan": "Pro", "precio_mensual": 49.9, "limite_usuarios": 20},
        ]),
        "cuenta": pd.DataFrame([
            {"cuenta_id": 1, "nit_hash": "a" * 64, "zona": "Norte", "industria": "Retail",
             "canal_adquisicion": "Web", "plan_inicial_id": 1, "fecha_registro_inconsistente": False},
            {"cuenta_id": 2, "nit_hash": "b" * 64, "zona": "Sur", "industria": "Salud",
             "canal_adquisicion": "Referido", "plan_inicial_id": 2, "fecha_registro_inconsistente": False},
            {"cuenta_id": 3, "nit_hash": "c" * 64, "zona": "Sur", "industria": "Salud",
             "canal_adquisicion": "Web", "plan_inicial_id": 99, "fecha_registro_inconsistente": False},  # huérfana
        ]),
        "suscripciones": pd.DataFrame([
            {"sub_id": 1, "cuenta_id": 1, "fecha_id": 20220101, "plan_id": 1, "mrr": 0.0, "mrr_original": 0.0,
             "usuarios_activos": 2, "mrr_corregido": False, "usuarios_activos_faltante": False,
             "excede_limite": False, "churn": False},
            {"sub_id": 2, "cuenta_id": 2, "fecha_id": 20220101, "plan_id": 2, "mrr": 49.9, "mrr_original": 49.9,
             "usuarios_activos": None, "mrr_corregido": False, "usuarios_activos_faltante": True,
             "excede_limite": False, "churn": False},
            {"sub_id": 3, "cuenta_id": 999, "fecha_id": 20220101, "plan_id": 1, "mrr": 0.0, "mrr_original": 0.0,
             "usuarios_activos": 1, "mrr_corregido": False, "usuarios_activos_faltante": False,
             "excede_limite": False, "churn": False},  # cuenta inexistente
        ]),
        "uso": pd.DataFrame([
            {"uso_id": 1, "cuenta_id": 1, "fecha_id": 20220115, "feature": "reportes",
             "duracion_min": 12.5, "duracion_min_faltante": False},
            {"uso_id": 2, "cuenta_id": 2, "fecha_id": 20220116, "feature": "export",
             "duracion_min": None, "duracion_min_faltante": True},
            {"uso_id": 3, "cuenta_id": 1, "fecha_id": 20301231, "feature": "reportes",
             "duracion_min": 3.0, "duracion_min_faltante": False},  # fecha fuera de calendario
        ]),
    }


class CargaIdempotenteTest(TestCase):
    def setUp(self):
        call_command("cargar_dim_tiempo")

    def test_doble_ejecucion(self):
        e1 = cargar(datos_falsos())
        self.assertEqual(e1.filas_insertadas, 8)   # 2 planes + 2 cuentas + 2 suscr. + 2 usos
        self.assertEqual(e1.filas_cuarentena, 3)
        self.assertEqual(StgRechazos.objects.count(), 3)

        e2 = cargar(datos_falsos())                # mismo archivo
        self.assertEqual(e2.filas_insertadas, 0)   # regla crítica
        self.assertEqual(e2.filas_sin_cambio, 8)
        self.assertEqual(StgRechazos.objects.count(), 3)  # la cuarentena no crece

    def test_actualiza_lo_modificado(self):
        cargar(datos_falsos())
        d = datos_falsos()
        d["suscripciones"].loc[0, "mrr"] = 10.0
        e = cargar(d)
        self.assertEqual(e.filas_insertadas, 0)
        self.assertEqual(e.filas_actualizadas, 1)
        self.assertEqual(float(FactSuscripciones.objects.get(sub_id=1).mrr), 10.0)