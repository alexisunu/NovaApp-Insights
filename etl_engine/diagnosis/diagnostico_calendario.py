"""
Diagnóstico de solo lectura: calendario e integridad referencial (NovaApp).

Uso:
    python diagnostico_calendario.py RUTA_EXCEL RUTA_CSV [--salida diagnostico_calendario.txt]

No modifica datos ni módulos. No imprime columnas con datos personales.
fact_suscripciones se deduplica por sub_id (primero) y fact_uso por uso_id (primero)
solo en memoria; para fact_uso se muestran también los conteos crudos.
"""

import argparse
import sys
from typing import List

import pandas as pd

from etl_engine.extractor import extraer_datos

_lineas: List[str] = []


def emitir(texto: str = '') -> None:
    """Imprime en consola y acumula la línea para el archivo de salida."""
    print(texto)
    _lineas.append(str(texto))


def titulo(letra: str, nombre: str, explicacion: str) -> None:
    emitir()
    emitir('=' * 78)
    emitir(f'({letra}) {nombre}')
    emitir(f'    Qué mide: {explicacion}')
    emitir('-' * 78)


def _guardar(ruta: str) -> None:
    try:
        with open(ruta, 'w', encoding='utf-8') as f:
            f.write('\n'.join(_lineas) + '\n')
        print(f'\nResultados guardados en: {ruta}')
    except OSError as e:
        print(f'No se pudo guardar el archivo de resultados: {e}')


def _fechas(serie: pd.Series) -> pd.Series:
    """Convierte fecha_id entero AAAAMMDD a datetime (inválidos -> NaT)."""
    txt = serie.astype('string').str.strip().str.replace(r'\.0+$', '', regex=True)
    return pd.to_datetime(txt, format='%Y%m%d', errors='coerce')


def _resumen_fechas(df: pd.DataFrame, fuera: pd.Series, etiqueta: str) -> None:
    """Imprime mínima, máxima y conteo por año y mes de las filas fuera de calendario."""
    sub = df[fuera]
    emitir(f'{etiqueta}: filas con fecha_id fuera de dim_tiempo = {len(sub)}')
    if sub.empty:
        emitir('No hay filas fuera de calendario.')
        return
    f = _fechas(sub['fecha_id'])
    ilegibles = int(f.isna().sum())
    if ilegibles:
        emitir(f'AVISO: {ilegibles} fecha_id no interpretables como AAAAMMDD (excluidas de mín/máx y conteos).')
    f = f.dropna()
    if f.empty:
        emitir('Ninguna fecha interpretable; no se puede calcular el rango.')
        return
    emitir(f'Fecha mínima: {f.min().date()} | Fecha máxima: {f.max().date()}')
    emitir('Conteo por año:')
    emitir(f.dt.year.value_counts().sort_index().rename('filas').to_string())
    emitir('Conteo por mes (AAAA-MM):')
    emitir(f.dt.strftime('%Y-%m').value_counts().sort_index().rename('filas').to_string())


