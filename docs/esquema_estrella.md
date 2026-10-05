# Esquema en estrella: NovaApp Insights

## Granularidad
- **fact_suscripciones:** una fila por cuenta y por mes. La pareja (cuenta_id, fecha_id) es única y todas las fechas son día 1 de cada mes.
- **fact_uso:** una fila por cada evento de uso registrado (uso_id).

```mermaid
erDiagram
    
    DIM_PLAN ||--o{ FACT_SUSCRIPCIONES : "plan"
    DIM_CUENTA ||--o{ FACT_SUSCRIPCIONES : "cuenta"
    DIM_TIEMPO ||--o{ FACT_SUSCRIPCIONES : "fecha"
    DIM_CUENTA ||--o{ FACT_USO : "cuenta"
    DIM_TIEMPO ||--o{ FACT_USO : "fecha"

    DIM_PLAN {
        
        string nombre_plan
        decimal precio_mensual
        int limite_usuarios
    }
    DIM_TIEMPO {
        int fecha_id PK
        date fecha
        int anio
        int mes
        string trimestre
        bool es_finde
    }
    DIM_CUENTA {
        int cuenta_id PK
        string nit_hash
        string zona
        string industria
        string canal_adquisicion
        int plan_inicial_id FK
        bool fecha_registro_inconsistente
    }
    FACT_SUSCRIPCIONES {
        int sub_id PK
        int cuenta_id FK
        int fecha_id FK
        int plan_id FK
        decimal mrr
        decimal mrr_original
        int usuarios_activos
        bool churn
    }
    FACT_USO {
        int uso_id PK
        int cuenta_id FK
        int fecha_id FK
        string feature
        decimal duracion_min
    }
```

## Tablas de control (fuera de la estrella, sin FKs a las dimensiones)
- **stg_rechazos:** cuarentena de registros con llaves huérfanas o fechas fuera de calendario. Llave única (tabla_origen, id_origen).
- **ejecucion_etl:** historial de cargas con sus contadores.
- **bitacora_cambios:** auditoría de decisiones (qué se corrigió, qué no y por qué), ligada a ejecucion_etl.