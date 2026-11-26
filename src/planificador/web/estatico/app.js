// Planificador de Ocupación: edición de las rejillas y confirmaciones.
"use strict";

(function () {
  const csrf = document.querySelector('meta[name="csrf"]')?.content || "";

  // Botones con data-confirmar: piden confirmación antes de enviar el formulario.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-confirmar]");
    if (b && !window.confirm(b.dataset.confirmar)) ev.preventDefault();
  });

  const caja = document.getElementById("rejilla");
  if (!caja) return;
  const estado = document.getElementById("estado");
  const editable = caja.dataset.editable === "1";

  function bloquear() {
    caja.querySelectorAll("input").forEach((i) => { i.readOnly = !editable; });
  }
  bloquear();

  function avisar(texto, error) {
    estado.textContent = texto || "";
    estado.classList.toggle("error", !!error);
    if (texto && !error) setTimeout(() => { if (estado.textContent === texto) estado.textContent = ""; }, 6000);
  }

  async function enviar(url, datos, foco) {
    const f = new FormData();
    f.append("vista", caja.dataset.vista);
    f.append("semana", caja.dataset.semana);
    f.append("tipo", caja.dataset.tipo);
    f.append("sala", caja.dataset.sala);
    for (const [k, v] of Object.entries(datos)) f.append(k, v ?? "");
    caja.classList.add("ocupada");
    try {
      const r = await fetch(url, { method: "POST", body: f, headers: { "X-CSRF": csrf }, credentials: "same-origin" });
      const j = await r.json().catch(() => ({ error: "Respuesta no válida del servidor" }));
      if (!r.ok || j.error && !j.html) { avisar(j.error || "No se ha podido guardar", true); return; }
      const scroll = caja.querySelector(".rejilla-scroll");
      const pos = scroll ? [scroll.scrollLeft, scroll.scrollTop] : null;
      caja.innerHTML = j.html;
      bloquear();
      const nuevo = caja.querySelector(".rejilla-scroll");
      if (nuevo && pos) { nuevo.scrollLeft = pos[0]; nuevo.scrollTop = pos[1]; }
      avisar(j.mensaje, j.error);
      if (foco) {
        const el = caja.querySelector(foco);
        if (el) el.focus();
      }
    } catch (e) {
      avisar("Sin conexión con el servidor: no se ha guardado", true);
    } finally {
      caja.classList.remove("ocupada");
    }
  }

  // Tras guardar, la rejilla se redibuja: el foco se recupera por posición (fila y columna).
  let focoSiguiente = null;

  function selectorDe(input, desplazamiento = 0) {
    const fila = [...caja.querySelectorAll("tbody tr")].indexOf(input.closest("tr")) + 1 + desplazamiento;
    const inv = input.dataset.inv;
    return inv ? `tbody tr:nth-child(${fila}) input.valor[data-inv="${CSS.escape(inv)}"]`
               : `tbody tr:nth-child(${fila}) input.tecnico`;
  }

  caja.addEventListener("change", (ev) => {
    if (!editable) return;
    const input = ev.target;
    const tr = input.closest("tr[data-clave]");
    if (!tr) return;
    const clave = JSON.parse(tr.dataset.clave);
    if (input.classList.contains("valor")) {
      const valor = input.value.trim().replace(/\./g, "") || "0";
      if (!/^\d+$/.test(valor)) { avisar("Escriba un número entero de bandejas", true); return; }
      const foco = focoSiguiente || selectorDe(input);
      focoSiguiente = null;
      enviar("/celda", { ...clave, invernadero: input.dataset.inv, valor }, foco);
    } else if (input.classList.contains("tecnico")) {
      enviar("/tecnico", { codigo: clave.codigo, fecha: input.value }, selectorDe(input));
    }
  });

  // Intro pasa a la celda de abajo (como en una hoja de cálculo).
  caja.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || !ev.target.classList.contains("valor")) return;
    ev.preventDefault();
    const input = ev.target;
    focoSiguiente = selectorDe(input, 1);
    input.blur();  // si el valor cambió, «change» guarda y usa focoSiguiente
    if (focoSiguiente) {  // no había cambios: se baja sin guardar
      caja.querySelector(focoSiguiente)?.focus();
      focoSiguiente = null;
    }
  });
})();
