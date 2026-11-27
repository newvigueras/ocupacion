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

  // Repartir una partida entre las calles de un invernadero (ventana aparte).
  const dialogo = document.getElementById("calles");
  if (!dialogo || !editable) return;
  const contenido = dialogo.querySelector("[data-contenido]");
  const miles = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const numero = (input) => Number(input.value.trim().replace(/\./g, "")) || 0;
  let volverA = null;

  caja.addEventListener("click", async (ev) => {
    const boton = ev.target.closest("button.a-calles");
    if (!boton) return;
    const clave = JSON.parse(boton.closest("tr[data-clave]").dataset.clave);
    volverA = selectorDe(boton.parentElement.querySelector("input.valor"));
    const q = new URLSearchParams({ vista: caja.dataset.vista, codigo: clave.codigo, inv: boton.dataset.inv });
    try {
      const r = await fetch("/calles?" + q, { credentials: "same-origin" });
      const j = await r.json().catch(() => ({ error: "Respuesta no válida del servidor" }));
      if (!r.ok || !j.html) { avisar(j.error || "No se han podido leer las calles", true); return; }
      contenido.innerHTML = j.html;
      repasar();
      dialogo.showModal();
      contenido.querySelector("input.valor")?.focus();
    } catch (e) {
      avisar("Sin conexión con el servidor", true);
    }
  });

  function repasar() {
    const form = contenido.querySelector("[data-reparto]");
    if (!form) return;
    const aqui = Number(form.dataset.aqui), maximo = Number(form.dataset.maximo);
    let total = 0;
    const pasadas = [];
    form.querySelectorAll("tbody tr").forEach((tr) => {
      const input = tr.querySelector("input.valor");
      const v = numero(input);
      total += v;
      const libre = Number(tr.querySelector("[data-libre]").dataset.libre);
      const pasa = v > libre;
      input.classList.toggle("pasa", pasa);
      tr.classList.toggle("llena", v > 0);
      if (pasa) pasadas.push(input.name.slice(6));
    });
    form.querySelector("[data-en-calles]").textContent = miles(total);
    form.querySelector("[data-sin-calle]").textContent = miles(Math.max(0, aqui - total));
    const avisos = [];
    if (pasadas.length) avisos.push(`Pasa del hueco en la calle ${pasadas.join(", ")} (se puede, con aviso).`);
    if (total > maximo) avisos.push(`La partida solo puede tener ${miles(maximo)} bandejas aquí: se pondrán como mucho esas.`);
    const aviso = form.querySelector("[data-aviso]");
    aviso.textContent = avisos.join(" ");
    aviso.hidden = !avisos.length;
  }

  contenido.addEventListener("input", repasar);

  contenido.addEventListener("click", (ev) => {
    const form = contenido.querySelector("[data-reparto]");
    if (!form) return;
    if (ev.target.closest("[data-cerrar]")) { dialogo.close(); return; }
    const vaciar = ev.target.closest("[data-vaciar]");
    const llenar = ev.target.closest("[data-llenar]");
    if (!vaciar && !llenar) return;
    const filas = [...form.querySelectorAll("tbody tr")];
    filas.forEach((tr) => { tr.querySelector("input.valor").value = ""; });
    if (llenar) {
      const aqui = Number(form.dataset.aqui);
      let resto = aqui > 0 ? aqui : Number(form.dataset.maximo);
      for (const tr of filas) {
        if (resto <= 0) break;
        const pon = Math.min(resto, Number(tr.querySelector("[data-libre]").dataset.libre));
        if (pon > 0) { tr.querySelector("input.valor").value = pon; resto -= pon; }
      }
      repasar();
      if (resto > 0) {
        const aviso = form.querySelector("[data-aviso]");
        aviso.textContent = `No caben ${miles(resto)} bandejas en las calles con hueco: se quedan sin calle.`;
        aviso.hidden = false;
      }
    } else {
      repasar();
    }
  });

  contenido.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const datos = { codigo: form.dataset.codigo, invernadero: form.dataset.invernadero };
    for (const input of form.querySelectorAll("input.valor")) {
      const v = input.value.trim().replace(/\./g, "");
      if (v && !/^\d+$/.test(v)) { input.focus(); input.select(); return; }
      datos[input.name] = v;
    }
    dialogo.close();
    enviar("/calles", datos, volverA);
  });

  // «close» llega con retraso: si ya se ha vuelto a abrir con otra partida, no se borra.
  dialogo.addEventListener("close", () => { if (!dialogo.open) contenido.innerHTML = ""; });
})();

// Por invernadero: abrir o cerrar todas las tarjetas.
(function () {
  document.addEventListener("click", (ev) => {
    const abrir = ev.target.closest("[data-abrir-todos]");
    const cerrar = ev.target.closest("[data-cerrar-todos]");
    if (!abrir && !cerrar) return;
    document.querySelectorAll("details.inv-tarjeta").forEach((d) => { d.open = !!abrir; });
  });
})();

