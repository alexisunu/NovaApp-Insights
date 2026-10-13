from django.urls import path

from . import acceso, views, views_hito2

app_name = "dashboard"

urlpatterns = [
    path("", acceso.inicio, name="inicio"),
    path("entrar/", views_hito2.Entrar.as_view(), name="entrar"),
    path("salir/", views_hito2.Salir.as_view(), name="salir"),
    # Hito 1 · los datos
    path("carga/", views.carga, name="carga"),
    path("carga/ejecutar/", views.ejecutar_carga, name="ejecutar_carga"),
    path("carga/reiniciar/", views.reiniciar_base, name="reiniciar_base"),
    path("calidad/", views.calidad, name="calidad"),
    path("privacidad/", views.privacidad, name="privacidad"),
    path("modelo/", views.modelo, name="modelo"),
    # Hito 2 · lo funcional
    path("tablero/", views_hito2.tablero, name="tablero"),
    path("explorar/", views_hito2.explorar, name="explorar"),
    path("alertas/", views_hito2.alertas, name="alertas"),
    path("metricas/", views_hito2.metricas, name="metricas"),
    path("accesos/", views_hito2.accesos, name="accesos"),
]
