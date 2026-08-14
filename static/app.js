(function () {
  var PROVINCIAS = [
    "Araba/Álava","Albacete","Alicante/Alacant","Almería","Ávila","Badajoz","Illes Balears",
    "Barcelona","Burgos","Cáceres","Cádiz","Castellón/Castelló","Ciudad Real","Córdoba",
    "A Coruña","Cuenca","Girona","Granada","Guadalajara","Gipuzkoa","Huelva","Huesca","Jaén",
    "León","Lleida","La Rioja","Lugo","Madrid","Málaga","Murcia","Navarra","Ourense","Asturias",
    "Palencia","Las Palmas","Pontevedra","Salamanca","Santa Cruz de Tenerife","Cantabria",
    "Segovia","Sevilla","Soria","Tarragona","Teruel","Toledo","Valencia/València","Valladolid",
    "Bizkaia","Zamora","Zaragoza","Ceuta","Melilla"
  ];

  var fEstado = document.getElementById("f-estado");
  var fTipoSub = document.getElementById("f-tiposub");
  var fTipoBien = document.getElementById("f-tipobien");
  var fProv = document.getElementById("f-prov");
  var fText = document.getElementById("f-text");

  PROVINCIAS.forEach(function (p) {
    var o = document.createElement("option");
    o.value = p; o.textContent = p;
    fProv.appendChild(o);
  });

  function fmt(n) {
    if (n === null || n === undefined) return "";
    return n.toLocaleString("es-ES", { style: "currency", currency: "EUR" });
  }

  function statusBadge(estado) {
    if (estado === "Próxima apertura") return '<span class="badge pronto">Próxima apertura</span>';
    if (estado === "Celebrándose") return '<span class="badge abierta">Celebrándose</span>';
    return '<span class="badge cerrada">Concluida</span>';
  }

  function currentFilters() {
    var p = new URLSearchParams();
    if (fEstado.value) p.set("estado", fEstado.value);
    if (fTipoSub.value) p.set("tipo_subasta", fTipoSub.value);
    if (fTipoBien.value) p.set("tipo_bien", fTipoBien.value);
    if (fProv.value) p.set("provincia", fProv.value);
    if (fText.value.trim()) p.set("texto", fText.value.trim());
    return p;
  }

  function renderKPIs(rows) {
    document.getElementById("kpi-total").textContent = rows.length;
    document.getElementById("kpi-proxima").textContent = rows.filter(function (r) { return r.estado === "Próxima apertura"; }).length;
    document.getElementById("kpi-celebrando").textContent = rows.filter(function (r) { return r.estado === "Celebrándose"; }).length;
    var conTasacion = rows.filter(function (r) { return r.valor_tasacion; });
    var avg = conTasacion.length ? conTasacion.reduce(function (s, r) { return s + r.valor_tasacion; }, 0) / conTasacion.length : null;
    document.getElementById("kpi-avg").textContent = avg ? fmt(avg) : "-";
  }

  function renderTable(rows) {
    var tbody = document.getElementById("lots-tbody");
    tbody.innerHTML = rows.map(function (r) {
      return "<tr>" +
        '<td class="lot-id">' + r.id + "</td>" +
        "<td>" + (r.tipo_subasta || "") + "</td>" +
        "<td>" + (r.tipo_bien || "") + "</td>" +
        "<td>" + (r.provincia || "") + (r.localidad ? " / " + r.localidad : "") + "</td>" +
        '<td class="lot-desc wrap" title="' + (r.descripcion || "").replace(/"/g, "&quot;") + '">' + (r.descripcion || "").slice(0, 90) + "</td>" +
        '<td class="num-val">' + fmt(r.valor_tasacion) + "</td>" +
        '<td class="num-val">' + fmt(r.valor_subasta) + "</td>" +
        "<td>" + (r.puja_minima ? fmt(r.puja_minima) : "Sin puja mínima") + "</td>" +
        "<td>" + (r.fecha_conclusion || "") + "</td>" +
        "<td>" + statusBadge(r.estado) + "</td>" +
        "</tr>";
    }).join("");
  }

  function cargar() {
    var params = currentFilters();
    fetch("/api/lotes?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        document.getElementById("table-count").textContent = rows.length
          ? "Mostrando " + rows.length + " lotes"
          : 'Sin resultados. Si todavía no sincronizaste, tocá "Actualizar".';
        renderKPIs(rows);
        renderTable(rows);
      });
  }

  function cargarOpciones() {
    fetch("/api/opciones")
      .then(function (r) { return r.json(); })
      .then(function (op) {
        op.tipos_subasta.forEach(function (t) {
          var o = document.createElement("option"); o.value = t; o.textContent = t;
          fTipoSub.appendChild(o);
        });
        op.tipos_bien.forEach(function (t) {
          var o = document.createElement("option"); o.value = t; o.textContent = t;
          fTipoBien.appendChild(o);
        });
      });
  }

  var pollTimer = null;

  function actualizarEstadoConexion() {
    return fetch("/api/estado")
      .then(function (r) { return r.json(); })
      .then(function (e) {
        var nota = document.getElementById("ultima-sync");
        nota.textContent = e.boe_ultima_sync
          ? "Última sincronización: " + e.boe_ultima_sync.replace("T", " ")
          : "Todavía no sincronizado";
        return e;
      });
  }

  function pollearSync() {
    var btn = document.getElementById("btn-sync");
    var statusText = document.getElementById("status-text");
    pollTimer = setInterval(function () {
      actualizarEstadoConexion().then(function (e) {
        statusText.textContent = e.mensaje_sync || "Sincronizando...";
        // refrescar tabla/KPIs en cada tick, no solo al terminar, para que
        // se vea crecer en vivo en vez de quedar en "0" hasta el final
        cargarOpciones();
        cargar();
        if (!e.sincronizando) {
          clearInterval(pollTimer);
          btn.disabled = false;
          btn.textContent = "Actualizar (traer subastas de hoy)";
        }
      });
    }, 2000);
  }

  [fEstado, fTipoSub, fTipoBien, fProv].forEach(function (el) { el.addEventListener("change", cargar); });
  fText.addEventListener("input", cargar);
  document.getElementById("f-clear").addEventListener("click", function () {
    fEstado.value = ""; fTipoSub.value = ""; fTipoBien.value = ""; fProv.value = ""; fText.value = "";
    cargar();
  });

  document.getElementById("export-xls").addEventListener("click", function () {
    var params = currentFilters();
    window.location.href = "/api/export?" + params.toString();
  });

  document.getElementById("btn-sync").addEventListener("click", function () {
    var btn = document.getElementById("btn-sync");
    var statusText = document.getElementById("status-text");
    btn.disabled = true;
    btn.textContent = "Actualizando... (2 a 5 minutos)";
    statusText.textContent = "Sincronizando con BOE Subastas...";
    fetch("/api/sync", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () { pollearSync(); })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "Actualizar (traer subastas de hoy)";
        statusText.textContent = "Hubo un error al sincronizar, probá de nuevo";
      });
  });

  cargarOpciones();
  cargar();
  actualizarEstadoConexion();
})();
