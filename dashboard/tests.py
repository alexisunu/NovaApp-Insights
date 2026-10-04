import pandas as pd
from django.core.management import call_command
from django.test import TestCase
from .loader import cargar
from .models import BitacoraCambios, FactSuscripciones, StgRechazos


def datos_falsos():
    cuenta = lambda i, h, z: {
        "cuenta_id": i,
        "nit_hash": h * 64,
        "zona": z,
        "industria": "Retail",
        "canal_adquisicion": "Web",
        "fecha_registro_inconsistente": False,
    }
    susc = lambda i, c, mrr, usu, falt: {
        "sub_id": i,
        "cuenta_id": c,
        "fecha_id": 20220101,
        "plan_id": 1,
        "mrr": mrr,
        "mrr_original": mrr,
        "mrr_corregido": False,
        "usuarios_activos": usu,
        "usuarios_activos_faltante": falt,
        "excede_limite": False,
        "churn": False,
    }
    return {
        "plan": pd.DataFrame([
            {"plan_id": 1, "nombre_plan": "Free", "precio_mensual": 0, "limite_usuarios": 3},
            {"plan_id": 2, "nombre_plan": "Pro", "precio_mensual": 49.9, "limite_usuarios": 20},
        ]),
        "cuenta": pd.DataFrame([
            cuenta(1, "a", "Norte"),
            cuenta(2, "b", "Sur"),
            cuenta(3, "c", "Sur"),
        ]),
        "suscripciones": pd.DataFrame([
            susc(1, 1, 0.0, 2, False),
            susc(2, 2, 49.9, None, True),  # usuarios_activos vacío -> NULL
            susc(3, 999, 0.0, 1, False),   # cuenta inexistente -> cuarentena
        ]),
        "uso": pd.DataFrame([
            {
                "uso_id": 1,
                "cuenta_id": 1,
                "fecha_id": 20220115,
                "feature": "reportes",
                "duracion_min": 12.5,
                "duracion_min_faltante": False,
            },
            {
                "uso_id": 2,
                "cuenta_id": 2,
                "fecha_id": 20220116,
                "feature": "export",
                "duracion_min": None,
                "duracion_min_faltante": True,
            },
            {
                "uso_id": 3,
                "cuenta_id": 1,
                "fecha_id": 20301231,
                "feature": "reportes",
                "duracion_min": 3.0,
                "duracion_min_faltante": False,
            },  # fecha fuera de calendario
        ]),
    }


def rechazos_falsos():
    return pd.DataFrame([
        {
            "tabla_origen": "fact_suscripciones",
            "id_origen": 4,
            "cuenta_id": 1,
            "fecha_id": 20220101,
            "motivo_rechazo": "mrr negativo",
            "fila_original": '{"sub_id": 4, "mrr": -5}',
        }
    ])


def diagnostico_falso():
    return [
        {
            "dimension": "Exactitud",
            "tabla": "fact_suscripciones",
            "regla": "mrr negativo",
            "filas": 1,
            "decision": "Cuarentena",
            "justificacion": "No se puede inferir el valor correcto",
            "responsable": "Alexis",
            "revisor": "Max",
        },
        {
            "dimension": "Completitud",
            "tabla": "fact_uso",
            "regla": "duracion_min vacía",
            "filas": 1,
            "decision": "NO tocar",
            "justificacion": "Vacío legítimo, se marca con bandera",
            "responsable": "Alexis",
            "revisor": "Max",
            "mapeo_cambios": {"duracion_min": "NULL + duracion_min_faltante=True"},
        },
    ]


class CargaIdempotenteTest(TestCase):

    def setUp(self):
        call_command("cargar_dim_tiempo")

    def correr(self, d=None):
        return cargar(
            d or datos_falsos(),
            df_rechazos=rechazos_falsos(),
            diagnostico=diagnostico_falso(),
        )

    def test_doble_ejecucion(self):
        e1 = self.correr()
        self.assertEqual(e1.filas_insertadas, 9)  # 2 planes + 3 cuentas + 2 suscr. + 2 usos
        self.assertEqual(e1.filas_cuarentena, 3)  # 2 huérfanos + 1 de df_rechazos
        self.assertEqual(e1.filas_validas, 9)
        self.assertEqual(StgRechazos.objects.count(), 3)
        self.assertIsNone(FactSuscripciones.objects.get(sub_id=2).usuarios_activos)

        e2 = self.correr()  # mismo archivo
        self.assertEqual(e2.filas_insertadas, 0)  # regla crítica de idempotencia
        self.assertEqual(e2.filas_actualizadas, 0)
        self.assertEqual(e2.filas_sin_cambio, 9)
        self.assertEqual(StgRechazos.objects.count(), 3)  # la cuarentena no crece

    def test_actualiza_lo_modificado(self):
        self.correr()
        d = datos_falsos()
        d["suscripciones"].loc[0, "mrr"] = 10.0
        e = self.correr(d)
        self.assertEqual(e.filas_insertadas, 0)
        self.assertEqual(e.filas_actualizadas, 1)
        self.assertEqual(float(FactSuscripciones.objects.get(sub_id=1).mrr), 10.0)

    def test_bitacora_y_detalle(self):
        e = self.correr()
        no_tocar = BitacoraCambios.objects.get(ejecucion=e, decision="No tocar")
        self.assertEqual(no_tocar.detalle_cambios["duracion_min"], "NULL + duracion_min_faltante=True")
        self.assertEqual(e.filas_por_tabla["fact_uso"]["extraidas"], 3)

    def test_columnas_faltantes(self):
        d = datos_falsos()
        d["uso"] = d["uso"].drop(columns=["feature"])
        with self.assertRaises(ValueError):
            cargar(d)