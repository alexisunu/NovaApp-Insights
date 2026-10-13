# Hito 2 · El sistema muestra resultados

## Qué cambió desde el Hito 1
- Todas las pantallas exigen inicio de sesión (`/entrar/`) y responden según el rol del usuario.
- Pantallas nuevas: **Tablero**, **Explorar**, **Alertas**, **Métricas** y **Accesos**.
- Tabla nueva `registro_acceso` (migración `0004_registro_acceso`): quién entró, con qué rol y a qué pantalla.
- Comando nuevo `crear_usuarios_demo` para crear los grupos de roles y un usuario por rol.
- Los indicadores se calculan en `dashboard/metricas.py` en cada consulta; no se guardan.
- Las pantallas del Hito 1 (Carga, Calidad, Privacidad, Modelo) quedan solo para Dirección.

## Tabla de métricas (E8)
| Indicador | Fórmula | Qué mide | Qué decisión apoya | Tipo |
|---|---|---|---|---|
| MRR del mes | SUMA(mrr) en el último mes | Cuánto dinero entra cada mes por suscripciones | Si el negocio crece o se encoge | Base |
| Variación del MRR vs. mes anterior | (MRR mes − MRR mes anterior) / MRR mes anterior | Si este mes entró más o menos que el anterior | Detectar a tiempo una caída | **Contra periodo anterior** |
| Cuentas activas | CONTAR DISTINTOS(cuenta_id) con suscripción en el último mes | Cuántas empresas tienen el producto hoy | Dimensionar soporte y metas comerciales | Base |
| Cancelación mensual | SUMA(churn) / CONTAR(filas) de los últimos 12 meses | Qué parte de las cuentas se va cada mes | Priorizar retención por plan o zona | Base |
| Ingreso por cuenta de pago (ARPA) | SUMA(mrr) / CONTAR DISTINTOS(cuenta_id), último mes, **filtrando precio_mensual > 0** | Cuánto paga en promedio una cuenta que sí paga | Subir de plan a clientes o cambiar precios | **Filtro modificado** |
| Eventos de uso por cuenta activa | CONTAR(fact_uso del último mes) / Cuentas activas | Qué tanto usan el producto | Alertar cuentas que pagan y no usan | Combina los dos hechos |
| Cuentas sobre el límite de su plan | CONTAR DISTINTOS(cuenta_id con excede_limite) / Cuentas activas | Clientes con más usuarios de los permitidos | Candidatos para un plan superior | Base |

Definiciones: un mes activo es una fila en `fact_suscripciones`; una cancelación es `churn = verdadero`; un plan de pago tiene `precio_mensual > 0`.

## Tablero de tres niveles (E9)
1. **Arriba**: seis indicadores del último mes con datos.
2. **Medio**: tendencia mensual del MRR (o del uso, para Soporte), con un título que dice el hallazgo.
3. **Abajo**: detalle por plan, por zona (o por industria si ya se filtró una zona) y por función.

Segmentación: zona, plan y año, en la parte superior. El filtro de plan no aplica al uso porque `fact_uso` no tiene plan.

## Control de acceso (E10)
| Rol (grupo de Django) | Pantallas | Datos | Regla |
|---|---|---|---|
| Dirección | Todas | Todas las zonas | — |
| Líder · <zona> | Tablero, Explorar, Alertas, Métricas | Solo su zona | `cuenta.zona = zona del usuario`, aplicada en la consulta |
| Soporte | Tablero, Métricas | Solo uso | el MRR no se consulta |

Una pantalla no permitida responde **403** sin calcular nada; ocultar el enlace del menú no es la protección.
Un líder que cambie `?zona=` en la URL sigue viendo su zona: el servidor ignora ese parámetro para su rol.

### Qué NO protege
- El filtro vive en la aplicación: quien entre directo a PostgreSQL ve todas las filas.
- Lo que un usuario ve lo puede copiar o enviar; el rol no controla lo que pasa después.
- Los superusuarios y `/admin` ven todo.
- El servidor de desarrollo no usa HTTPS.
- Los usuarios de demostración comparten clave.
- Soporte no ve el MRR, pero sí cuántas cuentas hay y cuánto usan.

## Alertas (E11, adelantado del Hito 3)
- **Meses atípicos**: |z| > 2 sobre los **eventos por día** de cada mes. Contar eventos totales haría que febrero
  pareciera una caída cada año (tiene menos días): eso es estacionalidad de calendario, no una anomalía.
- **Tendencia**: dos o más meses seguidos de caída del MRR.
- **Coherencia**: si las cuentas se multiplican y el uso no, se pide revisar las fechas de `fact_uso`.
- **Cancelación por plan**: diferencias menores a 3 puntos se marcan como "vigilar", no como conclusión.

## Decisiones
- País aparece como **zona** porque el ETL lo generalizó para cumplir k ≥ 5 (Hito 1, E3).
- Roles con grupos de Django: no hacen falta tablas de permisos propias y se administran desde `/admin`.
- Gráficos dibujados en el servidor (SVG): funcionan sin internet durante la sustentación.