// Cifras con punto de miles y fechas dd/mm/aaaa, como en el resto de la aplicación.
const planMiles = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
const planFecha = (iso, dias) => {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + dias);
  return d;
};
const planDosCifras = (n) => String(n).padStart(2, "0");
const planFechaTexto = (d) => `${planDosCifras(d.getDate())}/${planDosCifras(d.getMonth() + 1)}/${d.getFullYear()}`;
const planIso = (d) => `${d.getFullYear()}-${planDosCifras(d.getMonth() + 1)}-${planDosCifras(d.getDate())}`;
const planClase = (pct) => (pct > 100 ? "mal" : pct >= 85 ? "aviso" : "bien");

// Por invernadero: al pasar el ratón por el gráfico, bandejas y % de ese día.
(function () {
  document.querySelectorAll("svg.grafico[data-valores]").forEach((svg) => {
    const valores = svg.dataset.valores.split(",").map(Number);
    const capacidad = Number(svg.dataset.capacidad);
    const caja = svg.parentElement;
    const nota = document.createElement("div");
    nota.className = "nota-grafico";
    nota.hidden = true;
    caja.appendChild(nota);
    const vb = svg.viewBox.baseVal;
    const izq = 12, util = vb.width - 24;
    const raya = document.createElementNS("http://www.w3.org/2000/svg", "line");
    raya.setAttribute("class", "raton");
    raya.setAttribute("y1", "12");
    raya.setAttribute("y2", String(vb.height - 22));
    svg.appendChild(raya);

    svg.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect();
      const x = (ev.clientX - r.left) * (vb.width / r.width);
      const i = Math.max(0, Math.min(valores.length - 1, Math.floor((x - izq) / util * valores.length)));
      const v = valores[i];
      const pct = capacidad ? ` (${Math.round(100 * v / capacidad)}%)` : "";
      nota.innerHTML = "";
      const b = document.createElement("b");
      b.textContent = planMiles(v);
      nota.append(b, ` bandejas${pct}`, document.createElement("br"),
                  planFechaTexto(planFecha(svg.dataset.inicio, i)));
      nota.hidden = false;
      const xr = izq + (i + 0.5) / valores.length * util;
      raya.setAttribute("x1", xr);
      raya.setAttribute("x2", xr);
      raya.style.display = "";
      const escala = r.width / vb.width;
      const izqNota = Math.max(4, Math.min(r.width - nota.offsetWidth - 4, xr * escala - nota.offsetWidth / 2));
      nota.style.left = izqNota + "px";
    });
    svg.addEventListener("mouseleave", () => { nota.hidden = true; raya.style.display = "none"; });
  });
})();

// Ocupación Hotel: mover el día con la barra, sin recargar la página.
(function () {
  const hotel = document.getElementById("hotel");
  if (!hotel) return;
  const inicio = hotel.dataset.inicio;
  const deslizador = hotel.querySelector("[data-deslizador]");
  const fecha = hotel.querySelector("[data-fecha]");
  const linea = hotel.querySelector(".dia-elegido");
  hotel.querySelectorAll("[data-sin-js]").forEach((b) => { b.hidden = true; });

  function poner(i) {
    const dia = planFecha(inicio, i);
    fecha.value = planIso(dia);
    deslizador.value = i;
    if (linea) {
      const x = Number(linea.dataset.izquierda) + (i + 0.5) * Number(linea.dataset.anchoDia);
      linea.setAttribute("x1", x);
      linea.setAttribute("x2", x);
    }
    hotel.querySelectorAll("[data-valores]").forEach((caja) => {
      const v = Number(caja.dataset.valores.split(",")[i] || 0);
      const cap = Number(caja.dataset.capacidad);
      const pct = cap ? 100 * v / cap : 0;
      const texto = caja.querySelector("[data-pct]");
      if (texto) {
        texto.textContent = Math.round(pct) + "%";
        texto.className = "pct-texto " + planClase(pct);
      }
      const barra = caja.querySelector("[data-barra]");
      if (barra) {
        barra.className = planClase(pct);
        barra.style.setProperty("--p", Math.min(pct, 100) + "%");
      }
      caja.querySelector("[data-cifras]").textContent = planMiles(v) + (cap ? " / " + planMiles(cap) : "");
      const enlace = caja.querySelector("a.chip");
      if (enlace) {
        const u = new URL(enlace.href);
        u.searchParams.set("fecha", fecha.value);
        enlace.href = u.pathname + u.search;
      }
    });
  }

  deslizador.addEventListener("input", () => poner(Number(deslizador.value)));
  fecha.addEventListener("change", () => {
    const dias = Math.round((new Date(fecha.value + "T12:00:00") - new Date(inicio + "T12:00:00")) / 864e5);
    if (dias >= 0 && dias <= Number(deslizador.max)) poner(dias);
  });
})();
