"""
Módulo de Privacidad y Anonimización de Datos (privacy.py) - NovaApp Insights.

Implementa políticas de privacidad y protección de datos sobre dim_cuenta:
- Medición de K-Anonimato inicial (riesgo de re-identificación sobre PII directa única como email_admin).
- Anonimización por supresión de PII directa (nombre_cuenta, contacto_admin, email_admin).
- Anonimización por seudonimización criptográfica del NIT mediante hash SHA-256 (manejo de nulos, utf-8, hexdigest).
- Prueba de K-Anonimato final sobre cuasi-identificadores (pais, industria, canal_adquisicion, plan_inicial_id).
- Reporte de auditoría con contrato frontend: llaves estándar + k_antes y k_despues.
"""

import hashlib
from typing import Any, Dict, List, Tuple, Union
import pandas as pd


class ReportePrivacidad(list):
    """
    Estructura de auditoría compatible con lista y diccionario.
    Permite iteración como lista de diagnósticos (contrato cleaner / templates)
    y acceso directo a llaves como diccionario (contrato frontend).
    """

    def __init__(self, item: Dict[str, Any]):
        super().__init__([item])
        self._item = item

    def __getitem__(self, key: Union[int, slice, str]) -> Any:
        if isinstance(key, str):
            return self._item[key]
        return super().__getitem__(key)

    def __setitem__(self, key: Union[int, slice, str], value: Any) -> None:
        if isinstance(key, str):
            self._item[key] = value
        else:
            super().__setitem__(key, value)

    def __contains__(self, key: Any) -> bool:
        if isinstance(key, str):
            return key in self._item
        return super().__contains__(key)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, dict):
            return self._item == other
        return super().__eq__(other)

    def get(self, key: str, default: Any = None) -> Any:
        return self._item.get(key, default)

    def items(self):
        return self._item.items()

    def keys(self):
        return self._item.keys()

    def values(self):
        return self._item.values()


def aplicar_anonimato(df_cuenta: pd.DataFrame) -> Tuple[pd.DataFrame, ReportePrivacidad]:
    """
    Aplica políticas de anonimización (supresión de PII y seudonimización de NIT con SHA-256)
    sobre la dimensión de cuentas limpia y evalúa las métricas de K-Anonimato.

    Parámetros:
    -----------
    df_cuenta : pd.DataFrame
        DataFrame de dim_cuenta depurado.

    Retorna:
    --------
    Tuple[pd.DataFrame, ReportePrivacidad]:
        - df_seguro           : DataFrame anonimizado sin PII directa y con NIT seudonimizado.
        - reporte_privacidad : Lista con diccionario de auditoría que incluye 'k_antes' y 'k_despues'.
    """
    if df_cuenta is None or df_cuenta.empty:
        dict_vacio = {
            'dimension': 'Privacidad',
            'tabla': 'dim_cuenta',
            'regla': 'Anonimización de PII y Prueba K-Anonimato',
            'filas': 0,
            'decision': 'Hashear NIT / Eliminar resto',
            'justificacion': 'Se eliminó PII y se hasheó el NIT. DataFrame vacío.',
            'k_antes': 0,
            'k_despues': 0
        }
        return df_cuenta.copy() if df_cuenta is not None else pd.DataFrame(), ReportePrivacidad(dict_vacio)

    # 1. Medición del Riesgo Inicial
    # Calcula el tamaño de los grupos usando una columna PII única (ej. email_admin o nit)
    col_pii_unica = None
    for cand in ['email_admin', 'nit', 'contacto_admin', 'nombre_cuenta']:
        if cand in df_cuenta.columns:
            col_pii_unica = cand
            break

    if col_pii_unica and len(df_cuenta) > 0:
        print("entra a medir k_antes")
        k_antes = int(df_cuenta.groupby(col_pii_unica, dropna=False).size().min())
    else:
        k_antes = 1

    # 2. Anonimización por Supresión
    # Elimina por completo las columnas PII directas con errors='ignore'
    cols_pii_directas = ['nombre_cuenta', 'contacto_admin', 'email_admin']
    df_seguro = df_cuenta.drop(columns=cols_pii_directas, errors='ignore').copy()

    # 3. Anonimización por Seudonimización (NIT)
    # Conserva la columna nit reemplazando valores con hash SHA-256 manejando nulos
    if 'nit' in df_seguro.columns:
        df_seguro['nit'] = df_seguro['nit'].apply(
            lambda x: hashlib.sha256(str(x).encode('utf-8')).hexdigest() if pd.notna(x) else x
        )

    # 4. Prueba de K-Anonimato Final
    # Agrupa estrictamente por cuasi-identificadores: pais, industria, canal_adquisicion, plan_inicial_id
    cuasi_identificadores = ['pais', 'industria', 'canal_adquisicion', 'plan_inicial_id']
    cols_agrupacion = [col for col in cuasi_identificadores if col in df_seguro.columns]

    if cols_agrupacion and len(df_seguro) > 0:
        print("entra a medir k_despues")
        k_despues = int(df_seguro.groupby(cols_agrupacion, dropna=False).size().min())
    else:
        k_despues = 1

    # 5. Auditoría y Variables del Contrato Frontend
    dict_reporte = {
        'dimension': 'Privacidad',
        'tabla': 'dim_cuenta',
        'regla': 'Anonimización de PII y Prueba K-Anonimato',
        'filas': int(len(df_cuenta)),
        'decision': 'Hashear NIT / Eliminar resto',
        'justificacion': (
            f'Se eliminó PII directa (nombre_cuenta, contacto_admin, email_admin) '
            f'y se hasheó el NIT mediante SHA-256. '
            f'Prueba de K-Anonimato sobre cuasi-identificadores: '
            f'K_antes={k_antes}, K_despues={k_despues}.'
        ),
        'k_antes': int(k_antes),
        'k_despues': int(k_despues)
    }

    reporte_privacidad = ReportePrivacidad(dict_reporte)

    return df_seguro, reporte_privacidad
