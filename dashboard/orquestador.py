import os
import time
from typing import Dict, Any

from etl_engine.extractor import extraer_datos
from etl_engine.cleaner import run_quality_pipeline
from etl_engine.privacy import aplicar_anonimato
from dashboard.loader import cargar

def ejecutar_pipeline(ruta_excel: str, ruta_csv: str):
    # 1. Validar variables de entorno y archivos
    nit_clave = os.environ.get('NIT_CLAVE')
    if not nit_clave or not nit_clave.strip():
        raise ValueError("Error: La variable de entorno NIT_CLAVE no está definida o está vacía.")
    
    if not os.path.exists(ruta_excel):
        raise FileNotFoundError(f"Error: El archivo Excel no existe en la ruta: {ruta_excel}")
    if not os.path.exists(ruta_csv):
        raise FileNotFoundError(f"Error: El archivo CSV no existe en la ruta: {ruta_csv}")
    
    tiempos = {}
    
    # 2. Extracción
    t0 = time.time()
    res_ext = extraer_datos(ruta_excel, ruta_csv)
    dfs_ext = res_ext['dataframes']
    metricas_extraccion = res_ext.get('metricas_extraccion', {})
    total_filas_hechos = metricas_extraccion.get('total_filas_hechos', 0)
    
    # Forzamos nombres consistentes
    df_subs = dfs_ext['fact_suscripciones']
    df_uso = dfs_ext['fact_uso']
    df_cuenta = dfs_ext['dim_cuenta']
    df_plan = dfs_ext['dim_plan']
    df_tiempo = dfs_ext['dim_tiempo']
    t1 = time.time()
    tiempos['extraccion'] = t1 - t0
    
    # 3. Limpieza
    t_clean0 = time.time()
    subs_limpio, uso_limpio, cuenta_limpia, df_rechazos, diagnostico = run_quality_pipeline(
        df_subs, df_uso, df_cuenta, df_plan, df_tiempo
    )
    t_clean1 = time.time()
    tiempos['limpieza'] = t_clean1 - t_clean0
    
    # 4. Privacidad
    t_priv0 = time.time()
    cuenta_segura, reporte_privacidad = aplicar_anonimato(cuenta_limpia)
    t_priv1 = time.time()
    tiempos['privacidad'] = t_priv1 - t_priv0
    
    # 5. Armar DataFrames
    dfs = {
        'plan': df_plan,
        'tiempo': df_tiempo,
        'cuenta': cuenta_segura,
        'suscripciones': subs_limpio,
        'uso': uso_limpio
    }
    
    # 6. Diagnóstico y resumen privacidad
    fila_priv = dict(reporte_privacidad.items())
    diagnostico_final = diagnostico + [fila_priv]
    
    # 7. Duplicadas eliminadas
    duplicadas_eliminadas = 0
    for diag in diagnostico:
        regla = diag.get('regla', '')
        if diag.get('dimension') == 'Unicidad' and ('sub_id repetido' in regla or 'uso_id repetido' in regla):
            duplicadas_eliminadas += int(diag.get('filas', 0))
            
    # 8. Llamar cargar
    t_carg0 = time.time()
    ejecucion = cargar(
        dfs=dfs,
        df_rechazos=df_rechazos,
        diagnostico=diagnostico_final,
        resumen_privacidad=fila_priv,
        duplicadas_eliminadas=duplicadas_eliminadas,
        archivo_excel=os.path.basename(ruta_excel),
        archivo_csv=os.path.basename(ruta_csv)
    )
    t_carg1 = time.time()
    tiempos['carga'] = t_carg1 - t_carg0
    
    # 9. Ajustar métricas
    filas_extraidas = total_filas_hechos
    filas_cuarentena = len(df_rechazos)
    filas_validas = filas_extraidas - duplicadas_eliminadas - filas_cuarentena
    
    ejecucion.filas_extraidas = filas_extraidas
    ejecucion.filas_duplicadas_eliminadas = duplicadas_eliminadas
    ejecucion.filas_cuarentena = filas_cuarentena
    ejecucion.filas_validas = filas_validas
    
    # 10. Medir tiempo total
    tiempos['total'] = time.time() - t0
    ejecucion.duracion_segundos = tiempos['total']
    
    ejecucion.save()
    
    return ejecucion, tiempos
