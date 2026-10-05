"""Filtros de presentación del frontend: números con formato en español."""
from django import template

register = template.Library()


@register.filter
def miles(valor):
    """326962 -> '326.962'. Los vacíos se muestran como raya, nunca como cero."""
    if valor is None or valor == "":
        return "—"
    try:
        return f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return valor


@register.filter
def decimal(valor, cifras=1):
    """36.103 -> '36,1' (coma decimal)."""
    if valor is None or valor == "":
        return "—"
    try:
        texto = f"{float(valor):,.{int(cifras)}f}"
    except (TypeError, ValueError):
        return valor
    return texto.replace(",", "·").replace(".", ",").replace("·", ".")
