"""
Diagnóstico de solo lectura: fecha_registro (dim_cuenta) vs. suscripciones.

Uso:
    python diagnostico_fecha_registro.py RUTA_EXCEL RUTA_CSV [--salida diagnostico_fecha_registro.txt]

No modifica datos ni módulos. No imprime columnas con datos personales
(nombre_cuenta, nit, contacto_admin, email_admin).
"""

import argparse
import sys
from typing import List

import pandas as pd

from etl_engine.extractor import extraer_datos

LIMITE_MAX = pd.Timestamp('2025-12-31')
LIMITE_MIN = pd.Timestamp('2022-01-01')

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


def main() -> int:
    parser = argparse.ArgumentParser(description='Diagnóstico de fecha_registro (solo lectura).')
    parser.add_argument('ruta_excel', help='Ruta al Excel PROYECTO_NovaApp.xlsx')
    parser.add_argument('ruta_csv', help='Ruta al CSV de uso (fact_uso)')
    parser.add_argument('--salida', default='diagnostico_fecha_registro.txt',
                        help='Archivo de texto con los resultados')
    args = parser.parse_args()

    emitir('DIAGNÓSTICO DE FECHA_REGISTRO (solo lectura)')

    # 1. Carga con el extractor
    try:
        datos = extraer_datos(args.ruta_excel, args.ruta_csv)
        df_cuenta = datos['dataframes']['dim_cuenta']
        df_subs = datos['dataframes']['fact_suscripciones']
    except Exception as e:  # noqa: BLE001
        emitir(f'ERROR: no se pudieron cargar los datos: {e}')
        _guardar(args.salida)
        return 1

    emitir(f'dim_cuenta: {len(df_cuenta)} filas | fact_suscripciones (cruda): {len(df_subs)} filas')

    # Validación de columnas necesarias
    faltan = []
    for df, nombre, cols in [(df_cuenta, 'dim_cuenta', ['cuenta_id', 'fecha_registro']),
                             (df_subs, 'fact_suscripciones', ['sub_id', 'cuenta_id', 'fecha_id'])]:
        for c in cols:
            if c not in df.columns:
                faltan.append(f'{nombre}.{c}')
    if faltan:
        emitir(f'ERROR: columnas ausentes, no se puede calcular: {faltan}')
        _guardar(args.salida)
        return 1

    # 2. Preparación (solo columnas no personales; copias para no tocar originales)
    cuenta = df_cuenta[['cuenta_id', 'fecha_registro']].copy()
    subs = df_subs[['sub_id', 'cuenta_id', 'fecha_id']].drop_duplicates(
        subset=['sub_id'], keep='first').copy()
    emitir(f'fact_suscripciones deduplicada por sub_id: {len(subs)} filas '
           f'({len(df_subs) - len(subs)} duplicados descartados en memoria)')

    if cuenta['cuenta_id'].duplicated().any():
        emitir(f"AVISO: dim_cuenta tiene {int(cuenta['cuenta_id'].duplicated().sum())} "
               f"cuenta_id repetidos; los conteos son por fila de dim_cuenta.")

    tipo_reg = cuenta['fecha_registro'].dtype
    emitir(f'Tipo de fecha_registro: {tipo_reg} | Tipo de fecha_id: {subs["fecha_id"].dtype}')
    cuenta['_reg'] = pd.to_datetime(cuenta['fecha_registro'], errors='coerce')
    # fecha_id AAAAMMDD entero (se pasa a texto sin decimales para parseo estricto)
    fid_txt = subs['fecha_id'].astype('string').str.strip().str.replace(r'\.0+$', '', regex=True)
    subs['_fecha'] = pd.to_datetime(fid_txt, format='%Y%m%d', errors='coerce')
    fid_malas = int(subs['_fecha'].isna().sum())
    if fid_malas:
        emitir(f'AVISO: {fid_malas} fecha_id de suscripciones no se pudieron interpretar como AAAAMMDD '
               f'(se excluyen del cálculo de primera suscripción).')

    reg = cuenta['_reg']
    validas = reg.dropna()

    # a) rango y nulos
    titulo('a', 'Rango de fecha_registro y nulos',
           'fecha mínima/máxima de alta y cuentas con fecha nula o no interpretable.')
    try:
        if validas.empty:
            emitir('No hay fechas de registro interpretables; no se puede calcular el rango.')
        else:
            emitir(f'Fecha mínima: {validas.min().date()}')
            emitir(f'Fecha máxima: {validas.max().date()}')
        nulas_origen = int(cuenta['fecha_registro'].isna().sum())
        no_interp = int(reg.isna().sum()) - nulas_origen
        emitir(f'Cuentas con fecha_registro nula: {nulas_origen}')
        emitir(f'Cuentas con fecha_registro no interpretable (texto/valor inválido): {max(no_interp, 0)}')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (a): {e}')

    # b) fuera del rango de dim_tiempo
    titulo('b', 'Fuera del rango de dim_tiempo (2022-2025)',
           'cuentas registradas después del 2025-12-31 o antes del 2022-01-01.')
    try:
        emitir(f'fecha_registro > 2025-12-31: {int((reg > LIMITE_MAX).sum())}')
        emitir(f'fecha_registro < 2022-01-01: {int((reg < LIMITE_MIN).sum())}')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (b): {e}')

    # c) por año
    titulo('c', 'Cuentas por año de registro',
           'distribución anual de las altas de cuentas.')
    try:
        por_anio = validas.dt.year.value_counts().sort_index()
        for anio, n in por_anio.items():
            emitir(f'{int(anio)}: {int(n)}')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (c): {e}')

    # d) primera suscripción
    titulo('d', 'Registro posterior a la primera suscripción',
           'cuentas cuya fecha_registro es posterior a su primera suscripción (incoherencia temporal).')
    comp = None
    try:
        primera = (subs.dropna(subset=['_fecha']).groupby('cuenta_id')['_fecha']
                   .min().rename('primera_sub'))
        comp = cuenta.merge(primera, left_on='cuenta_id', right_index=True, how='left')
        con_sub = comp[comp['primera_sub'].notna()]
        sin_sub = int(comp['primera_sub'].isna().sum())
        posterior = con_sub[con_sub['_reg'] > con_sub['primera_sub']]
        n_con = len(con_sub)
        emitir(f'Cuentas con al menos una suscripción: {n_con}')
        emitir(f'Cuentas sin ninguna suscripción: {sin_sub}')
        emitir(f'Cuentas con fecha_registro POSTERIOR a su primera suscripción: {len(posterior)}')
        if n_con:
            emitir(f'Porcentaje sobre cuentas con suscripción: {len(posterior) / n_con * 100:.2f}%')
        else:
            emitir('Porcentaje no calculable: ninguna cuenta tiene suscripción.')
        emitir(f'Cuentas con fecha_registro no interpretable entre las que tienen suscripción '
               f'(excluidas de la comparación): {int(con_sub["_reg"].isna().sum())}')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (d): {e}')
        posterior = None

    # e) diferencia en días
    titulo('e', 'Diferencia en días (registro - primera suscripción)',
           'qué tan grande es el desfase en las cuentas con registro posterior a su primera suscripción.')
    try:
        if posterior is None:
            emitir('No calculable: depende del bloque (d).')
        elif posterior.empty:
            emitir('No hay cuentas con registro posterior a su primera suscripción.')
        else:
            dias = (posterior['_reg'] - posterior['primera_sub']).dt.days
            emitir(f'Mínimo: {int(dias.min())} días')
            emitir(f'Mediana: {float(dias.median()):.1f} días')
            emitir(f'Máximo: {int(dias.max())} días')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (e): {e}')

    # f) control
    titulo('f', 'Pregunta de control',
           'cuentas con fecha_registro > 2025-12-31 que, aun así, tienen suscripciones.')
    try:
        if comp is None:
            emitir('No calculable: depende del bloque (d).')
        else:
            n_f = int(((comp['_reg'] > LIMITE_MAX) & comp['primera_sub'].notna()).sum())
            emitir(f'Cuentas con fecha_registro > 2025-12-31 Y con suscripciones: {n_f}')
    except Exception as e:  # noqa: BLE001
        emitir(f'No se pudo calcular (f): {e}')

    _guardar(args.salida)
    return 0


def _guardar(ruta: str) -> None:
    try:
        with open(ruta, 'w', encoding='utf-8') as f:
            f.write('\n'.join(_lineas) + '\n')
        print(f'\nResultados guardados en: {ruta}')
    except OSError as e:
        print(f'No se pudo guardar el archivo de resultados: {e}')


if __name__ == '__main__':
    sys.exit(main())
