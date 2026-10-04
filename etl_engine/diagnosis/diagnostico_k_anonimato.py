import argparse
import sys
import pandas as pd
import numpy as np
import unicodedata
import os
from etl_engine.extractor import extraer_datos

def normalizar_texto(series):
    # Elimina espacios, convierte a minúsculas, elimina tildes
    if series.dtype == 'object' or series.dtype == 'string':
        return series.astype(str).str.strip().str.lower().apply(
            lambda x: ''.join((c for c in unicodedata.normalize('NFD', x) if unicodedata.category(c) != 'Mn')) if pd.notnull(x) else x
        )
    return series

def calcular_metricas_k(df, cuasi_id):
    # Agrupar por cuasi_id
    grupo = df.groupby(cuasi_id, dropna=False).size().reset_index(name='k')
    
    combinaciones_distintas = len(grupo)
    k_minimo = grupo['k'].min() if not grupo.empty else 0
    
    # Distribución del tamaño de grupo
    k_1 = (grupo['k'] == 1).sum()
    k_2 = (grupo['k'] == 2).sum()
    k_3_4 = ((grupo['k'] >= 3) & (grupo['k'] <= 4)).sum()
    k_5_9 = ((grupo['k'] >= 5) & (grupo['k'] <= 9)).sum()
    k_10_mas = (grupo['k'] >= 10).sum()
    
    distribucion_grupos = {
        'k=1': k_1,
        'k=2': k_2,
        'k=3-4': k_3_4,
        'k=5-9': k_5_9,
        'k=10+': k_10_mas
    }
    
    cuentas_k1 = grupo[grupo['k'] == 1]['k'].sum()
    cuentas_menor_5 = grupo[grupo['k'] < 5]['k'].sum()
    total_cuentas = len(df)
    porcentaje_menor_5 = (cuentas_menor_5 / total_cuentas * 100) if total_cuentas > 0 else 0
    
    return combinaciones_distintas, k_minimo, distribucion_grupos, cuentas_k1, porcentaje_menor_5, grupo

