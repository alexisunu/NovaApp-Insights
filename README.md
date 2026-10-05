# novaapp_insights

Proyecto de Inteligencia de Negocios para análisis, limpieza, anonimización y visualización de datos con Django.

Se requiere la variable de entorno NIT_CLAVE (utilizada por el módulo de privacidad/hashing), la cual debe mantenerse fija entre ejecuciones y nunca subirse al repositorio.


## Cómo ejecutar el proyecto

### 1. Preparar el entorno (Windows PowerShell)
Crea y activa tu entorno virtual, e instala las dependencias necesarias:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Base de Datos
Debes crear una base de datos PostgreSQL vacía en tu máquina (por defecto espera que se llame `novaapp`).

### 3. Variables de Entorno
Configura las credenciales de tu base de datos y la clave de hashing antes de ejecutar el proyecto. Reemplaza `tu-clave-aqui` con una contraseña consistente que mantendrás en tu máquina (¡nunca la subas al repositorio!).

```powershell
$env:DB_NAME="novaapp"
$env:DB_USER="postgres"
$env:DB_PASSWORD="tu-password-aqui"
$env:DB_HOST="localhost"
$env:DB_PORT="5432"
$env:NIT_CLAVE="tu-clave-aqui"
```

### 4. Inicializar la base de datos
Construye las tablas de Django y carga la dimensión de tiempo obligatoria:
```powershell
python manage.py migrate
python manage.py cargar_dim_tiempo
```

### 5. Pruebas Unitarias
Para correr los tests creados en el dashboard:
```powershell
python manage.py test dashboard
```

### 6. Verificaciones del ETL
Los archivos de datos originales no están en el repositorio por contener información personal, por lo que cada integrante debe tenerlos descargados localmente.

Para ejecutar los scripts de verificación de las capas de limpieza y privacidad del ETL, usa las rutas de tus propios archivos (cambia las rutas del ejemplo):

**Verificación del Cleaner:**
```powershell
$env:PYTHONPATH="."
python -m etl_engine.cleaner --excel "C:\ruta\al\PROYECTO_NovaApp.xlsx" --csv "C:\ruta\al\na_fact_uso.csv"
```

**Verificación de Privacidad (K-Anonimato y Hashing):**
```powershell
$env:PYTHONPATH="."
python etl_engine/verificar_privacy.py --excel "C:\ruta\al\PROYECTO_NovaApp.xlsx" --csv "C:\ruta\al\na_fact_uso.csv"
```

> **Nota:** El archivo Excel original y el archivo CSV se encuentran ignorados en Git (vía `.gitignore`). Es indispensable que cuentes con ellos en tu disco para poder ejecutar el ETL.