def main() -> int:
    parser = argparse.ArgumentParser(description='Diagnóstico de calendario e integridad (solo lectura).')
    parser.add_argument('ruta_excel')
    parser.add_argument('ruta_csv')
    parser.add_argument('--salida', default='diagnostico_calendario.txt')
    args = parser.parse_args()

    emitir('DIAGNÓSTICO DE CALENDARIO E INTEGRIDAD (solo lectura)')

    try:
        dfs = extraer_datos(args.ruta_excel, args.ruta_csv)['dataframes']
    except Exception as e:  # noqa: BLE001
        emitir(f'ERROR: no se pudieron cargar los datos: {e}')
        _guardar(args.salida)
        return 1

    uso_raw = dfs['fact_uso']
    subs_raw = dfs['fact_suscripciones']
    cuenta = dfs['dim_cuenta']
    plan = dfs['dim_plan']
    tiempo = dfs['dim_tiempo']

    def faltan(df, nombre, cols):
        return [f'{nombre}.{c}' for c in cols if c not in df.columns]

    # Preparación segura
    ok_tiempo = 'fecha_id' in tiempo.columns
    if not ok_tiempo:
        emitir('AVISO: dim_tiempo.fecha_id ausente; no se puede determinar fechas fuera de calendario.')
    fechas_validas = set(pd.to_numeric(tiempo['fecha_id'], errors='coerce').dropna().astype('int64')) if ok_tiempo else set()
    if ok_tiempo:
        emitir(f'dim_tiempo: {len(tiempo)} fechas válidas (fecha_id distintos: {len(fechas_validas)})')

    uso = subs = None
    if not faltan(uso_raw, 'fact_uso', ['uso_id', 'cuenta_id', 'fecha_id']):
        uso = uso_raw.drop_duplicates(subset=['uso_id'], keep='first')[['uso_id', 'cuenta_id', 'fecha_id']].copy()
    if not faltan(subs_raw, 'fact_suscripciones', ['sub_id', 'cuenta_id', 'plan_id', 'fecha_id']):
        subs = subs_raw.drop_duplicates(subset=['sub_id'], keep='first')[['sub_id', 'cuenta_id', 'plan_id', 'fecha_id']].copy()
    emitir(f'fact_uso: {len(uso_raw)} filas crudas, {"-" if uso is None else len(uso)} deduplicadas por uso_id')
    emitir(f'fact_suscripciones: {len(subs_raw)} filas crudas, {"-" if subs is None else len(subs)} deduplicadas por sub_id')

    def fuera_de_calendario(df):
        num = pd.to_numeric(df['fecha_id'], errors='coerce')
        return ~num.isin(fechas_validas) | num.isna()

    def seguro(letra, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            emitir(f'No se pudo calcular ({letra}): {e}')

    # a) fact_uso
    def bloque_a():
        titulo('a', 'fact_uso con fecha_id fuera de dim_tiempo',
               'rango y distribución temporal de usos cuya fecha no existe en el calendario oficial.')
        if uso is None or not ok_tiempo:
            emitir('No calculable: faltan columnas necesarias.')
            return
        _resumen_fechas(uso_raw, fuera_de_calendario(uso_raw), 'Sobre fact_uso CRUDA')
        emitir()
        _resumen_fechas(uso, fuera_de_calendario(uso), 'Sobre fact_uso DEDUPLICADA por uso_id')
    seguro('a', bloque_a)

    # b) fact_suscripciones
    def bloque_b():
        titulo('b', 'fact_suscripciones con fecha_id fuera de dim_tiempo',
               'rango y distribución temporal de suscripciones cuya fecha no existe en el calendario oficial.')
        if subs is None or not ok_tiempo:
            emitir('No calculable: faltan columnas necesarias.')
            return
        _resumen_fechas(subs, fuera_de_calendario(subs), 'Sobre fact_suscripciones DEDUPLICADA por sub_id')
    seguro('b', bloque_b)

    # c) integridad en suscripciones
    def bloque_c():
        titulo('c', 'Integridad referencial en fact_suscripciones',
               'suscripciones con cuenta_id ausente en dim_cuenta o plan_id ausente en dim_plan.')
        if subs is None:
            emitir('No calculable: faltan columnas en fact_suscripciones.')
            return
        if 'cuenta_id' in cuenta.columns:
            sin_cuenta = ~subs['cuenta_id'].isin(set(cuenta['cuenta_id'].dropna()))
            emitir(f'cuenta_id inexistente en dim_cuenta: {int(sin_cuenta.sum())} filas '
                   f'({int(subs.loc[sin_cuenta, "cuenta_id"].nunique())} cuentas distintas)')
        else:
            emitir('No calculable: dim_cuenta.cuenta_id ausente.')
        if 'plan_id' in plan.columns:
            sin_plan = ~subs['plan_id'].isin(set(plan['plan_id'].dropna()))
            emitir(f'plan_id inexistente en dim_plan: {int(sin_plan.sum())} filas '
                   f'({int(subs.loc[sin_plan, "plan_id"].nunique())} planes distintos)')
        else:
            emitir('No calculable: dim_plan.plan_id ausente.')
    seguro('c', bloque_c)

    # d) solapamiento en uso
    def bloque_d():
        titulo('d', 'Solapamiento de motivos en fact_uso',
               'cuenta_id sin cuenta que además tienen fecha fuera de calendario.')
        if uso is None or not ok_tiempo or 'cuenta_id' not in cuenta.columns:
            emitir('No calculable: faltan columnas necesarias.')
            return
        for etiqueta, df in [('CRUDA', uso_raw), ('DEDUPLICADA por uso_id', uso)]:
            sin_cuenta = ~df['cuenta_id'].isin(set(cuenta['cuenta_id'].dropna()))
            fuera = fuera_de_calendario(df)
            emitir(f'fact_uso {etiqueta}:')
            emitir(f'  Solo cuenta inexistente:   {int((sin_cuenta & ~fuera).sum())}')
            emitir(f'  Solo fecha fuera calendario: {int((~sin_cuenta & fuera).sum())}')
            emitir(f'  AMBOS motivos (solapamiento): {int((sin_cuenta & fuera).sum())}')
            emitir(f'  Total con al menos un motivo: {int((sin_cuenta | fuera).sum())}')
    seguro('d', bloque_d)

    _guardar(args.salida)
    return 0


if __name__ == '__main__':
    sys.exit(main())
