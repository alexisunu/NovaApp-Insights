import argparse
import os
import sys
import pandas as pd
from etl_engine.extractor import extraer_datos
from etl_engine.cleaner import run_quality_pipeline
from etl_engine.privacy import aplicar_anonimato

def verificar_privacy():
    parser = argparse.ArgumentParser()
    parser.add_argument('--excel', required=True)
    parser.add_argument('--csv', required=True)
    args = parser.parse_args()

    # Set NIT_CLAVE a un valor claro para pruebas
    os.environ['NIT_CLAVE'] = 'clave_secreta_solo_para_pruebas'

    # Extract
    resultado_extraccion = extraer_datos(args.excel, args.csv)
    dfs = resultado_extraccion['dataframes']
    df_subs = dfs['fact_suscripciones']
    df_uso = dfs['fact_uso']
    df_cuenta = dfs['dim_cuenta']
    df_plan = dfs['dim_plan']
    df_tiempo = dfs['dim_tiempo']

    # Clean
    _, _, df_cuenta_limpio, _, _ = run_quality_pipeline(df_subs, df_uso, df_cuenta, df_plan, df_tiempo)

    # Privacy run 1
    df_seguro_1, reporte = aplicar_anonimato(df_cuenta_limpio)

    # Privacy run 2
    df_seguro_2, _ = aplicar_anonimato(df_cuenta_limpio)

    resultados = []

    def agregar_resultado(chequeo, esperado, obtenido, condicion):
        resultados.append({
            'chequeo': chequeo,
            'esperado': esperado,
            'obtenido': obtenido,
            'resultado': 'Aprobado' if condicion else 'Fallido'
        })

    # 1. Filas 5000
    filas = len(df_seguro_1)
    agregar_resultado('Filas', 5000, filas, filas == 5000)

    # 2. Columnas en orden exacto (7 columnas con pais incluido)
    esperado_orden = ['cuenta_id', 'nit_hash', 'pais', 'zona', 'industria', 'canal_adquisicion', 'fecha_registro_inconsistente']
    obtenido_orden = list(df_seguro_1.columns)
    agregar_resultado('Columnas exactas y en orden', str(esperado_orden), str(obtenido_orden), esperado_orden == obtenido_orden)

    # 3. Sin columnas de datos personales (pais ya no es prohibida)
    pii = {'nombre_cuenta', 'contacto_admin', 'email_admin', 'nit', 'fecha_registro', 'plan_inicial_id'}
    interseccion = set(obtenido_orden).intersection(pii)
    agregar_resultado('Sin columnas PII', '0', str(len(interseccion)), len(interseccion) == 0)

    # 3b. Comprobar que pais tiene exactamente los 8 valores canónicos y suma 5000
    paises_canonicos = {'Argentina', 'Chile', 'Colombia', 'Ecuador', 'España', 'México', 'Perú', 'Uruguay'}
    paises_obtenidos = set(df_seguro_1['pais'].dropna().unique()) if 'pais' in df_seguro_1.columns else set()
    agregar_resultado('Pais tiene 8 valores canónicos', str(sorted(list(paises_canonicos))), str(sorted(list(paises_obtenidos))), paises_obtenidos == paises_canonicos)

    suma_cuentas_pais = int(df_seguro_1['pais'].value_counts().sum()) if 'pais' in df_seguro_1.columns else 0
    agregar_resultado('Suma cuentas por país == 5000', 5000, suma_cuentas_pais, suma_cuentas_pais == 5000)

    # 4. Valores textuales de industria y canal_adquisicion
    for col in ['industria', 'canal_adquisicion']:
        if col in df_cuenta_limpio.columns and col in df_seguro_1.columns:
            valores_entrada = set(df_cuenta_limpio[col].astype(str).str.strip())
            valores_salida = set(df_seguro_1[col])
            agregar_resultado(f'Valores textuales {col}', str(valores_entrada), str(valores_salida), valores_entrada == valores_salida)
        else:
            agregar_resultado(f'Valores textuales {col}', 'N/A', 'N/A', False)

    # 5. k_antes = 1
    k_antes = reporte['k_antes']
    agregar_resultado('k_antes', 1, k_antes, k_antes == 1)

    # 6. k_despues >= 5
    k_despues = reporte['k_despues']
    agregar_resultado('k_despues >= 5', '>= 5', k_despues, k_despues >= 5)

    # 7. cumple_k verdadero
    cumple_k = reporte['cumple_k']
    agregar_resultado('cumple_k', True, cumple_k, cumple_k is True)

    # 8. nit_hash de 64 caracteres hexadecimales
    if 'nit_hash' in df_seguro_1.columns:
        validos = df_seguro_1['nit_hash'].dropna().apply(lambda x: isinstance(x, str) and len(x) == 64 and all(c in '0123456789abcdefABCDEF' for c in x)).all()
        agregar_resultado('nit_hash 64 hex', True, bool(validos), bool(validos) == True)
    else:
        agregar_resultado('nit_hash 64 hex', True, 'No existe columna', False)

    # 9. nit_hash únicos igual a NIT únicos tras normalizar
    if 'nit' in df_cuenta_limpio.columns and 'nit_hash' in df_seguro_1.columns:
        nit_unicos = df_cuenta_limpio['nit'].astype(str).str.replace(r'\D', '', regex=True).replace('', pd.NA).dropna().nunique()
        hash_unicos = df_seguro_1['nit_hash'].dropna().nunique()
        agregar_resultado('Unicidad NIT == Hash', nit_unicos, hash_unicos, nit_unicos == hash_unicos)
    else:
        agregar_resultado('Unicidad NIT == Hash', 'N/A', 'N/A', False)

    # 10. Ejecutar dos veces da los mismos hashes
    if 'nit_hash' in df_seguro_1.columns and 'nit_hash' in df_seguro_2.columns:
        iguales = df_seguro_1['nit_hash'].equals(df_seguro_2['nit_hash'])
        agregar_resultado('Hashes estables', True, bool(iguales), bool(iguales) == True)
    else:
        agregar_resultado('Hashes estables', True, False, False)

    # 11. Quitar columna requerida lanza ValueError
    df_incompleto = df_cuenta_limpio.copy().drop(columns=['fecha_registro_inconsistente'])
    lanza_error_col = False
    try:
        aplicar_anonimato(df_incompleto)
    except ValueError:
        lanza_error_col = True
    except Exception:
        pass
    agregar_resultado('Falta col requerida -> ValueError', True, lanza_error_col, lanza_error_col is True)

    # 12. NIT_CLAVE vacía lanza ValueError
    os.environ['NIT_CLAVE'] = '   '
    lanza_error_clave = False
    try:
        aplicar_anonimato(df_cuenta_limpio)
    except ValueError:
        lanza_error_clave = True
    except Exception:
        pass
    agregar_resultado('NIT_CLAVE vacía -> ValueError', True, lanza_error_clave, lanza_error_clave is True)
    
    # Restore key just in case
    os.environ['NIT_CLAVE'] = 'clave_secreta_solo_para_pruebas'

    # Imprimir tabla
    print(f"{'Chequeo':<40} | {'Esperado':<40} | {'Obtenido':<40} | {'Resultado'}")
    print("-" * 140)
    aprobados = 0
    fallidos = 0
    for r in resultados:
        if r['resultado'] == 'Aprobado':
            aprobados += 1
        else:
            fallidos += 1
        
        esp = str(r['esperado'])[:37] + '...' if len(str(r['esperado'])) > 40 else str(r['esperado'])
        obt = str(r['obtenido'])[:37] + '...' if len(str(r['obtenido'])) > 40 else str(r['obtenido'])
        print(f"{r['chequeo']:<40} | {esp:<40} | {obt:<40} | {r['resultado']}")

    print("\n" + "="*40)
    print(f"Resumen: {aprobados} aprobados, {fallidos} fallidos")
    print("="*40)

    # Métricas adicionales al final de la ejecución
    print("\n" + "="*50)
    print("DISTRIBUCIÓN DE CUENTAS POR PAÍS")
    print("="*50)
    print(f"{'País':<20} | {'Cuentas':<15}")
    print("-" * 38)
    conteo_paises = df_seguro_1['pais'].value_counts().sort_index()
    for pais, cant in conteo_paises.items():
        print(f"{pais:<20} | {cant:<15}")
    print("-" * 38)
    print(f"{'Total':<20} | {int(conteo_paises.sum()):<15}")

    cuasi_finales = ['pais', 'industria', 'canal_adquisicion']
    from etl_engine.privacy import normalizar_texto
    df_temp_k = df_seguro_1.copy()
    for col in cuasi_finales:
        df_temp_k[col] = normalizar_texto(df_temp_k[col])
    grupos_k = df_temp_k.groupby(cuasi_finales, dropna=False).size()
    num_combinaciones = len(grupos_k)
    grupos_5_9 = int(((grupos_k >= 5) & (grupos_k <= 9)).sum())

    print("\n" + "="*50)
    print("MÉTRICAS DE PRIVACIDAD Y K-ANONIMATO (FINAL)")
    print("="*50)
    print(f"{'Métrica':<35} | {'Valor':<20}")
    print("-" * 58)
    print(f"{'k_despues':<35} | {reporte['k_despues']:<20}")
    print(f"{'Porcentaje de riesgo (%)':<35} | {reporte['pct_cuentas_riesgo_despues']:.2f}%")
    print(f"{'Número de combinaciones':<35} | {num_combinaciones:<20}")
    print(f"{'Grupos entre 5 y 9 cuentas':<35} | {grupos_5_9:<20}")
    print("="*58)

    if fallidos > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    verificar_privacy()

