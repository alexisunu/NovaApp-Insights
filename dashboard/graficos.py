"""
Gráficos SVG dibujados en el servidor: no dependen de internet ni de librerías.
Cada marca lleva un <title> para el tooltip al pasar el mouse.
"""
import math
from html import escape

from django.utils.safestring import mark_safe

ANCHO, ALTO = 760, 230
IZQ, DER, ARR, ABA = 46, 14, 14, 28


def _paso_bonito(maximo, divisiones=4):
    if maximo <= 0:
        return 1
    bruto = maximo / divisiones
    potencia = 10 ** math.floor(math.log10(bruto))
    for m in (1, 2, 2.5, 5, 10):
        if bruto <= m * potencia:
            return m * potencia
    return 10 * potencia


def _corto(valor):
    """Etiqueta de eje compacta en español: 12500 -> '12,5 mil'."""
    a = abs(valor)
    if a >= 1e9:
        t, s = valor / 1e9, " mil M"
    elif a >= 1e6:
        t, s = valor / 1e6, " M"
    elif a >= 1e4:
        t, s = valor / 1e3, " mil"
    else:
        return f"{valor:,.0f}".replace(",", ".")
    texto = f"{t:,.1f}".replace(",", "·").replace(".", ",").replace("·", ".")
    return texto.removesuffix(",0") + s


def _ejes(maximo, n, etiquetas):
    paso = _paso_bonito(maximo)
    tope = paso * math.ceil(maximo / paso) if maximo > 0 else 1
    alto_util = ALTO - ARR - ABA
    partes = []
    for i in range(int(round(tope / paso)) + 1):
        v = i * paso
        y = ARR + alto_util * (1 - v / tope)
        partes.append(f'<line class="g-rejilla" x1="{IZQ}" x2="{ANCHO - DER}" y1="{y:.1f}" y2="{y:.1f}"/>')
        partes.append(f'<text class="g-eje" x="{IZQ - 8}" y="{y + 3.5:.1f}" text-anchor="end">{_corto(v)}</text>')
    ancho_util = ANCHO - IZQ - DER
    for i, etiqueta in etiquetas:
        x = IZQ + ancho_util * (i + 0.5) / n
        partes.append(f'<text class="g-eje" x="{x:.1f}" y="{ALTO - 8}" text-anchor="middle">{escape(etiqueta)}</text>')
    return tope, partes


def _etiquetas_por_anio(claves):
    """Marca el eje x solo en el primer mes de cada año: '2022', '2023'…"""
    vistos, salida = set(), []
    for i, clave in enumerate(claves):
        anio = str(clave)[:4]
        if anio not in vistos:
            vistos.add(anio)
            salida.append((i, anio))
    return salida


def linea(puntos, formato, nombre="serie"):
    """puntos: [(clave AAAAMM…, etiqueta, valor)]. Marca el máximo y el último."""
    if not puntos:
        return ""
    n = len(puntos)
    valores = [p[2] for p in puntos]
    tope, partes = _ejes(max(valores), n, _etiquetas_por_anio([p[0] for p in puntos]))
    ancho_util, alto_util = ANCHO - IZQ - DER, ALTO - ARR - ABA
    xy = [(IZQ + ancho_util * (i + 0.5) / n, ARR + alto_util * (1 - v / tope)) for i, v in enumerate(valores)]
    trazo = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(xy))
    base = ARR + alto_util
    partes.append(f'<path class="g-area" d="{trazo} L{xy[-1][0]:.1f} {base} L{xy[0][0]:.1f} {base} Z"/>')
    partes.append(f'<path class="g-linea" d="{trazo}"/>')
    i_max = max(range(n), key=lambda i: valores[i])
    for i, clase in ((i_max, "g-punto-max"), (n - 1, "g-punto-ult")):
        partes.append(f'<circle class="{clase}" cx="{xy[i][0]:.1f}" cy="{xy[i][1]:.1f}" r="4.5"/>')
    paso = ancho_util / n
    for (x, _), (_, etiqueta, v) in zip(xy, puntos):
        partes.append(f'<rect class="g-zona" x="{x - paso / 2:.1f}" y="{ARR}" width="{paso:.1f}" height="{alto_util}">'
                      f'<title>{escape(etiqueta)}: {escape(formato(v))}</title></rect>')
    return mark_safe(f'<svg class="grafico" viewBox="0 0 {ANCHO} {ALTO}" role="img" '
                     f'aria-label="{escape(nombre)} por mes">{"".join(partes)}</svg>')


def columnas(puntos, formato, destacados=(), nombre="serie"):
    """puntos: [(clave, etiqueta, valor)]; las claves en 'destacados' se pintan como alerta."""
    if not puntos:
        return ""
    n = len(puntos)
    valores = [p[2] for p in puntos]
    tope, partes = _ejes(max(valores), n, _etiquetas_por_anio([p[0] for p in puntos]))
    ancho_util, alto_util = ANCHO - IZQ - DER, ALTO - ARR - ABA
    paso = ancho_util / n
    ancho_barra = max(2.0, paso - 2)  # 2 px de separación entre barras
    for i, (clave, etiqueta, v) in enumerate(puntos):
        alto = alto_util * v / tope
        x = IZQ + paso * i + (paso - ancho_barra) / 2
        clase = "g-barra alerta" if clave in destacados else "g-barra"
        partes.append(f'<rect class="{clase}" x="{x:.1f}" y="{ARR + alto_util - alto:.1f}" width="{ancho_barra:.1f}" '
                      f'height="{max(alto, 1):.1f}" rx="2"><title>{escape(etiqueta)}: {escape(formato(v))}'
                      f'{" · fuera de lo normal" if clave in destacados else ""}</title></rect>')
    return mark_safe(f'<svg class="grafico" viewBox="0 0 {ANCHO} {ALTO}" role="img" '
                     f'aria-label="{escape(nombre)} por mes">{"".join(partes)}</svg>')
