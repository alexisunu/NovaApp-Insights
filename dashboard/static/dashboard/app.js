/* NovaApp Insights — interacción del Hito 1. Sin dependencias. */
(function () {
  "use strict";

  // ───────────── notas del curso (se recuerda la elección) ─────────────
  var casillaNotas = document.getElementById("ver-notas");
  function leer(clave) { try { return window.localStorage.getItem(clave); } catch (e) { return null; } }
  function guardar(clave, valor) { try { window.localStorage.setItem(clave, valor); } catch (e) { /* sin almacenamiento */ } }

  if (casillaNotas) {
    if (leer("novaapp.notas") === "no") {
      casillaNotas.checked = false;
      document.body.classList.add("sin-notas");
    }
    casillaNotas.addEventListener("change", function () {
      document.body.classList.toggle("sin-notas", !casillaNotas.checked);
      guardar("novaapp.notas", casillaNotas.checked ? "si" : "no");
    });
  }

  // ───────────── calidad: justificación por fila y reglas en cero ─────────────
  document.addEventListener("click", function (evento) {
    var fila = evento.target.closest ? evento.target.closest(".diagnostico .regla") : null;
    if (fila && !window.getSelection().toString()) alternar(fila);
  });
  document.addEventListener("keydown", function (evento) {
    if ((evento.key === "Enter" || evento.key === " ") && evento.target.classList &&
        evento.target.classList.contains("regla")) {
      evento.preventDefault();
      alternar(evento.target);
    }
  });
  function alternar(fila) {
    var abierta = fila.classList.toggle("abierta");
    fila.setAttribute("aria-expanded", abierta ? "true" : "false");
  }

  document.addEventListener("change", function (evento) {
    if (evento.target.id === "ver-ceros") {
      var tabla = document.getElementById("diagnostico");
      if (tabla) tabla.classList.toggle("con-ceros", evento.target.checked);
    }
  });

  // ───────────── carga: ejecutar y reiniciar ─────────────
  var ocupado = false;

  function token() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function enviar(url) {
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": token(), "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    }).then(function (respuesta) {
      return respuesta.json().catch(function () {
        return { ok: false, error: "El servidor respondió " + respuesta.status + " sin detalle." };
      });
    });
  }

  // Vuelve a pedir la página y reemplaza el contenido: todo lo que se ve sale de la base.
  function refrescar() {
    return fetch(window.location.href, { credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var nuevo = new DOMParser().parseFromString(html, "text/html");
        ["contenido"].forEach(function (id) {
          var actual = document.getElementById(id), fresco = nuevo.getElementById(id);
          if (actual && fresco) actual.innerHTML = fresco.innerHTML;
        });
        var estado = document.querySelector(".estado-carga"), estadoNuevo = nuevo.querySelector(".estado-carga");
        if (estado && estadoNuevo) estado.replaceWith(estadoNuevo);
      });
  }

  function botones(deshabilitar, textoEjecutar) {
    var ejecutar = document.getElementById("btn-ejecutar"), reiniciar = document.getElementById("btn-reiniciar");
    if (ejecutar) {
      ejecutar.disabled = deshabilitar;
      if (textoEjecutar) ejecutar.querySelector("span").textContent = textoEjecutar;
    }
    if (reiniciar) reiniciar.disabled = deshabilitar;
  }

  function escribir(consola, texto, clase) {
    var linea = document.createElement("span");
    if (clase) linea.className = clase;
    linea.textContent = texto;
    consola.appendChild(linea);
    consola.appendChild(document.createTextNode("\n"));
    consola.scrollTop = consola.scrollHeight;
    return linea;
  }

  function mostrarError(mensaje) {
    var consola = document.getElementById("consola");
    if (consola) escribir(consola, "✗ " + mensaje, "error");
  }

  function ejecutar() {
    var proceso = document.getElementById("proceso"), pasos = document.getElementById("pasos");
    var consola = document.getElementById("consola");
    if (!proceso || ocupado) return;
    ocupado = true;
    botones(true, "Ejecutando…");
    pasos.classList.remove("completando");
    pasos.classList.add("corriendo");

    var espera = consola.querySelector(".tenue");
    if (espera) { consola.textContent = ""; } else { consola.appendChild(document.createTextNode("\n")); }
    escribir(consola, "$ python manage.py ejecutar_etl", "orden");
    var reloj = escribir(consola, "ejecutando… 0 s", "tenue");
    var inicio = Date.now();
    var intervalo = window.setInterval(function () {
      reloj.textContent = "ejecutando… " + Math.round((Date.now() - inicio) / 1000) + " s";
    }, 1000);

    enviar(proceso.dataset.urlEjecutar)
      .then(function (datos) {
        window.clearInterval(intervalo);
        if (!datos.ok) throw new Error(datos.error || "La carga falló.");
        return refrescar().then(function () {
          var nuevos = document.getElementById("pasos");
          if (nuevos) nuevos.classList.add("completando");
          var c = document.getElementById("consola");
          if (c) c.scrollTop = c.scrollHeight;
        });
      })
      .catch(function (error) {
        window.clearInterval(intervalo);
        var p = document.getElementById("pasos");
        if (p) p.classList.remove("corriendo");
        reloj.remove();
        mostrarError(error.message);
      })
      .then(function () {
        ocupado = false;
        botones(false, "Ejecutar carga");
      });
  }

  function reiniciar() {
    var proceso = document.getElementById("proceso");
    if (!proceso || ocupado) return;
    if (!window.confirm("Se vacían los hechos, las cuentas, los planes, la cuarentena y el historial de ejecuciones.\n" +
                        "El calendario (dim_tiempo) se conserva. ¿Reiniciar la base?")) return;
    ocupado = true;
    botones(true);
    enviar(proceso.dataset.urlReiniciar)
      .then(function (datos) {
        if (!datos.ok) throw new Error(datos.error || "No se pudo reiniciar la base.");
        return refrescar();
      })
      .catch(function (error) { mostrarError(error.message); })
      .then(function () { ocupado = false; botones(false); });
  }

  // Delegación: el contenido se reemplaza después de cada acción.
  document.addEventListener("click", function (evento) {
    if (!evento.target.closest) return;
    if (evento.target.closest("#btn-ejecutar")) ejecutar();
    else if (evento.target.closest("#btn-reiniciar")) reiniciar();
  });

  var consolaInicial = document.getElementById("consola");
  if (consolaInicial) consolaInicial.scrollTop = consolaInicial.scrollHeight;
})();
