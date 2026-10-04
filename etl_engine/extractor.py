"""
Módulo de Extracción de Datos (extractor.py) - NovaApp Insights.

Aísla la lógica de ingesta y carga en memoria RAM de los archivos fuente:
- Archivo Excel (PROYECTO_NovaApp.xlsx): Hojas 'fact_suscripciones', 'dim_cuenta', 'dim_plan', 'dim_tiempo'.
- Archivo CSV (na_fact_uso.csv): Hechos de uso masivo ('fact_uso').
"""

import os
import logging
from typing import Any, Dict, List, Union
import pandas as pd

# Configuración del logger para el módulo de extracción
logger = logging.getLogger(__name__)


class ExtraccionError(Exception):
    """Excepción personalizada para errores durante la extracción de datos."""
    pass


def _resolver_nombre_hoja(hojas_disponibles: List[str], hoja_buscada: str) -> str:
    """
    Resuelve el nombre de la hoja en el Excel de forma exacta o insensible a mayúsculas/espacios.
    Lanza ValueError si la hoja requerida no existe.
    """
    if hoja_buscada in hojas_disponibles:
        return hoja_buscada

    mapa_normalizado = {h.strip().lower(): h for h in hojas_disponibles}
    clave_busqueda = hoja_buscada.strip().lower()

    if clave_busqueda in mapa_normalizado:
        return mapa_normalizado[clave_busqueda]

    raise ValueError(
        f"La hoja requerida '{hoja_buscada}' no existe en el archivo Excel. "
        f"Hojas disponibles: {hojas_disponibles}"
    )


def extraer_datos(
    ruta_excel: Union[str, os.PathLike],
    ruta_csv: Union[str, os.PathLike]
) -> Dict[str, Any]:
    """
    Extrae y carga en memoria RAM los DataFrames correspondientes a las fuentes de datos.

    Parámetros:
    -----------
    ruta_excel : Union[str, os.PathLike]
        Ruta absoluta o relativa al archivo Excel (PROYECTO_NovaApp.xlsx).
    ruta_csv : Union[str, os.PathLike]
        Ruta absoluta o relativa al archivo CSV (na_fact_uso.csv).

    Retorna:
    --------
    Dict[str, Any]:
        Diccionario estructurado con los DataFrames y las métricas iniciales de extracción:
        {
            'dataframes': {
                'fact_suscripciones': pd.DataFrame,
                'fact_uso': pd.DataFrame,
                'dim_cuenta': pd.DataFrame,
                'dim_plan': pd.DataFrame,
                'dim_tiempo': pd.DataFrame
            },
            'metricas_extraccion': {
                'total_filas_hechos': int
            }
        }

    Lanza:
    ------
    FileNotFoundError:
        Si alguno de los archivos especificados no existe en el sistema de archivos.
    ExtraccionError:
        Si ocurre un error al procesar, parsear o leer las hojas o el archivo CSV.
    """
    # -------------------------------------------------------------------------
    # 1. Validación de existencia de archivos
    # -------------------------------------------------------------------------
    ruta_excel_str = str(ruta_excel)
    ruta_csv_str = str(ruta_csv)

    if not os.path.exists(ruta_excel_str):
        error_msg = f"El archivo Excel no fue encontrado en la ruta: {ruta_excel_str}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    if not os.path.exists(ruta_csv_str):
        error_msg = f"El archivo CSV no fue encontrado en la ruta: {ruta_csv_str}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    # -------------------------------------------------------------------------
    # 2. Lectura resiliente del archivo Excel (PROYECTO_NovaApp.xlsx)
    # -------------------------------------------------------------------------
    hojas_requeridas = ['fact_suscripciones', 'dim_cuenta', 'dim_plan', 'dim_tiempo']
    dfs_excel: Dict[str, pd.DataFrame] = {}

    try:
        logger.info("Iniciando lectura de hojas del archivo Excel: %s", ruta_excel_str)
        with pd.ExcelFile(ruta_excel_str) as xls:
            hojas_disponibles = xls.sheet_names
            logger.debug("Hojas encontradas en el libro Excel: %s", hojas_disponibles)

            for hoja in hojas_requeridas:
                nombre_real = _resolver_nombre_hoja(hojas_disponibles, hoja)
                df_hoja = pd.read_excel(xls, sheet_name=nombre_real)
                dfs_excel[hoja] = df_hoja
                logger.info("Hoja '%s' cargada exitosamente con %d filas.", hoja, len(df_hoja))

    except Exception as e:
        error_msg = f"Error al procesar el archivo Excel '{ruta_excel_str}': {str(e)}"
        logger.exception(error_msg)
        raise ExtraccionError(error_msg) from e

    # -------------------------------------------------------------------------
    # 3. Lectura resiliente del archivo CSV (na_fact_uso.csv)
    # -------------------------------------------------------------------------
    try:
        logger.info("Iniciando lectura del archivo CSV masivo: %s", ruta_csv_str)
        try:
            df_uso = pd.read_csv(ruta_csv_str)
        except UnicodeDecodeError:
            logger.warning("Fallo en codificación UTF-8 para %s. Reintentando con codificación latin1.", ruta_csv_str)
            df_uso = pd.read_csv(ruta_csv_str, encoding='latin1')

        logger.info("Tabla 'fact_uso' cargada exitosamente con %d filas.", len(df_uso))

    except Exception as e:
        error_msg = f"Error al leer el archivo CSV '{ruta_csv_str}': {str(e)}"
        logger.exception(error_msg)
        raise ExtraccionError(error_msg) from e

    # -------------------------------------------------------------------------
    # 4. Auditoría Inicial: Conteo total de filas de tablas de hechos
    # -------------------------------------------------------------------------
    df_subs = dfs_excel['fact_suscripciones']
    total_filas_hechos = int(len(df_subs) + len(df_uso))
    logger.info("Auditoría de extracción completada: Total filas de hechos = %d", total_filas_hechos)

    # -------------------------------------------------------------------------
    # 5. Estructuración y retorno de resultados
    # -------------------------------------------------------------------------
    resultado: Dict[str, Any] = {
        'dataframes': {
            'fact_suscripciones': df_subs,
            'fact_uso': df_uso,
            'dim_cuenta': dfs_excel['dim_cuenta'],
            'dim_plan': dfs_excel['dim_plan'],
            'dim_tiempo': dfs_excel['dim_tiempo'],
        },
        'metricas_extraccion': {
            'total_filas_hechos': total_filas_hechos,
        }
    }

    return resultado