def main():
    parser = argparse.ArgumentParser(description="Diagnóstico de K-Anonimato en dim_cuenta")
    parser.add_argument('excel_path', type=str, help='Ruta al archivo Excel')
    parser.add_argument('csv_path', type=str, help='Ruta al archivo CSV')
    args = parser.parse_args()

    # Cargar datos
    print("Extrayendo datos...")
    resultado_extraccion = extraer_datos(args.excel_path, args.csv_path)
    df_cuenta = resultado_extraccion['dataframes']['dim_cuenta']

    output_lines = []
    output_lines.append("=== DIAGNÓSTICO DE K-ANONIMATO ===")
    
    # Normalizar en memoria
    df_cuenta_norm = df_cuenta.copy()
    for col in ['pais', 'industria', 'canal_adquisicion']:
        if col in df_cuenta_norm.columns:
            df_cuenta_norm[col] = normalizar_texto(df_cuenta_norm[col])

    # a) Valores distintos por cuasi-identificador
    output_lines.append("\na) Valores distintos y conteo por cuasi-identificador base:")
    cuasi_id_base = ['pais', 'industria', 'canal_adquisicion', 'plan_inicial_id']
    for col in cuasi_id_base:
        if col in df_cuenta_norm.columns:
            conteos = df_cuenta_norm[col].value_counts(dropna=False)
            output_lines.append(f"\n- {col} ({len(conteos)} valores distintos):")
            for val, count in conteos.items():
                output_lines.append(f"  {val}: {count}")

    # b) 4 cuasi-identificadores base
    output_lines.append("\nb) Con los 4 cuasi-identificadores base:")
    combinaciones, k_min, distribucion, cuentas_k1, pct_menor_5, _ = calcular_metricas_k(df_cuenta_norm, cuasi_id_base)
    output_lines.append(f"Combinaciones distintas: {combinaciones}")
    output_lines.append(f"K mínimo: {k_min}")
    output_lines.append(f"Distribución de grupos: {distribucion}")
    output_lines.append(f"Cuentas con k=1: {cuentas_k1}")
    output_lines.append(f"% de cuentas en grupos < 5: {pct_menor_5:.2f}%")

    # c) Pasos de generalización
    output_lines.append("\nc) Pasos de generalización cumulativos:")
    resultados_pasos = []
    
    # Paso 1: pais -> zona
    df_paso1 = df_cuenta_norm.copy()
    zonas = {
        'mexico': 'mexico',
        'colombia': 'andina', 'ecuador': 'andina', 'peru': 'andina',
        'argentina': 'cono sur', 'chile': 'cono sur', 'uruguay': 'cono sur',
        'espana': 'espana'
    }
    if 'pais' in df_paso1.columns:
        df_paso1['zona'] = df_paso1['pais'].map(lambda x: zonas.get(x, 'otros') if pd.notnull(x) else x)
    cuasi_paso1 = ['zona', 'industria', 'canal_adquisicion', 'plan_inicial_id']
    _, k_min1, _, k1_1, pct1, _ = calcular_metricas_k(df_paso1, cuasi_paso1)
    resultados_pasos.append(f"Paso 1 (zona) | K min: {k_min1} | Cuentas k=1: {k1_1} | % en < 5: {pct1:.2f}%")

    # Paso 2: industria y canal_adquisicion -> Otros (< 100 cuentas)
    df_paso2 = df_paso1.copy()
    for col in ['industria', 'canal_adquisicion']:
        if col in df_paso2.columns:
            conteos = df_paso2[col].value_counts()
            valid_cats = conteos[conteos >= 100].index
            df_paso2[col] = df_paso2[col].where(df_paso2[col].isin(valid_cats), 'otros')
    _, k_min2, _, k1_2, pct2, _ = calcular_metricas_k(df_paso2, cuasi_paso1)
    resultados_pasos.append(f"Paso 2 (industria/canal < 100 -> otros) | K min: {k_min2} | Cuentas k=1: {k1_2} | % en < 5: {pct2:.2f}%")

    # Paso 3: plan_inicial_id -> Free / De pago
    df_paso3 = df_paso2.copy()
    if 'plan_inicial_id' in df_paso3.columns:
        df_paso3['tipo_plan'] = df_paso3['plan_inicial_id'].apply(lambda x: 'free' if pd.notnull(x) and x == 1 else 'de pago')
    cuasi_paso3 = ['zona', 'industria', 'canal_adquisicion', 'tipo_plan']
    _, k_min3, _, k1_3, pct3, _ = calcular_metricas_k(df_paso3, cuasi_paso3)
    resultados_pasos.append(f"Paso 3 (plan -> tipo) | K min: {k_min3} | Cuentas k=1: {k1_3} | % en < 5: {pct3:.2f}%")

    # Paso 4: eliminar plan_inicial_id (tipo_plan)
    df_paso4 = df_paso3.copy()
    cuasi_paso4 = ['zona', 'industria', 'canal_adquisicion']
    _, k_min4, _, k1_4, pct4, grupo4 = calcular_metricas_k(df_paso4, cuasi_paso4)
    resultados_pasos.append(f"Paso 4 (sin plan) | K min: {k_min4} | Cuentas k=1: {k1_4} | % en < 5: {pct4:.2f}%")

    for res in resultados_pasos:
        output_lines.append(res)

    # d) Mejor resultado (Asumimos el Paso 4)
    output_lines.append("\nd) Detalle del mejor resultado (Paso 4):")
    cuentas_menor_5 = grupo4[grupo4['k'] < 5]['k'].sum()
    output_lines.append(f"Cuentas en grupos < 5: {cuentas_menor_5}")
    if cuentas_menor_5 > 0:
        grupos_menores = grupo4[grupo4['k'] < 5]
        output_lines.append("Combinaciones con grupos < 5:")
        for idx, row in grupos_menores.iterrows():
            comb = " | ".join([str(row[c]) for c in cuasi_paso4])
            output_lines.append(f"  {comb}: {row['k']} cuentas")

    # Guardar y mostrar
    output_text = "\n".join(output_lines)
    print(output_text)
    
    with open("diagnostico_k_anonimato.txt", "w", encoding="utf-8") as f:
        f.write(output_text)

if __name__ == '__main__':
    main()
