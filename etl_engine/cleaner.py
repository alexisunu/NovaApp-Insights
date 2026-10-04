"""
Módulo de limpieza y calidad de datos (cleaner.py) - NovaApp Insights.

Pipeline ETL que depura y audita cuatro tablas del caso NovaApp:
- fact_suscripciones: sub_id, cuenta_id, fecha_id, plan_id, mrr, usuarios_activos, churn
- fact_uso: uso_id, cuenta_id, fecha_id, feature, duracion_min
- dim_cuenta: cuenta_id, pais, fecha_registro
- dim_plan: plan_id, nombre_plan, precio_mensual, limite_usuarios
- dim_tiempo: fecha_id

Orden de ejecución:
1. Unicidad: se cuenta sobre los datos crudos y luego se deduplica (se conserva el primero).
2. El resto de los diagnósticos se calculan sobre los datos ya deduplicados y antes de
   cualquier corrección o cuarentena.
3. Se aplican correcciones, banderas y cuarentena. Nunca se imputan ceros ni promedios.

Uso por línea de comandos (desde la raíz del proyecto):
    python -m etl_engine.cleaner --excel RUTA_EXCEL --csv RUTA_CSV
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


# Dimensiones de calidad permitidas en diagnostico_calidad
DIMENSIONES = ('Unicidad', 'Exactitud', 'Completitud', 'Consistencia',
               'Integridad', 'Oportunidad', 'Formato')

# Decisiones permitidas en diagnostico_calidad
DECISIONES = ('Corregir', 'Eliminar', 'Marcar', 'Cuarentena', 'No tocar')

# Valores canónicos explícitos para la normalización de categorías
PAISES_CANONICOS = ['Argentina', 'Chile', 'Colombia', 'Ecuador', 'España', 'México', 'Perú', 'Uruguay']
FEATURES_CANONICOS = ['Alertas', 'API', 'Colaboración', 'Dashboard',
                      'Exportar', 'Integración', 'Reporte']

NOMBRE_PLAN_FREE = 'Free'

# Columnas obligatorias por tabla
COLUMNAS_REQUERIDAS = {
    'fact_suscripciones': ['sub_id', 'cuenta_id', 'fecha_id', 'plan_id', 'mrr', 'usuarios_activos', 'churn'],
    'fact_uso': ['uso_id', 'cuenta_id', 'fecha_id', 'feature', 'duracion_min'],
    'dim_cuenta': ['cuenta_id', 'pais', 'fecha_registro'],
    'dim_plan': ['plan_id', 'nombre_plan', 'precio_mensual', 'limite_usuarios'],
    'dim_tiempo': ['fecha_id'],
}

COLUMNAS_RECHAZOS = ['tabla_origen', 'id_origen', 'cuenta_id', 'fecha_id', 'motivo_rechazo', 'fila_original']


# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def _validar_columnas(tablas: Dict[str, pd.DataFrame]) -> None:
    """Lanza ValueError si falta alguna columna obligatoria en alguna tabla."""
    faltantes = [f'{nombre}.{col}' for nombre, df in tablas.items()
                 for col in COLUMNAS_REQUERIDAS[nombre] if col not in df.columns]
    if faltantes:
        raise ValueError(f'Columnas obligatorias ausentes: {faltantes}')


def _responsables(dimension: str, tabla: str) -> Tuple[str, str]:
    """
    Devuelve (responsable, revisor) según el tipo de regla.
    Integridad y Oportunidad sobre hechos -> Diego / Alexis; el resto -> Alexis / Max
    (incluye la Oportunidad de dim_cuenta).
    """
    if dimension in ('Integridad', 'Oportunidad') and tabla != 'dim_cuenta':
        return 'Diego', 'Alexis'
    return 'Alexis', 'Max'


def _fila_diagnostico(dimension: str, tabla: str, regla: str, filas: int,
                      decision: str, justificacion: str, **extra: Any) -> Dict[str, Any]:
    """Construye una fila de diagnostico_calidad con responsable y revisor."""
    if dimension not in DIMENSIONES:
        raise ValueError(f'Dimensión no permitida: {dimension}')
    if decision not in DECISIONES:
        raise ValueError(f'Decisión no permitida: {decision}. Valores válidos: {DECISIONES}')
    responsable, revisor = _responsables(dimension, tabla)
    fila = {
        'dimension': dimension,
        'tabla': tabla,
        'regla': regla,
        'filas': int(filas),
        'decision': decision,
        'justificacion': justificacion,
        'responsable': responsable,
        'revisor': revisor,
    }
    fila.update(extra)
    return fila


def _contar_duplicados(df: pd.DataFrame, col_id: str) -> Tuple[int, int, int]:
    """
    Cuenta los duplicados de un identificador sobre datos crudos.

    Retorna (total de filas sobrantes, idénticas a una fila previa, mismo id con valores distintos).
    """
    sobrantes = df.duplicated(subset=[col_id], keep='first')
    identicas = df.duplicated(keep='first')
    total = int(sobrantes.sum())
    n_identicas = int((sobrantes & identicas).sum())
    return total, n_identicas, total - n_identicas


def _a_fecha(serie: pd.Series) -> pd.Series:
    """Convierte fecha_id entero AAAAMMDD a datetime (valores no interpretables -> NaT)."""
    texto = serie.astype('string').str.strip().str.replace(r'\.0+$', '', regex=True)
    return pd.to_datetime(texto, format='%Y%m%d', errors='coerce')


def _fuera_de_calendario(serie_fecha_id: pd.Series, fechas_validas: set) -> pd.Series:
    """Máscara de fecha_id que no existen en dim_tiempo (incluye valores no numéricos)."""
    numerica = pd.to_numeric(serie_fecha_id, errors='coerce')
    return ~numerica.isin(fechas_validas)


def _mapear_plan(plan_ids: pd.Series, df_plan: pd.DataFrame, columna: str) -> pd.Series:
    """Busca un atributo de dim_plan para cada plan_id (nulo si el plan no existe)."""
    plan_unico = df_plan.drop_duplicates(subset=['plan_id']).set_index('plan_id')[columna]
    return plan_ids.map(plan_unico)


def _contar_no_numericos(serie: pd.Series) -> Tuple[int, pd.Series]:
    """
    Convierte a número y cuenta cuántos valores NO nulos se vuelven nulos al convertir.

    Retorna (cantidad de valores perdidos, serie numérica).
    """
    convertida = pd.to_numeric(serie, errors='coerce')
    return int((serie.notna() & convertida.isna()).sum()), convertida


def _clave_normalizada(serie: pd.Series) -> pd.Series:
    """Clave de comparación: sin espacios sobrantes, sin tildes y en minúsculas."""
    clave = serie.astype('string').str.strip().str.replace(r'\s+', ' ', regex=True)
    clave = clave.str.normalize('NFKD').str.replace(r'[\u0300-\u036f]', '', regex=True).str.lower()
    return clave.astype('object')


def _normalizar_categoria(serie: pd.Series, canonicos: List[str]
                          ) -> Tuple[pd.Series, pd.Series, pd.Series, List[Dict[str, str]]]:
    """
    Asigna el valor canónico de una lista explícita comparando por regla
    (sin espacios sobrantes, sin diferencias de mayúsculas ni de tildes).
    Los valores no reconocidos se dejan sin tocar.

    Retorna (serie limpia, máscara de cambios, máscara de no reconocidos, mapeo de cambios únicos).
    """
    claves_canonicas = _clave_normalizada(pd.Series(canonicos))
    mapa = dict(zip(claves_canonicas, canonicos))
    candidata = _clave_normalizada(serie).map(mapa)
    reconocido = candidata.notna()

    limpia = serie.copy()
    limpia[reconocido] = candidata[reconocido]

    cambio = reconocido & (serie != limpia)
    no_reconocido = serie.notna() & ~reconocido
    mapeo = (pd.DataFrame({'original': serie[cambio], 'nuevo': limpia[cambio]})
             .drop_duplicates().to_dict(orient='records'))
    return limpia, cambio, no_reconocido, mapeo


def _armar_rechazos(df: pd.DataFrame, tabla_origen: str, col_id: str, motivo: pd.Series) -> pd.DataFrame:
    """Da formato uniforme a las filas en cuarentena (la fila completa se guarda como texto JSON)."""
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS_RECHAZOS)
    filas_json = df.to_json(orient='records', lines=True, force_ascii=False, date_format='iso').splitlines()
    return pd.DataFrame({
        'tabla_origen': tabla_origen,
        'id_origen': df[col_id].to_numpy(),
        'cuenta_id': df['cuenta_id'].to_numpy(),
        'fecha_id': df['fecha_id'].to_numpy(),
        'motivo_rechazo': motivo.to_numpy(),
        'fila_original': filas_json,
    })


def _listar(valores: pd.Series) -> str:
    """Texto con los valores únicos de una serie (para justificaciones)."""
    unicos = sorted(valores.dropna().astype(str).unique().tolist())
    return ', '.join(repr(v) for v in unicos) if unicos else 'ninguno'


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def run_quality_pipeline(
    df_subs: pd.DataFrame,
    df_uso: pd.DataFrame,
    df_cuenta: pd.DataFrame,
    df_plan: pd.DataFrame,
    df_tiempo: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]:
    """
    Ejecuta el pipeline de calidad y limpieza sobre los DataFrames de NovaApp.

    Parámetros:
    -----------
    df_subs : fact_suscripciones.
    df_uso : fact_uso.
    df_cuenta : dim_cuenta.
    df_plan : dim_plan.
    df_tiempo : dim_tiempo.

    Retorna:
    --------
    (df_subs_limpio, df_uso_limpio, df_cuenta_limpio, df_rechazos, diagnostico_calidad)
        - df_subs_limpio: suscripciones deduplicadas y corregidas, con banderas
          (mrr_original, mrr_corregido, usuarios_activos_faltante, excede_limite,
          mrr_cero_o_nulo_plan_pago, pareja_cuenta_fecha_repetida).
        - df_uso_limpio: usos deduplicados y normalizados, con la bandera duracion_min_faltante.
        - df_cuenta_limpio: cuentas con país normalizado y las banderas
          fecha_registro_inconsistente y cuenta_id_repetido (fecha_registro no se modifica).
        - df_rechazos: filas en cuarentena de ambas tablas de hechos con formato uniforme
          (tabla_origen, id_origen, cuenta_id, fecha_id, motivo_rechazo, fila_original).
        - diagnostico_calidad: lista de diccionarios de auditoría.
    """
    _validar_columnas({'fact_suscripciones': df_subs, 'fact_uso': df_uso, 'dim_cuenta': df_cuenta,
                       'dim_plan': df_plan, 'dim_tiempo': df_tiempo})
    diagnostico: List[Dict[str, Any]] = []

    def agregar(*args: Any, **kwargs: Any) -> None:
        diagnostico.append(_fila_diagnostico(*args, **kwargs))

    # -------------------------------------------------------------------------
    # 1. UNICIDAD sobre datos crudos y deduplicación (se conserva el primero)
    # -------------------------------------------------------------------------
    dup_sub, ident_sub, conf_sub = _contar_duplicados(df_subs, 'sub_id')
    dup_uso, ident_uso, conf_uso = _contar_duplicados(df_uso, 'uso_id')

    subs = df_subs.drop_duplicates(subset=['sub_id'], keep='first').reset_index(drop=True)
    uso = df_uso.drop_duplicates(subset=['uso_id'], keep='first').reset_index(drop=True)
    cuenta = df_cuenta.copy().reset_index(drop=True)

    agregar('Unicidad', 'fact_suscripciones', 'sub_id repetido', dup_sub, 'Eliminar',
            f'Se conserva el primer registro de cada sub_id; {ident_sub} filas sobrantes son idénticas '
            f'y {conf_sub} tienen el mismo id con valores distintos.')
    agregar('Unicidad', 'fact_uso', 'uso_id repetido', dup_uso, 'Eliminar',
            f'Se conserva el primer registro de cada uso_id; {ident_uso} filas sobrantes son idénticas '
            f'y {conf_uso} tienen el mismo id con valores distintos.')

    pareja_repetida = subs.duplicated(subset=['cuenta_id', 'fecha_id'], keep=False)
    agregar('Unicidad', 'fact_suscripciones', 'pareja (cuenta_id, fecha_id) repetida', pareja_repetida.sum(),
            'Marcar',
            'Suscripciones deduplicadas con más de una fila para la misma cuenta y fecha_id; '
            'se marcan con la bandera pareja_cuenta_fecha_repetida.')
    cuenta_repetida = cuenta.duplicated(subset=['cuenta_id'], keep=False)
    agregar('Unicidad', 'dim_cuenta', 'cuenta_id repetido', cuenta_repetida.sum(), 'Marcar',
            'Filas de dim_cuenta con cuenta_id repetido; se marcan con la bandera cuenta_id_repetido.')

    # -------------------------------------------------------------------------
    # 2. DIAGNÓSTICOS sobre datos deduplicados (antes de corregir o enviar a cuarentena)
    # -------------------------------------------------------------------------
    # Formato: valores no nulos que se vuelven nulos al convertir a número
    perdidos_mrr, mrr_num = _contar_no_numericos(subs['mrr'])
    perdidos_usu, usuarios_num = _contar_no_numericos(subs['usuarios_activos'])
    perdidos_dur, duracion_num = _contar_no_numericos(uso['duracion_min'])

    # Atributos del plan de cada suscripción
    nombre_plan = _mapear_plan(subs['plan_id'], df_plan, 'nombre_plan')
    precio_plan = pd.to_numeric(_mapear_plan(subs['plan_id'], df_plan, 'precio_mensual'), errors='coerce')
    limite_plan = pd.to_numeric(_mapear_plan(subs['plan_id'], df_plan, 'limite_usuarios'), errors='coerce')
    es_free = nombre_plan == NOMBRE_PLAN_FREE
    es_pago = nombre_plan.notna() & ~es_free

    # Exactitud
    mrr_negativo = mrr_num < 0
    coincide_precio = mrr_negativo & np.isclose(mrr_num.abs(), precio_plan)
    agregar('Exactitud', 'fact_suscripciones', 'mrr < 0', mrr_negativo.sum(), 'Corregir',
            f'mrr negativo convertido a valor absoluto; {int(coincide_precio.sum())} de '
            f'{int(mrr_negativo.sum())} |mrr| coinciden con el precio de su plan. '
            f'El valor original se conserva en mrr_original.')

    mrr_99999 = mrr_num == 99999
    n_99999_free = int((mrr_99999 & es_free).sum())
    n_99999_sin_plan = int((mrr_99999 & precio_plan.isna()).sum())
    agregar('Exactitud', 'fact_suscripciones', 'mrr == 99999', mrr_99999.sum(), 'Corregir',
            f'mrr == 99999 reemplazado por precio_mensual de su plan (por plan_id); {n_99999_free} son del '
            f'plan Free (quedan en 0) y {n_99999_sin_plan} sin plan encontrado quedan nulos. '
            f'El valor original se conserva en mrr_original.')

    excede_limite = (usuarios_num > limite_plan) & usuarios_num.notna() & limite_plan.notna()
    agregar('Exactitud', 'fact_suscripciones', 'usuarios activos superan límite de su plan',
            excede_limite.sum(), 'Marcar',
            'usuarios_activos mayor que limite_usuarios de su plan en dim_plan; '
            'se marca con la bandera excede_limite sin modificar el valor.')

    # Completitud
    free_mrr_cero = es_free & (mrr_num == 0)
    agregar('Completitud', 'fact_suscripciones', 'Plan Free con mrr 0', free_mrr_cero.sum(), 'No tocar',
            'Plan Free (nombre_plan == "Free" en dim_plan) con mrr 0: valor esperado para un plan gratuito.')

    pago_vacio = es_pago & (mrr_num.isna() | (mrr_num == 0))
    agregar('Completitud', 'fact_suscripciones', 'mrr 0 o nulo en plan de pago', pago_vacio.sum(), 'Marcar',
            'Suscripciones de planes de pago con mrr 0 o nulo; se marcan con la bandera '
            'mrr_cero_o_nulo_plan_pago.')

    usuarios_nulo = subs['usuarios_activos'].isna()
    agregar('Completitud', 'fact_suscripciones', 'usuarios_activos vacío o nulo', usuarios_nulo.sum(),
            'Marcar',
            'Ausencia dispersa sin patrón; causa no determinada. Se conserva el nulo (sin imputar) '
            'y se crea la bandera usuarios_activos_faltante.')

    duracion_nula = uso['duracion_min'].isna()
    agregar('Completitud', 'fact_uso', 'duracion_min vacía o nula', duracion_nula.sum(), 'Marcar',
            'Ausencia dispersa sin patrón; causa no determinada. Se conserva el nulo (sin imputar) '
            'y se crea la bandera duracion_min_faltante.')

    # Formato
    texto_formato = ('Valores no nulos que se vuelven nulos al convertir a número; '
                     'si hubiera, quedarían como nulos en la tabla limpia.')
    agregar('Formato', 'fact_suscripciones', 'mrr no numérico', perdidos_mrr, 'Marcar', texto_formato)
    agregar('Formato', 'fact_suscripciones', 'usuarios_activos no numérico', perdidos_usu, 'Marcar', texto_formato)
    agregar('Formato', 'fact_uso', 'duracion_min no numérica', perdidos_dur, 'Marcar', texto_formato)

    # Consistencia
    pais_limpio, cambio_pais, no_rec_pais, mapeo_pais = _normalizar_categoria(cuenta['pais'], PAISES_CANONICOS)
    feature_limpio, cambio_feat, no_rec_feat, mapeo_feat = _normalizar_categoria(uso['feature'], FEATURES_CANONICOS)
    texto_consistencia = ('Se quitan espacios sobrantes y se unifican mayúsculas y tildes '
                          'contra la lista canónica explícita de {}.')
    agregar('Consistencia', 'dim_cuenta', 'normalizar pais', cambio_pais.sum(), 'Corregir',
            texto_consistencia.format('países'), mapeo_cambios=mapeo_pais)
    agregar('Consistencia', 'dim_cuenta', 'pais no reconocido', no_rec_pais.sum(), 'No tocar',
            f'Valores fuera de la lista canónica; se dejan sin modificar. Valores: {_listar(cuenta.loc[no_rec_pais, "pais"])}.')
    agregar('Consistencia', 'fact_uso', 'normalizar feature', cambio_feat.sum(), 'Corregir',
            texto_consistencia.format('features'), mapeo_cambios=mapeo_feat)
    agregar('Consistencia', 'fact_uso', 'feature no reconocido', no_rec_feat.sum(), 'No tocar',
            f'Valores fuera de la lista canónica; se dejan sin modificar. Valores: {_listar(uso.loc[no_rec_feat, "feature"])}.')

    # Integridad y Oportunidad (calendario y llaves)
    fechas_validas = set(pd.to_numeric(df_tiempo['fecha_id'], errors='coerce').dropna().astype('int64'))
    cuentas_validas = set(cuenta['cuenta_id'].dropna())
    planes_validos = set(df_plan['plan_id'].dropna())

    uso_sin_cuenta = ~uso['cuenta_id'].isin(cuentas_validas)
    uso_fuera_cal = _fuera_de_calendario(uso['fecha_id'], fechas_validas)
    uso_ambos = uso_sin_cuenta & uso_fuera_cal
    fechas_afectadas = pd.to_numeric(uso.loc[uso_fuera_cal, 'fecha_id'], errors='coerce').dropna()
    rango_afectado = (f'fecha_id afectadas entre {int(fechas_afectadas.min())} y {int(fechas_afectadas.max())}'
                      if not fechas_afectadas.empty else 'sin fecha_id afectadas')

    agregar('Integridad', 'fact_uso', 'cuenta_id sin cuenta', uso_sin_cuenta.sum(), 'Cuarentena',
            'cuenta_id de fact_uso inexistente en dim_cuenta; las filas pasan a df_rechazos con su motivo.')
    agregar('Oportunidad', 'fact_uso', 'fecha_id fuera de dim_tiempo', uso_fuera_cal.sum(), 'Cuarentena',
            f'fecha_id de fact_uso inexistente en dim_tiempo ({rango_afectado}); '
            f'las filas pasan a df_rechazos con su motivo.')
    agregar('Integridad', 'fact_uso', 'cuenta_id sin cuenta y fecha_id fuera de dim_tiempo', uso_ambos.sum(),
            'Cuarentena', 'Filas con ambos motivos a la vez (subconjunto de las dos reglas anteriores).')

    subs_sin_cuenta = ~subs['cuenta_id'].isin(cuentas_validas)
    subs_sin_plan = ~subs['plan_id'].isin(planes_validos)
    subs_fuera_cal = _fuera_de_calendario(subs['fecha_id'], fechas_validas)
    agregar('Integridad', 'fact_suscripciones', 'cuenta_id sin cuenta', subs_sin_cuenta.sum(), 'Cuarentena',
            'cuenta_id de fact_suscripciones inexistente en dim_cuenta; las filas pasan a df_rechazos.')
    agregar('Integridad', 'fact_suscripciones', 'plan_id sin plan', subs_sin_plan.sum(), 'Cuarentena',
            'plan_id de fact_suscripciones inexistente en dim_plan; las filas pasan a df_rechazos.')
    agregar('Integridad', 'fact_suscripciones', 'fecha_id fuera de dim_tiempo', subs_fuera_cal.sum(), 'Cuarentena',
            'fecha_id de fact_suscripciones inexistente en dim_tiempo; las filas pasan a df_rechazos.')

    # Oportunidad a nivel de cuenta: fecha_registro frente a suscripciones y calendario
    fecha_sub = _a_fecha(subs['fecha_id'])
    primera_sub = pd.DataFrame({'c': subs['cuenta_id'], 'f': fecha_sub}).groupby('c')['f'].min()
    registro = pd.to_datetime(cuenta['fecha_registro'], errors='coerce')
    registro_inconsistente = registro > cuenta['cuenta_id'].map(primera_sub)
    ultima_fecha_tiempo = _a_fecha(df_tiempo['fecha_id']).max()
    registro_post_calendario = (registro > ultima_fecha_tiempo) if pd.notna(ultima_fecha_tiempo) \
        else pd.Series(False, index=cuenta.index)
    causa = 'Causa no determinada; habría que consultar al dueño del dato en CRM.'
    agregar('Oportunidad', 'dim_cuenta', 'fecha_registro posterior a la primera suscripción',
            registro_inconsistente.sum(), 'Marcar',
            f'Cuentas con fecha_registro posterior a la primera fecha_id de sus suscripciones; '
            f'se marcan con la bandera fecha_registro_inconsistente sin modificar fecha_registro. {causa}')
    agregar('Oportunidad', 'dim_cuenta', 'fecha_registro posterior a la última fecha de dim_tiempo',
            registro_post_calendario.sum(), 'Marcar',
            f'Cuentas con fecha_registro posterior a la última fecha de dim_tiempo '
            f'({"no calculable" if pd.isna(ultima_fecha_tiempo) else ultima_fecha_tiempo.date()}); {causa}')

    # -------------------------------------------------------------------------
    # 3. CUARENTENA (formato uniforme para ambas tablas de hechos)
    # -------------------------------------------------------------------------
    rechazo_uso = uso_sin_cuenta | uso_fuera_cal
    motivo_uso = pd.Series(np.select(
        [uso_ambos, uso_sin_cuenta, uso_fuera_cal],
        ['Cuenta inexistente; Fecha fuera de calendario oficial', 'Cuenta inexistente',
         'Fecha fuera de calendario oficial'], default=''), index=uso.index)

    rechazo_subs = subs_sin_cuenta | subs_sin_plan | subs_fuera_cal
    partes = (pd.Series(np.where(subs_sin_cuenta, 'Cuenta inexistente', ''), index=subs.index) + '; '
              + pd.Series(np.where(subs_sin_plan, 'Plan inexistente', ''), index=subs.index) + '; '
              + pd.Series(np.where(subs_fuera_cal, 'Fecha fuera de calendario oficial', ''), index=subs.index))
    motivo_subs = partes.str.replace(r'(; )+', '; ', regex=True).str.strip('; ')

    df_rechazos = pd.concat([
        _armar_rechazos(subs[rechazo_subs], 'fact_suscripciones', 'sub_id', motivo_subs[rechazo_subs]),
        _armar_rechazos(uso[rechazo_uso], 'fact_uso', 'uso_id', motivo_uso[rechazo_uso]),
    ], ignore_index=True)

    # -------------------------------------------------------------------------
    # 4. CORRECCIONES Y BANDERAS (sin imputar ceros ni promedios)
    # -------------------------------------------------------------------------
    subs_out = subs.copy()
    mrr_corregido = mrr_num.where(~mrr_negativo, mrr_num.abs())
    mrr_corregido = mrr_corregido.where(~mrr_99999, precio_plan)  # plan no encontrado -> nulo
    subs_out['mrr_original'] = mrr_num
    subs_out['mrr'] = mrr_corregido
    subs_out['mrr_corregido'] = (mrr_negativo | mrr_99999).to_numpy()
    subs_out['usuarios_activos'] = usuarios_num
    subs_out['usuarios_activos_faltante'] = usuarios_num.isna().to_numpy()
    subs_out['excede_limite'] = excede_limite.to_numpy()
    subs_out['mrr_cero_o_nulo_plan_pago'] = pago_vacio.to_numpy()
    subs_out['pareja_cuenta_fecha_repetida'] = pareja_repetida.to_numpy()
    df_subs_limpio = subs_out[~rechazo_subs].reset_index(drop=True)

    uso_out = uso.copy()
    uso_out['duracion_min'] = duracion_num
    uso_out['duracion_min_faltante'] = duracion_num.isna().to_numpy()
    uso_out['feature'] = feature_limpio
    df_uso_limpio = uso_out[~rechazo_uso].reset_index(drop=True)

    cuenta_out = cuenta.copy()
    cuenta_out['pais'] = pais_limpio
    cuenta_out['fecha_registro_inconsistente'] = registro_inconsistente.to_numpy()
    cuenta_out['cuenta_id_repetido'] = cuenta_repetida.to_numpy()
    df_cuenta_limpio = cuenta_out

    # -------------------------------------------------------------------------
    # 5. VERIFICACIÓN FINAL DE MRR (sobre la tabla limpia)
    # -------------------------------------------------------------------------
    precio_final = pd.to_numeric(_mapear_plan(df_subs_limpio['plan_id'], df_plan, 'precio_mensual'), errors='coerce')
    mrr_final = df_subs_limpio['mrr']
    distinto_precio = mrr_final.notna() & ~np.isclose(mrr_final, precio_final)
    agregar('Exactitud', 'fact_suscripciones', 'mrr distinto del precio de su plan', distinto_precio.sum(),
            'No tocar', 'Verificación posterior a la corrección; solo informa.')
    agregar('Exactitud', 'fact_suscripciones', 'mrr nulo', mrr_final.isna().sum(), 'No tocar',
            'Verificación posterior a la corrección; solo informa.')

    print('Valores únicos de pais tras limpiar:', sorted(df_cuenta_limpio['pais'].dropna().unique().tolist()))
    print('Valores únicos de feature tras limpiar:', sorted(df_uso_limpio['feature'].dropna().unique().tolist()))

    return df_subs_limpio, df_uso_limpio, df_cuenta_limpio, df_rechazos, diagnostico


# =============================================================================
# EJECUCIÓN POR LÍNEA DE COMANDOS: CONCILIACIÓN Y TABLA DE VERIFICACIÓN
# =============================================================================

def _filas_regla(diagnostico: List[Dict[str, Any]], tabla: str, regla: str) -> Any:
    """Busca el conteo de una regla; devuelve 'N/D' si la regla no existe."""
    for fila in diagnostico:
        if fila['tabla'] == tabla and fila['regla'] == regla:
            return fila['filas']
    return 'N/D'


def _main() -> int:
    """Ejecuta el pipeline con las rutas recibidas e imprime conciliación y verificación."""
    parser = argparse.ArgumentParser(description='Pipeline de calidad de datos NovaApp.')
    parser.add_argument('--excel', required=True, help='Ruta del Excel (PROYECTO_NovaApp.xlsx)')
    parser.add_argument('--csv', required=True, help='Ruta del CSV de uso (fact_uso)')
    args = parser.parse_args()

    # Permite ejecutar también como "python etl_engine/cleaner.py" desde la raíz del proyecto
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from etl_engine.extractor import extraer_datos

    datos = extraer_datos(args.excel, args.csv)
    dfs = datos['dataframes']
    extraidas = datos['metricas_extraccion']['total_filas_hechos']

    subs_l, uso_l, cuenta_l, rechazos, diag = run_quality_pipeline(
        dfs['fact_suscripciones'], dfs['fact_uso'], dfs['dim_cuenta'], dfs['dim_plan'], dfs['dim_tiempo'])

    # Conciliación de filas de hechos
    duplicados = (_filas_regla(diag, 'fact_suscripciones', 'sub_id repetido')
                  + _filas_regla(diag, 'fact_uso', 'uso_id repetido'))
    cuarentena = len(rechazos)
    validas = len(subs_l) + len(uso_l)
    conciliacion = extraidas - duplicados - cuarentena
    print('\nCONCILIACIÓN DE FILAS DE HECHOS')
    print(f'  {extraidas} - {duplicados} - {cuarentena} = {conciliacion} (filas válidas: {validas})')
    if not rechazos.empty:
        print('  Cuarentena por tabla y motivo:')
        print(rechazos.groupby(['tabla_origen', 'motivo_rechazo']).size().rename('filas').to_string())

    # Conteos que requieren cálculo adicional
    dup_sub = _contar_duplicados(dfs['fact_suscripciones'], 'sub_id')
    dup_uso = _contar_duplicados(dfs['fact_uso'], 'uso_id')
    negativos = subs_l['mrr_original'] < 0
    precio_plan = pd.to_numeric(_mapear_plan(subs_l['plan_id'], dfs['dim_plan'], 'precio_mensual'), errors='coerce')
    coinciden = int((negativos & np.isclose(subs_l['mrr_original'].abs(), precio_plan)).sum())
    n_formato = sum(_filas_regla(diag, t, r) for t, r in [
        ('fact_suscripciones', 'mrr no numérico'), ('fact_suscripciones', 'usuarios_activos no numérico'),
        ('fact_uso', 'duracion_min no numérica')])

    S, U, C = 'fact_suscripciones', 'fact_uso', 'dim_cuenta'
    verificaciones = [
        ('Duplicados sub_id', 420, dup_sub[0]),
        ('  sub_id idénticos', 420, dup_sub[1]),
        ('  sub_id en conflicto', 0, dup_sub[2]),
        ('Duplicados uso_id', 2600, dup_uso[0]),
        ('  uso_id idénticos', 2600, dup_uso[1]),
        ('  uso_id en conflicto', 0, dup_uso[2]),
        ('Parejas (cuenta, mes) repetidas', 0, _filas_regla(diag, S, 'pareja (cuenta_id, fecha_id) repetida')),
        ('cuenta_id repetido en dim_cuenta', 0, _filas_regla(diag, C, 'cuenta_id repetido')),
        ('mrr < 0', 124, _filas_regla(diag, S, 'mrr < 0')),
        ('  de esos, |mrr| coincide con el precio', 124, coinciden),
        ('mrr == 99999', 260, _filas_regla(diag, S, 'mrr == 99999')),
        ('Usuarios superan el límite del plan', 317, _filas_regla(diag, S, 'usuarios activos superan límite de su plan')),
        ('Free con mrr=0', 22558, _filas_regla(diag, S, 'Plan Free con mrr 0')),
        ('mrr 0 o nulo en planes de pago', 0, _filas_regla(diag, S, 'mrr 0 o nulo en plan de pago')),
        ('usuarios_activos nulos', 540, _filas_regla(diag, S, 'usuarios_activos vacío o nulo')),
        ('duracion_min nulos', 2100, _filas_regla(diag, U, 'duracion_min vacía o nula')),
        ('Integridad suscripciones: sin cuenta', 0, _filas_regla(diag, S, 'cuenta_id sin cuenta')),
        ('Integridad suscripciones: sin plan', 0, _filas_regla(diag, S, 'plan_id sin plan')),
        ('Integridad suscripciones: fecha fuera', 0, _filas_regla(diag, S, 'fecha_id fuera de dim_tiempo')),
        ('fact_uso sin cuenta', 1800, _filas_regla(diag, U, 'cuenta_id sin cuenta')),
        ('fact_uso fecha fuera de dim_tiempo', 900, _filas_regla(diag, U, 'fecha_id fuera de dim_tiempo')),
        ('fact_uso ambos motivos', 0, _filas_regla(diag, U, 'cuenta_id sin cuenta y fecha_id fuera de dim_tiempo')),
        ('Cuentas con registro posterior a su 1ª suscripción', 3585,
         _filas_regla(diag, C, 'fecha_registro posterior a la primera suscripción')),
        ('Cuentas con registro posterior al calendario', 926,
         _filas_regla(diag, C, 'fecha_registro posterior a la última fecha de dim_tiempo')),
        ('Filas finales: suscripciones', 73942, len(subs_l)),
        ('Filas finales: uso', 247300, len(uso_l)),
        ('Filas finales: cuentas', 5000, len(cuenta_l)),
        ('Filas finales: rechazos', 2700, cuarentena),
        ('Conciliación (extraídas - duplicados - cuarentena)', 321242, conciliacion),
        ('Conciliación coincide con filas válidas', validas, conciliacion),
        ('mrr distinto del precio de su plan', 0, _filas_regla(diag, S, 'mrr distinto del precio de su plan')),
        ('mrr nulos', 0, _filas_regla(diag, S, 'mrr nulo')),
        ('Valores únicos de pais', 8, cuenta_l['pais'].nunique()),
        ('Features no reconocidos', 0, _filas_regla(diag, U, 'feature no reconocido')),
        ('No nulos que se vuelven nulos al convertir a número', 0, n_formato),
    ]

    print('\nTABLA DE VERIFICACIÓN')
    ancho = max(len(v[0]) for v in verificaciones)
    print(f'{"chequeo".ljust(ancho)} | {"esperado":>9} | {"obtenido":>9} | resultado')
    fallas = 0
    for nombre, esperado, obtenido in verificaciones:
        ok = esperado == obtenido
        fallas += 0 if ok else 1
        print(f'{nombre.ljust(ancho)} | {esperado:>9} | {str(obtenido):>9} | {"OK" if ok else "FALLA"}')
    print(f'\nResumen: {len(verificaciones) - fallas} OK, {fallas} FALLA')
    return 1 if fallas else 0


if __name__ == '__main__':
    sys.exit(_main())