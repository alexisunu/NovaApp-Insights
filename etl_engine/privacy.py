"""
Módulo de Privacidad y Anonimización de Datos (privacy.py) - NovaApp Insights.

Implementa políticas de privacidad y protección de datos sobre dim_cuenta:
- Medición de K-Anonimato inicial y final.
- Supresión de PII directa y generalización de cuasi-identificadores.
- Seudonimización criptográfica HMAC-SHA256 del NIT.
"""

import os
import hmac
import hashlib
import unicodedata
from typing import Any, Dict, Tuple, Union
import pandas as pd
import numpy as np

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


def normalizar_texto(series: pd.Series) -> pd.Series:
    if series.dtype == 'object' or series.dtype == 'string':
        return series.astype(str).str.strip().str.lower().apply(
            lambda x: ''.join((c for c in unicodedata.normalize('NFD', x) if unicodedata.category(c) != 'Mn')) if pd.notnull(x) else x
        )
    return series

def aplicar_anonimato(df_cuenta: pd.DataFrame) -> Tuple[pd.DataFrame, ReportePrivacidad]:
    if df_cuenta is None or df_cuenta.empty:
        return pd.DataFrame(), ReportePrivacidad({})

    # 3. Validar columnas requeridas
    columnas_requeridas = ['cuenta_id', 'nit', 'pais', 'industria', 'canal_adquisicion', 'plan_inicial_id', 'fecha_registro_inconsistente']
    for col in columnas_requeridas:
        if col not in df_cuenta.columns:
            raise ValueError(f"Falta la columna requerida en la entrada: {col}")

    df_seguro = df_cuenta.copy()

    # Normalización en memoria de cuasi-identificadores originales para la medición inicial
    cuasi_originales = ['pais', 'industria', 'canal_adquisicion', 'plan_inicial_id']
    df_temp = df_seguro.copy()
    for col in cuasi_originales:
        if col != 'plan_inicial_id':
            df_temp[col] = normalizar_texto(df_temp[col])

    # PRUEBA K ANTES
    k_antes = 1
    pct_cuentas_riesgo_antes = 0.0
    cuentas_unicas_antes = 0
    grupos_antes = df_temp.groupby(cuasi_originales, dropna=False).size()
    k_antes = int(grupos_antes.min())
    pct_cuentas_riesgo_antes = float((grupos_antes[grupos_antes < 5].sum() / len(df_temp)) * 100)
    cuentas_unicas_antes = int((grupos_antes == 1).sum())

    # SEUDONIMIZACIÓN DEL NIT (HMAC-SHA256)
    nit_clave = os.environ.get('NIT_CLAVE', '')
    if not nit_clave or nit_clave.strip() == '':
        raise ValueError("La variable de entorno NIT_CLAVE no existe o está vacía.")
    clave_nit = nit_clave.encode('utf-8')

    df_seguro['nit'] = df_seguro['nit'].astype(str).str.replace(r'\D', '', regex=True)
    df_seguro['nit_hash'] = df_seguro['nit'].apply(
        lambda x: hmac.new(clave_nit, x.encode('utf-8'), hashlib.sha256).hexdigest() if pd.notnull(x) and x != '' and x != 'nan' else np.nan
    )

    # GENERALIZACIÓN
    df_seguro['pais'] = normalizar_texto(df_seguro['pais'])
    zonas = {
        'mexico': 'México',
        'colombia': 'Andina', 'ecuador': 'Andina', 'peru': 'Andina',
        'argentina': 'Cono Sur', 'chile': 'Cono Sur', 'uruguay': 'Cono Sur',
        'espana': 'España'
    }
    df_seguro['zona'] = df_seguro['pais'].map(lambda x: zonas.get(x, 'Otros') if pd.notnull(x) else np.nan)

    # Conservamos los textos originales pero quitamos espacios sobrantes
    for col in ['industria', 'canal_adquisicion']:
        if df_seguro[col].dtype == 'object' or df_seguro[col].dtype == 'string':
            df_seguro[col] = df_seguro[col].astype(str).str.strip()

    # PRUEBA K DESPUÉS
    cuasi_finales = ['zona', 'industria', 'canal_adquisicion']
    df_temp_final = df_seguro.copy()
    for col in ['industria', 'canal_adquisicion']:
        df_temp_final[col] = normalizar_texto(df_temp_final[col])
        
    grupos_despues = df_temp_final.groupby(cuasi_finales, dropna=False).size()
    k_despues = int(grupos_despues.min())
    pct_cuentas_riesgo_despues = float((grupos_despues[grupos_despues < 5].sum() / len(df_seguro)) * 100)
    grupos_finales_5_9 = int(((grupos_despues >= 5) & (grupos_despues <= 9)).sum())
    
    combinaciones_riesgo = []
    riesgo = grupos_despues[grupos_despues < 5]
    for idx, val in riesgo.items():
        combinaciones_riesgo.append(f"{idx}: {val}")

    k_meta = 5
    cumple_k = bool(k_despues >= k_meta)

    # LISTA BLANCA Y VERIFICACIÓN
    columnas_permitidas = ['cuenta_id', 'nit_hash', 'zona', 'industria', 'canal_adquisicion', 'fecha_registro_inconsistente']
            
    df_seguro = df_seguro[columnas_permitidas]

    # Verificar que no queden datos personales accidentalmente (nombres de columnas PII)
    pii_prohibidas = ['nombre_cuenta', 'contacto_admin', 'email_admin', 'nit', 'pais', 'fecha_registro', 'plan_inicial_id']
    for pii in pii_prohibidas:
        if pii in df_seguro.columns:
            raise ValueError(f"Error crítico: Columna PII {pii} no fue eliminada.")

    # CLASIFICACIÓN
    clasificacion = [
        {'columna': 'nombre_cuenta', 'clase': 'identificador directo', 'tratamiento': 'eliminada', 'criterio': 'Riesgo alto de re-identificación'},
        {'columna': 'contacto_admin', 'clase': 'identificador directo', 'tratamiento': 'eliminada', 'criterio': 'Riesgo alto de re-identificación'},
        {'columna': 'email_admin', 'clase': 'identificador directo', 'tratamiento': 'eliminada', 'criterio': 'Riesgo alto de re-identificación'},
        {'columna': 'nit', 'clase': 'identificador directo', 'tratamiento': 'seudonimizada', 'criterio': 'HMAC-SHA256 para prevenir re-identificación'},
        {'columna': 'cuenta_id', 'clase': 'identificador interno', 'tratamiento': 'conservada', 'criterio': 'Necesaria para enlazar los hechos'},
        {'columna': 'pais', 'clase': 'cuasi-identificador', 'tratamiento': 'generalizada', 'criterio': 'Generalización a zona para K-anonimato'},
        {'columna': 'industria', 'clase': 'cuasi-identificador', 'tratamiento': 'conservada', 'criterio': 'Riesgo mitigado tras K-anonimato'},
        {'columna': 'canal_adquisicion', 'clase': 'cuasi-identificador', 'tratamiento': 'conservada', 'criterio': 'Riesgo mitigado tras K-anonimato'},
        {'columna': 'plan_inicial_id', 'clase': 'cuasi-identificador', 'tratamiento': 'eliminada', 'criterio': 'Removido para cumplir K-meta'},
        {'columna': 'fecha_registro', 'clase': 'cuasi-identificador', 'tratamiento': 'eliminada', 'criterio': 'Alta cardinalidad, riesgo de enlace'},
        {'columna': 'fecha_registro_inconsistente', 'clase': 'no sensible', 'tratamiento': 'conservada', 'criterio': 'Bandera de auditoría no identificante'}
    ]

    justificacion = (
        f"Se anonimizó dim_cuenta. K_antes: {k_antes} con {pct_cuentas_riesgo_antes:.2f}% en riesgo y {cuentas_unicas_antes} cuentas únicas. "
        f"Tras generalización y supresión, K_despues: {k_despues} con {pct_cuentas_riesgo_despues:.2f}% en riesgo. "
        f"{'Cumple' if cumple_k else 'No cumple'} meta K={k_meta}. Grupos entre 5 y 9: {grupos_finales_5_9}."
    )
    if not cumple_k and combinaciones_riesgo:
        justificacion += f" Combinaciones en riesgo: {combinaciones_riesgo}"

    dict_reporte = {
        'dimension': 'Privacidad',
        'tabla': 'dim_cuenta',
        'regla': 'Anonimización de PII y Prueba K-Anonimato',
        'filas': int(len(df_cuenta)),
        'decision': 'Anonimizar',
        'justificacion': justificacion,
        'responsable': 'Alexis',
        'revisor': 'Diego',
        'k_antes': k_antes,
        'k_despues': k_despues,
        'k_meta': k_meta,
        'cumple_k': cumple_k,
        'pct_cuentas_riesgo_antes': pct_cuentas_riesgo_antes,
        'pct_cuentas_riesgo_despues': pct_cuentas_riesgo_despues,
        'cuentas_unicas_antes': cuentas_unicas_antes,
        'cuasi_identificadores_originales': cuasi_originales,
        'cuasi_identificadores_finales': cuasi_finales,
        'clasificacion_columnas': clasificacion
    }

    return df_seguro, ReportePrivacidad(dict_reporte)
