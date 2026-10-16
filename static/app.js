const $ = (id) => document.getElementById(id);
const COLOR = { alta: "#b42318", media: "#d98a1c", baja: "#1f7a4d" };
const SLIDERS = ["w_calor", "w_contam", "w_sombra", "max_por_calle"];
const NUMEROS = { n: [100, 1, 1000], presupuesto: [50000, 1000, 50000000], coste: [400, 50, 10000] }; // [defecto, mín, máx]
// Lee un campo numérico: si está vacío o no es válido usa el valor por defecto, y lo ajusta a [mín, máx]
const valor = (id) => {
  const [def, min, max] = NUMEROS[id];
  const v = Math.round(Number($(id).value));
  return Number.isFinite(v) && $(id).value !== "" ? Math.max(min, Math.min(max, v)) : def;
};
const nivel = (p) => (p >= 0.7 ? "alta" : p >= 0.5 ? "media" : "baja");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const eur = (v) => Number(v).toLocaleString("es-ES") + " €";
const modo = () => document.querySelector("input[name=modo]:checked").value;

function cargar(tag, attrs) {
  return new Promise((ok, fallo) => {
    const el = document.createElement(tag);
    Object.assign(el, attrs);
    el.onload = ok; el.onerror = fallo;
    document.head.appendChild(el);
  });
}
async function cargarLeaflet() {
  for (const base of ["vendor/leaflet/", "https://unpkg.com/leaflet@1.9.4/dist/"]) {
    try {
      await cargar("link", { rel: "stylesheet", href: base + "leaflet.css" });
      await cargar("script", { src: base + "leaflet.js" });
      return;
    } catch (e) { /* siguiente fuente */ }
  }
  throw new Error("sin Leaflet");
}

let mapa, capaSel, centrado = false, temporizador;

function consulta() {
  const p = ["w_calor", "w_contam", "w_sombra", "max_por_calle"].map((k) => `${k}=${$(k).value}`);
  if (modo() === "presupuesto") p.push(`presupuesto=${valor("presupuesto")}`, `coste=${valor("coste")}`);
  else p.push(`n=${valor("n")}`);
  if ($("barrio").value) p.push(`barrio=${encodeURIComponent($("barrio").value)}`);
  return p.join("&");
}

async function cargarBarrios() {
  try {
    const lista = await fetch("/api/barrios").then((r) => r.json());
    lista.forEach((b) => {
      const o = document.createElement("option");
      o.value = b.barrio;
      o.textContent = `${b.barrio} (${b.posiciones})`;
      $("barrio").appendChild(o);
    });
  } catch (e) { /* el filtro es opcional */ }
}

