from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.carga, name="carga"),
    path("carga/ejecutar/", views.ejecutar_carga, name="ejecutar_carga"),
    path("carga/reiniciar/", views.reiniciar_base, name="reiniciar_base"),
    path("calidad/", views.calidad, name="calidad"),
    path("privacidad/", views.privacidad, name="privacidad"),
    path("modelo/", views.modelo, name="modelo"),
]
