"""
Diagnóstico de solo lectura: vacíos cruzados y unicidad (NovaApp).

Uso:
    python diagnostico_vacios_cruce.py RUTA_EXCEL RUTA_CSV [--salida diagnostico_vacios_cruce.txt]

No modifica datos ni módulos. No imprime columnas con datos personales
(solo identificadores técnicos como cuenta_id). Trabaja con fact_suscripciones
deduplicada por sub_id y fact_uso deduplicada por uso_id (primero), en memoria.
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
    """fecha_id entero AAAAMMDD -> datetime (inválidos -> NaT)."""
    txt = serie.astype('string').str.strip().str.replace(r'\.0+$', '', regex=True)
    return pd.to_datetime(txt, format='%Y%m%d', errors='coerce')


def por_grupo(df: pd.DataFrame, flag: pd.Series, clave: pd.Series, etiqueta: str,
              top: int = None, ordenar_por_nulos: bool = False) -> None:
    """Tabla: nulos, total del grupo y % de nulos dentro del grupo."""
    tmp = pd.DataFrame({'g': clave.astype('object').where(clave.notna(), 'SIN DATO'), 'n': flag})
    res = tmp.groupby('g').agg(nulos=('n', 'sum'), total=('n', 'size'))
    res['pct_nulos_en_grupo'] = (res['nulos'] / res['total'] * 100).round(2)
    res.index.name = etiqueta
    if ordenar_por_nulos:
        res = res.sort_values('nulos', ascending=False)
    if top:
        res = res.head(top)
    emitir(res.to_string())


def main() -> int:
    parser = argparse.ArgumentParser(description='Vacíos cruzados y unicidad (solo lectura).')
    parser.add_argument('ruta_excel')
    parser.add_argument('ruta_csv')
    parser.add_argument('--salida', default='diagnostico_vacios_cruce.txt')
    args = parser.parse_args()

    emitir('DIAGNÓSTICO DE VACÍOS CRUZADOS Y UNICIDAD (solo lectura)')

    try:
        dfs = extraer_datos(args.ruta_excel, args.ruta_csv)['dataframes']
    except Exception as e:  # noqa: BLE001
        emitir(f'ERROR: no se pudieron cargar los datos: {e}')
        _guardar(args.salida)
        return 1

    subs_raw, uso_raw = dfs['fact_suscripciones'], dfs['fact_uso']
    cuenta, plan = dfs['dim_cuenta'], dfs['dim_plan']

    def faltan(df, nombre, cols):
        return [f'{nombre}.{c}' for c in cols if c not in df.columns]

    f_subs = faltan(subs_raw, 'fact_suscripciones', ['sub_id', 'cuenta_id', 'fecha_id', 'plan_id', 'usuarios_activos', 'churn'])
    f_uso = faltan(uso_raw, 'fact_uso', ['uso_id', 'cuenta_id', 'fecha_id', 'feature', 'duracion_min'])
    f_plan = faltan(plan, 'dim_plan', ['plan_id', 'nombre_plan'])
    for lista in (f_subs, f_uso, f_plan):
        if lista:
            emitir(f'AVISO: columnas ausentes (algunos bloques no se pueden calcular): {lista}')

    subs = uso = None
    if not f_subs:
        subs = subs_raw.drop_duplicates(subset=['sub_id'], keep='first').copy()
        if not f_plan:
            subs = subs.merge(plan[['plan_id', 'nombre_plan']].drop_duplicates('plan_id'), on='plan_id', how='left')
        else:
            subs['nombre_plan'] = subs['plan_id']
        subs['_f'] = _fechas(subs['fecha_id'])
        malas = int(subs['_f'].isna().sum())
        emitir(f'fact_suscripciones deduplicada: {len(subs)} filas'
               + (f' | AVISO: {malas} fecha_id no interpretables' if malas else ''))
    if not f_uso:
        uso = uso_raw.drop_duplicates(subset=['uso_id'], keep='first').copy()
        uso['_f'] = _fechas(uso['fecha_id'])
        malas = int(uso['_f'].isna().sum())
        emitir(f'fact_uso deduplicada: {len(uso)} filas'
               + (f' | AVISO: {malas} fecha_id no interpretables' if malas else ''))

    def seguro(letra, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            emitir(f'No se pudo calcular ({letra}): {e}')

    # a) usuarios_activos nulo
    def bloque_a():
        titulo('a', 'usuarios_activos nulo (suscripciones)',
               'dónde se concentran los nulos y qué % representan dentro de cada grupo.')
        if subs is None:
            emitir('No calculable: faltan columnas.')
            return
        flag = subs['usuarios_activos'].isna()
        emitir(f'Total nulos: {int(flag.sum())} de {len(subs)} ({flag.mean() * 100:.2f}%)')
        emitir('-- Por plan --')
        por_grupo(subs, flag, subs['nombre_plan'], 'plan')
        emitir('-- Por churn --')
        por_grupo(subs, flag, subs['churn'], 'churn')
        emitir('-- Por año de fecha_id --')
        por_grupo(subs, flag, subs['_f'].dt.year, 'año')
        emitir('-- Por mes (AAAA-MM) --')
        por_grupo(subs, flag, subs['_f'].dt.strftime('%Y-%m'), 'mes')
    seguro('a', bloque_a)

    # b) duracion_min nulo
    def bloque_b():
        titulo('b', 'duracion_min nulo (uso)',
               'dónde se concentran los nulos de duración por feature, año y cuenta (top 10).')
        if uso is None:
            emitir('No calculable: faltan columnas.')
            return
        flag = uso['duracion_min'].isna()
        emitir(f'Total nulos: {int(flag.sum())} de {len(uso)} ({flag.mean() * 100:.2f}%)')
        emitir('-- Por feature (valores tal como vienen, sin normalizar) --')
        por_grupo(uso, flag, uso['feature'].astype('string').str.strip(), 'feature', ordenar_por_nulos=True)
        emitir('-- Por año de fecha_id --')
        por_grupo(uso, flag, uso['_f'].dt.year, 'año')
        emitir('-- Top 10 cuentas con más nulos --')
        por_grupo(uso, flag, uso['cuenta_id'], 'cuenta_id', top=10, ordenar_por_nulos=True)
    seguro('b', bloque_b)

    # c) usuarios_activos == 0
    def bloque_c():
        titulo('c', 'usuarios_activos == 0',
               'filas con cero usuarios activos (valor real, no nulo), por plan y churn.')
        if subs is None:
            emitir('No calculable: faltan columnas.')
            return
        flag = pd.to_numeric(subs['usuarios_activos'], errors='coerce') == 0
        emitir(f'Total usuarios_activos == 0: {int(flag.sum())} de {len(subs)} ({flag.mean() * 100:.2f}%)')
        if flag.sum():
            emitir('-- Por plan (con % dentro del plan) --')
            por_grupo(subs, flag, subs['nombre_plan'], 'plan')
            emitir('-- Por churn (con % dentro del grupo) --')
            por_grupo(subs, flag, subs['churn'], 'churn')
    seguro('c', bloque_c)

    # d) unicidad
    def analizar_duplicados(df_raw, id_col, nombre):
        dup_extra = df_raw.duplicated(subset=[id_col], keep='first')
        identica = df_raw.duplicated(keep='first')  # idéntica en todas las columnas a una fila previa
        n_extra = int(dup_extra.sum())
        n_ident = int((dup_extra & identica).sum())
        n_distinta = n_extra - n_ident
        emitir(f'{nombre}: {id_col} duplicados (filas sobrantes): {n_extra}')
        emitir(f'  Filas totalmente idénticas a una previa: {n_ident}')
        emitir(f'  Mismo {id_col} pero valores distintos:   {n_distinta}')
        ids_dup = df_raw.loc[df_raw[id_col].duplicated(keep=False)]
        if len(ids_dup):
            conflicto = ids_dup.drop_duplicates().groupby(id_col).size()
            emitir(f'  {id_col} distintos con repetición: {ids_dup[id_col].nunique()} | '
                   f'con contenido en conflicto: {int((conflicto > 1).sum())}')

    def bloque_d():
        titulo('d', 'Unicidad',
               'parejas (cuenta_id, fecha_id) repetidas, cuenta_id repetidos y naturaleza de los IDs duplicados.')
        if subs is not None:
            pares = subs.duplicated(subset=['cuenta_id', 'fecha_id'], keep=False)
            emitir(f'fact_suscripciones deduplicada: filas en parejas (cuenta_id, fecha_id) repetidas: {int(pares.sum())} '
                   f'| parejas distintas repetidas: {int(subs.loc[pares, ["cuenta_id", "fecha_id"]].drop_duplicates().shape[0])}')
        else:
            emitir('No calculable: fact_suscripciones sin columnas necesarias.')
        if 'cuenta_id' in cuenta.columns:
            rep = cuenta['cuenta_id'].duplicated(keep=False)
            emitir(f'dim_cuenta: filas con cuenta_id repetido: {int(rep.sum())} '
                   f'(cuenta_id distintos repetidos: {int(cuenta.loc[rep, "cuenta_id"].nunique())})')
        else:
            emitir('No calculable: dim_cuenta.cuenta_id ausente.')
        if not f_subs:
            analizar_duplicados(subs_raw, 'sub_id', 'fact_suscripciones')
        if not f_uso:
            analizar_duplicados(uso_raw, 'uso_id', 'fact_uso')
    seguro('d', bloque_d)

    _guardar(args.salida)
    return 0


if __name__ == '__main__':
    sys.exit(main())