async function iniciar() {
  try { await cargarLeaflet(); } catch (e) { $("estado").textContent = "Mapa no disponible sin conexión (ver README). El resto funciona."; }
  if (window.L) {
    mapa = L.map("mapa").setView([37.3891, -5.9845], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(mapa);
    capaSel = L.layerGroup().addTo(mapa);
    const gAire = L.layerGroup().addTo(mapa), gTemp = L.layerGroup().addTo(mapa);
    try {
      const est = await fetch("/api/estaciones").then((r) => r.json());
      est.forEach((e) => L.circleMarker([+e.lat, +e.lon], { radius: 7, color: "#17261c", fillColor: "#fff", fillOpacity: 1, weight: 3 })
        .bindTooltip(`<b>${esc(e.nombre)}</b><br>NO₂ ${e.no2} · PM2,5 ${e.pm25} µg/m³`).addTo(gAire));
    } catch (e) { /* estaciones opcionales */ }
    try {
      const temps = await fetch("/api/temperaturas").then((r) => r.json());
      temps.forEach((f) => {
        const [lon, lat] = f.geometry.coordinates, p = f.properties;
        L.circleMarker([lat, lon], { radius: 6, color: "#7a3b00", fillColor: "#f59e0b", fillOpacity: 0.9, weight: 2 })
          .bindTooltip(`<b>${esc(p.distrito)}</b><br>Máx. media verano ${p.temp_max_media_verano} °C (+${p.anomalia_isla_calor} °C)<br><i>Estimación generada con IA</i>`).addTo(gTemp);
      });
    } catch (e) { /* temperaturas opcionales */ }
    L.control.layers(null, { "Estaciones de aire": gAire, "Temperatura de verano (estimada)": gTemp }, { collapsed: false }).addTo(mapa);
    const leyenda = L.control({ position: "bottomright" });
    leyenda.onAdd = () => {
      const d = L.DomUtil.create("div", "leyenda");
      d.innerHTML = `<b>Cada círculo es un árbol propuesto</b>
        <div><i style="background:${COLOR.alta};border-color:${COLOR.alta}"></i>Plantar ya (prioridad ≥ 0,70)</div>
        <div><i style="background:${COLOR.media};border-color:${COLOR.media}"></i>Siguiente fase (0,50 a 0,70)</div>
        <div><i style="background:${COLOR.baja};border-color:${COLOR.baja}"></i>Menor prioridad (&lt; 0,50)</div>
        <hr>
        <div><i style="background:#f59e0b;border-color:#7a3b00"></i>Temperatura de verano (estimada)</div>
        <div><i style="background:#fff;border-color:#17261c"></i>Estación de calidad del aire</div>
        <small>Prioridad: calor, contaminación y falta de sombra.</small>`;
      return d;
    };
    leyenda.addTo(mapa);
  }
  await cargarBarrios();

  const diferir = () => { clearTimeout(temporizador); temporizador = setTimeout(simular, 120); etiquetas(); };
  SLIDERS.forEach((k) => $(k).addEventListener("input", diferir));
  Object.keys(NUMEROS).forEach((k) => {
    $(k).addEventListener("input", diferir);
    $(k).addEventListener("change", () => { $(k).value = valor(k); simular(); });   // al salir del campo, lo corrige
  });
  $("barrio").addEventListener("change", () => { centrado = false; simular(); });
  document.querySelectorAll("input[name=modo]").forEach((r) => r.addEventListener("change", () => {
    $("bloque-arboles").hidden = modo() !== "arboles";
    $("bloque-presupuesto").hidden = modo() !== "presupuesto";
    simular();
  }));
  document.querySelectorAll(".presets button").forEach((b) => b.addEventListener("click", () => {
    ["w_calor", "w_contam", "w_sombra"].forEach((k) => ($(k).value = b.dataset[k]));
    simular();
  }));
  await simular();
}

function etiquetas() {
  ["w_calor", "w_contam", "w_sombra"].forEach((k) => ($("o-" + k).textContent = $(k).value + " %"));
  $("o-max_por_calle").textContent = $("max_por_calle").value;
  $("o-presupuesto").textContent = `≈ ${Math.floor(valor("presupuesto") / valor("coste")).toLocaleString("es-ES")} árboles a ${eur(valor("coste"))} por árbol`;
  $("exportar").href = "/api/exportar.csv?" + consulta();
}

async function simular() {
  etiquetas();
  try {
    const r = await fetch("/api/simular?" + consulta()).then((x) => { if (!x.ok) throw 0; return x.json(); });
    pintar(r);
  } catch (e) {
    $("estado").textContent = "No hay datos. Ejecuta: python3 server.py (y, si falta data/candidatos.json, python3 tools/preparar_datos.py)";
  }
}

function pintar(r) {
  const k = r.kpis;
  $("k-arboles").textContent = k.arboles;
  $("k-calles").textContent = k.calles;
  $("k-sombra").textContent = k.sombra_m2.toLocaleString("es-ES");
  $("k-co2").textContent = k.co2_kg_ano.toLocaleString("es-ES");
  $("k-coste").textContent = `Coste estimado: ${eur(k.coste_total_eur)} (${eur(k.coste_arbol_eur)} por árbol, supuesto).`;
  $("k-azar").textContent = k.arboles ? `Prioridad media un ${k.mejora_vs_azar_pct} % mayor que elegir posiciones viables al azar${$("barrio").value ? " en este barrio" : ""}.` : "";
  const aviso = $("k-aviso");
  if (k.arboles < k.arboles_pedidos) {
    aviso.hidden = false;
    aviso.textContent = k.arboles === 0
      ? "No hay posiciones candidatas con estos filtros."
      : `Solo hay ${k.arboles} posiciones disponibles de las ${k.arboles_pedidos} pedidas (límite por calle o pocas posiciones en la zona).`;
  } else aviso.hidden = true;
  $("estado").textContent = "";

  $("barrios").innerHTML = r.barrios.slice(0, 6).map((b) =>
    `<li class="${nivel(b.prioridad_media)}"><strong><span>${esc(b.barrio)}</span><span>${b.arboles} árboles</span></strong></li>`).join("");
  $("top").innerHTML = r.seleccion.slice(0, 10).map((s) =>
    `<li class="${nivel(s.prioridad)}"><strong><span>${s.rank}. ${esc(s.calle)}</span><span>${s.prioridad.toFixed(2)}</span></strong>
     <small>${esc(s.barrio)} · ${esc(s.motivo)} · ${esc(s.accion)}</small></li>`).join("");
  $("datos").textContent = `Posiciones candidatas analizadas: ${r.meta.n_candidatos.toLocaleString("es-ES")} · árboles existentes: ${r.meta.n_arboles.toLocaleString("es-ES")} (${r.meta.fuente_arboles}). ` +
    "Sombra: dato real. Calor: " + r.meta.calor_fuente + ". Contaminación: 4 estaciones interpoladas (IDW)." +
    (r.meta.calor_fuente.includes("IA") ? " La temperatura es una estimación generada con IA, no una medición." : "");
  $("supuestos").textContent = JSON.stringify(r.supuestos, null, 2);

  if (!capaSel) return;
  capaSel.clearLayers();
  r.seleccion.forEach((s) => {
    const c = COLOR[nivel(s.prioridad)];
    L.circleMarker([s.lat, s.lon], { radius: 5, color: c, fillColor: c, fillOpacity: 0.7, weight: 1 })
      .bindPopup(`<b>${s.rank}. ${esc(s.calle)}</b><br>${esc(s.barrio)}<br>${esc(s.motivo)}${s.temp != null ? `<br>Temp. máx. media verano ≈ ${s.temp} °C (estimada)` : ""}<br>Prioridad ${s.prioridad.toFixed(2)} · ${esc(s.accion)}`)
      .addTo(capaSel);
  });
  if (!centrado && r.seleccion.length) {
    mapa.fitBounds(L.latLngBounds(r.seleccion.map((s) => [s.lat, s.lon])), { padding: [40, 40], maxZoom: 17 });
    centrado = true;
  }
}

iniciar();
