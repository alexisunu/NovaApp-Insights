"""
Diagnóstico de solo lectura: mrr, vacíos y churn (NovaApp).

Uso:
    python diagnostico_mrr_vacios.py RUTA_EXCEL RUTA_CSV [--salida diagnostico_mrr_vacios.txt]

No modifica datos ni módulos. No imprime columnas con datos personales.
"""

import argparse
import sys
from typing import List

import numpy as np
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


def tabla(obj) -> None:
    emitir(obj.to_string())


def _guardar(ruta: str) -> None:
    try:
        with open(ruta, 'w', encoding='utf-8') as f:
            f.write('\n'.join(_lineas) + '\n')
        print(f'\nResultados guardados en: {ruta}')
    except OSError as e:
        print(f'No se pudo guardar el archivo de resultados: {e}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Diagnóstico de mrr, vacíos y churn (solo lectura).')
    parser.add_argument('ruta_excel')
    parser.add_argument('ruta_csv')
    parser.add_argument('--salida', default='diagnostico_mrr_vacios.txt')
    args = parser.parse_args()

    emitir('DIAGNÓSTICO DE MRR, VACÍOS Y CHURN (solo lectura)')

    try:
        dfs = extraer_datos(args.ruta_excel, args.ruta_csv)['dataframes']
    except Exception as e:  # noqa: BLE001
        emitir(f'ERROR: no se pudieron cargar los datos: {e}')
        _guardar(args.salida)
        return 1

    df_subs_raw = dfs['fact_suscripciones']
    df_plan = dfs['dim_plan']

    req = [(df_subs_raw, 'fact_suscripciones', ['sub_id', 'plan_id', 'mrr', 'churn']),
           (df_plan, 'dim_plan', ['plan_id', 'nombre_plan', 'precio_mensual'])]
    faltan = [f'{n}.{c}' for df, n, cols in req for c in cols if c not in df.columns]

    subs = None
    if faltan:
        emitir(f'AVISO: columnas ausentes; los bloques a-e y g no se pueden calcular: {faltan}')
    else:
        subs = df_subs_raw.drop_duplicates(subset=['sub_id'], keep='first').copy()
        emitir(f'fact_suscripciones deduplicada por sub_id: {len(subs)} filas '
               f'({len(df_subs_raw) - len(subs)} duplicados descartados en memoria)')
        plan = df_plan[['plan_id', 'nombre_plan', 'precio_mensual']].drop_duplicates('plan_id')
        subs = subs.merge(plan, on='plan_id', how='left')
        sin_plan = int(subs['nombre_plan'].isna().sum())
        if sin_plan:
            emitir(f'AVISO: {sin_plan} suscripciones con plan_id sin match en dim_plan '
                   f'(se agrupan como "SIN PLAN").')
        subs['nombre_plan'] = subs['nombre_plan'].fillna('SIN PLAN')
        subs['_mrr'] = pd.to_numeric(subs['mrr'], errors='coerce')
        mrr_no_num = int((subs['_mrr'].isna() & subs['mrr'].notna()).sum())
        if mrr_no_num:
            emitir(f'AVISO: {mrr_no_num} valores de mrr no numéricos; se tratan como nulos.')
        subs['_precio'] = pd.to_numeric(subs['precio_mensual'], errors='coerce')
        subs['_free'] = subs['nombre_plan'].astype(str).str.lower().str.contains('free')
        emitir(f'Planes en dim_plan: {plan["nombre_plan"].tolist()} | '
               f'plan(es) detectado(s) como Free: {plan.loc[plan["nombre_plan"].astype(str).str.lower().str.contains("free"), "nombre_plan"].tolist()}')

    def seguro(letra, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            emitir(f'No se pudo calcular ({letra}): {e}')

    def requiere_subs(letra):
        if subs is None:
            emitir(f'No calculable ({letra}): faltan columnas necesarias.')
            return False
        return True

    # a) mrr < 0
    def bloque_a():
        titulo('a', 'mrr negativo', 'cuántos mrr < 0 y cuántos coinciden en valor absoluto con el precio del plan.')
        if not requiere_subs('a'):
            return
        neg = subs[subs['_mrr'] < 0].copy()
        neg['coincide'] = np.isclose(neg['_mrr'].abs(), neg['_precio'], equal_nan=False)
        emitir(f'Total mrr < 0: {len(neg)}')
        emitir(f'Con |mrr| == precio_mensual de su plan: {int(neg["coincide"].sum())}')
        emitir(f'Con |mrr| distinto del precio: {int((~neg["coincide"]).sum())}')
        if len(neg):
            emitir('Desglose por plan:')
            g = neg.groupby('nombre_plan').agg(total=('sub_id', 'size'), coincide_precio=('coincide', 'sum'))
            tabla(g)
    seguro('a', bloque_a)

    # b) mrr == 99999
    def bloque_b():
        titulo('b', 'mrr == 99999', 'cuántas filas tienen el valor centinela 99999 y en qué planes.')
        if not requiere_subs('b'):
            return
        m = subs[np.isclose(subs['_mrr'].fillna(0), 99999)]
        emitir(f'Total mrr == 99999: {len(m)}')
        if len(m):
            emitir('Desglose por plan:')
            tabla(m.groupby('nombre_plan').size().rename('filas'))
    seguro('b', bloque_b)

    # c) normales
    def bloque_c():
        titulo('c', 'Filas normales de planes de pago',
               'mrr > 0, distinto de 99999 y plan no Free: cumplimiento del precio de catálogo.')
        if not requiere_subs('c'):
            return
        n = subs[(subs['_mrr'] > 0) & ~np.isclose(subs['_mrr'].fillna(0), 99999) & ~subs['_free']].copy()
        emitir(f'Filas normales: {len(n)}')
        if n.empty:
            emitir('No hay filas normales; no se puede calcular el porcentaje.')
            return
        n['cumple'] = np.isclose(n['_mrr'], n['_precio'])
        emitir(f'Cumplen mrr == precio_mensual: {int(n["cumple"].sum())} '
               f'({n["cumple"].mean() * 100:.2f}%)')
        emitir('Los 10 valores de mrr más frecuentes por plan:')
        for nombre, g in n.groupby('nombre_plan'):
            precio = g['_precio'].iloc[0]
            emitir(f'  Plan {nombre} (precio catálogo: {precio}):')
            top = g['_mrr'].value_counts().head(10)
            for v, c in top.items():
                emitir(f'    mrr={v}: {int(c)}')
        no = n[~n['cumple']].copy()
        emitir(f'Filas que NO cumplen: {len(no)}')
        if len(no):
            no['dif'] = no['_mrr'] - no['_precio']
            emitir('Distribución de la diferencia (mrr - precio de catálogo):')
            tabla(no['dif'].describe())
            emitir('Diferencias más frecuentes (valor: conteo):')
            tabla(no['dif'].round(2).value_counts().head(10))
    seguro('c', bloque_c)

    # d) 0 y nulos por plan
    def bloque_d():
        titulo('d', 'mrr == 0 y mrr nulo por plan',
               'filas con mrr cero o vacío, separando Free de planes de pago.')
        if not requiere_subs('d'):
            return
        subs['_cero'] = subs['_mrr'] == 0
        subs['_nulo'] = subs['_mrr'].isna()
        g = subs.groupby(['_free', 'nombre_plan']).agg(
            mrr_cero=('_cero', 'sum'), mrr_nulo=('_nulo', 'sum'), filas=('sub_id', 'size'))
        g.index = g.index.set_levels(['Pago', 'Free'], level=0)
        tabla(g)
        pago = subs[~subs['_free']]
        emitir(f'Total planes de pago -> mrr==0: {int(pago["_cero"].sum())}, mrr nulo: {int(pago["_nulo"].sum())}')
    seguro('d', bloque_d)

    # e) Free
    def bloque_e():
        titulo('e', 'Plan Free', 'dentro de Free, cuántas filas tienen mrr 0 y cuántas mrr nulo.')
        if not requiere_subs('e'):
            return
        f = subs[subs['_free']]
        if f.empty:
            emitir('No se encontró ningún plan Free en los datos.')
            return
        emitir(f'Filas Free: {len(f)}')
        emitir(f'mrr == 0: {int((f["_mrr"] == 0).sum())}')
        emitir(f'mrr nulo: {int(f["_mrr"].isna().sum())}')
        emitir(f'mrr con otro valor: {int((f["_mrr"].notna() & (f["_mrr"] != 0)).sum())}')
    seguro('e', bloque_e)

    # f) nulos por columna
    def bloque_f():
        titulo('f', 'Nulos por columna en las cinco tablas',
               'conteo y porcentaje de valores nulos por columna, de mayor a menor (solo conteos).')
        for nombre in ['fact_suscripciones', 'fact_uso', 'dim_cuenta', 'dim_plan', 'dim_tiempo']:
            df = dfs.get(nombre)
            emitir(f'-- {nombre} ({0 if df is None else len(df)} filas) --')
            if df is None:
                emitir('   Tabla ausente; no se puede calcular.')
                continue
            if nombre == 'fact_suscripciones':
                emitir('   (sobre la tabla cruda, sin deduplicar)')
            n = df.isna().sum()
            res = pd.DataFrame({'nulos': n, 'porcentaje': (n / max(len(df), 1) * 100).round(2)})
            tabla(res.sort_values(['nulos'], ascending=False))
    seguro('f', bloque_f)

    # g) churn
    def bloque_g():
        titulo('g', 'Valores de churn y cruce con plan',
               'valores distintos de churn con su conteo y su distribución por plan.')
        if not requiere_subs('g'):
            return
        tabla(subs['churn'].value_counts(dropna=False).rename('filas'))
        emitir('Cruce churn vs plan (filas):')
        tabla(pd.crosstab(subs['nombre_plan'], subs['churn'].fillna('NULO')))
    seguro('g', bloque_g)

    _guardar(args.salida)
    return 0


if __name__ == '__main__':
    sys.exit(main())
