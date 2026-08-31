(function () {
  document.documentElement.dataset.theme = "dark";
  document.getElementById("theme-toggle").addEventListener("click", function () {
    var dark = document.documentElement.dataset.theme === "dark";
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    document.getElementById("theme-icon-dark").style.display = dark ? "none" : "block";
    document.getElementById("theme-icon-light").style.display = dark ? "block" : "none";
  });
})();

(function () {
  // El scroll real pasa dentro de #main (overflow:auto), no en window/body
  // -.app-window fija todo a 100vh y .main es el unico que scrollea. Pedido
  // del cliente: con el historico completo trayendo miles de filas, llegar
  // hasta la tabla (o volver arriba) a puro scroll era muy incomodo.
  var main = document.getElementById("main");
  document.getElementById("scroll-top").addEventListener("click", function () {
    main.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.getElementById("scroll-bottom").addEventListener("click", function () {
    main.scrollTo({ top: main.scrollHeight, behavior: "smooth" });
  });
})();

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

  // Multi-select tipo checkboxes: cada filtro deja tildar varias opciones a
  // la vez y combinarlas (pedido explicito del cliente: "en subastaboe...
  // se hace por click en los filtros que necesitas", cosa que un <select>
  // de una sola opcion no permite).
  function crearMultiSelect(container, etiqueta) {
    var seleccion = [];
    var opciones = [];
    var onChange = function () {};

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "msel-btn";
    var panel = document.createElement("div");
    panel.className = "msel-panel";
    container.appendChild(btn);
    container.appendChild(panel);

    function renderBtn() {
      btn.textContent = etiqueta + (seleccion.length ? " (" + seleccion.length + ")" : "");
      btn.classList.toggle("active", seleccion.length > 0);
    }

    function renderPanel() {
      panel.innerHTML = "";
      if (!opciones.length) {
        var vacio = document.createElement("div");
        vacio.className = "msel-empty";
        vacio.textContent = "Sin opciones todavía";
        panel.appendChild(vacio);
        return;
      }
      opciones.forEach(function (v) {
        var row = document.createElement("label");
        row.className = "msel-row";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = seleccion.indexOf(v) !== -1;
        cb.addEventListener("change", function () {
          if (cb.checked) {
            if (seleccion.indexOf(v) === -1) seleccion.push(v);
          } else {
            seleccion = seleccion.filter(function (x) { return x !== v; });
          }
          renderBtn();
          onChange();
        });
        var span = document.createElement("span");
        span.textContent = v;
        row.appendChild(cb);
        row.appendChild(span);
        panel.appendChild(row);
      });
    }

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var abierto = panel.classList.contains("open");
      document.querySelectorAll(".msel-panel.open").forEach(function (p) { p.classList.remove("open"); });
      if (!abierto) panel.classList.add("open");
    });
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () { panel.classList.remove("open"); });

    renderBtn();
    renderPanel();

    return {
      setOptions: function (nuevas) {
        opciones = nuevas;
        seleccion = seleccion.filter(function (v) { return opciones.indexOf(v) !== -1; });
        renderBtn();
        renderPanel();
      },
      getValues: function () { return seleccion.slice(); },
      clear: function () { seleccion = []; renderBtn(); renderPanel(); },
      onChange: function (fn) { onChange = fn; },
    };
  }

  var fText = document.getElementById("f-text");
  var fFechaInicioDesde = document.getElementById("f-fecha-inicio-desde");
  var fFechaInicioHasta = document.getElementById("f-fecha-inicio-hasta");
  var fFechaFinDesde = document.getElementById("f-fecha-fin-desde");
  var fFechaFinHasta = document.getElementById("f-fecha-fin-hasta");

  // Los <input type=date> mostraban el formato segun el idioma de Windows
  // en vez del de la pagina (lang="es" no alcanza, confirmado en vivo por
  // el cliente: interpretaba mes/dia al reves sin darse cuenta). Con un
  // input de texto simple, el formato dd/mm/aaaa queda fijo sin importar
  // la configuracion regional de la maquina. Auto-inserta las barras para
  // que sea comodo de tipear igual (basta con escribir los numeros).
  function autoFormatearFecha(input) {
    input.addEventListener("input", function () {
      var digitos = input.value.replace(/\D/g, "").slice(0, 8);
      var partes = [];
      if (digitos.length > 0) partes.push(digitos.slice(0, 2));
      if (digitos.length > 2) partes.push(digitos.slice(2, 4));
      if (digitos.length > 4) partes.push(digitos.slice(4, 8));
      input.value = partes.join("/");
    });
  }

  // "dd/mm/aaaa" (lo que tipea el usuario) -> "aaaa-mm-dd" (lo que espera
  // el backend); vacio o incompleto -> "" (sin filtro).
  function fechaAIso(texto) {
    var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec((texto || "").trim());
    if (!m) return "";
    var dia = parseInt(m[1], 10), mes = parseInt(m[2], 10), anio = parseInt(m[3], 10);
    // El formato (2/2/4 digitos) no alcanza para saber si la fecha es
    // real - "23/42/0303" tambien matchea ese patron. Sin este chequeo,
    // una fecha invalida se mandaba igual al backend como texto ISO
    // ("0303-42-23"), y como ese texto ordena alfabeticamente ANTES que
    // cualquier año real (20XX), un filtro "desde" con eso terminaba
    // sin filtrar nada en vez de fallar o ser ignorado - confirmado en
    // vivo: seguian apareciendo resultados con una fecha sin sentido
    // puesta en el filtro.
    if (mes < 1 || mes > 12 || dia < 1 || dia > 31) return "";
    return anio + "-" + m[2] + "-" + m[1];
  }

  [fFechaInicioDesde, fFechaInicioHasta, fFechaFinDesde, fFechaFinHasta].forEach(autoFormatearFecha);
  var mFuente = crearMultiSelect(document.getElementById("msel-fuente"), "Fuente");
  var mEstado = crearMultiSelect(document.getElementById("msel-estado"), "Estado");
  var mTipoSub = crearMultiSelect(document.getElementById("msel-tiposub"), "Tipo de subasta");
  var mTipoBien = crearMultiSelect(document.getElementById("msel-tipobien"), "Tipo de bien");
  var mProv = crearMultiSelect(document.getElementById("msel-prov"), "Provincia");

  mFuente.setOptions(["BOE Subastas", "Seguridad Social"]);
  // Categorias reales del formulario de busqueda avanzada de BOE
  // (parametro dato[0] / SUBASTA.ORIGEN, confirmado en vivo) - antes este
  // filtro mostraba texto libre ("JUDICIAL EN VIA DE APREMIO") sacado de
  // lo que ya se hubiera sincronizado, que nunca coincidia con las
  // opciones reales del sitio.
  mTipoSub.setOptions(["Judicial", "Notarial", "AEAT", "Otras administraciones tributarias", "Subastas administrativas generales"]);
  mProv.setOptions(PROVINCIAS);

  // BOE Subastas y Seguridad Social usan categorias distintas (pedido del
  // cliente: "si te fijas en la web de seguridad social los filtros son
  // distintos"). Sin ninguna fuente tildada (o con las dos) se muestra la
  // union; tildando solo una se acota a sus categorias reales.
  var TIPO_BIEN_BOE = ["Inmueble", "Vehículo", "Bien mueble"];
  var TIPO_BIEN_SS = ["Finca Rústica", "Finca Urbana", "Vehículo", "Embarcación", "Resto de Bienes Muebles"];
  var ESTADOS_BOE = ["Próxima apertura", "Celebrándose", "Suspendida", "Cancelada", "Concluida en Portal de Subastas", "Finalizada por Autoridad Gestora"];
  var ESTADOS_SS = ["Próxima apertura", "Celebrándose", "Concluida", "Sin fecha"];

  function union(a, b) {
    return a.concat(b.filter(function (v) { return a.indexOf(v) === -1; }));
  }

  function actualizarFiltrosSegunFuente() {
    var fuentes = mFuente.getValues();
    var conBOE = !fuentes.length || fuentes.indexOf("BOE Subastas") !== -1;
    var conSS = !fuentes.length || fuentes.indexOf("Seguridad Social") !== -1;
    var tipoBien = [], estados = [];
    if (conBOE) { tipoBien = union(tipoBien, TIPO_BIEN_BOE); estados = union(estados, ESTADOS_BOE); }
    if (conSS) { tipoBien = union(tipoBien, TIPO_BIEN_SS); estados = union(estados, ESTADOS_SS); }
    mTipoBien.setOptions(tipoBien);
    mEstado.setOptions(estados);
  }

  actualizarFiltrosSegunFuente();

  function fmt(n) {
    if (n === null || n === undefined) return "";
    return n.toLocaleString("es-ES", { style: "currency", currency: "EUR" });
  }

  var TIPO_BIEN_COLOR = {
    "Inmueble": "#5a9fd6", "Finca Rústica": "#5a9fd6", "Finca Urbana": "#5a9fd6",
    "Vehículo": "#f0794a",
    "Bien mueble": "#22c98c", "Embarcación": "#22c98c", "Resto de Bienes Muebles": "#22c98c",
  };

  function statusBadge(estado) {
    if (estado === "Próxima apertura") {
      return '<span class="badge pronto"><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/><line x1="5" y1="5" x2="5" y2="2.6" stroke="currentColor" stroke-width="1.3"/><line x1="5" y1="5" x2="6.8" y2="6" stroke="currentColor" stroke-width="1.3"/></svg>Próxima apertura</span>';
    }
    if (estado === "Celebrándose") {
      return '<span class="badge abierta"><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="3.4" fill="currentColor"/></svg>Celebrándose</span>';
    }
    if (estado === "Suspendida" || estado === "Cancelada") {
      return '<span class="badge cerrada"><svg viewBox="0 0 10 10"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' + estado + '</span>';
    }
    if (estado === "Sin fecha") {
      // Seguridad Social a veces no publica fecha para un lote - antes
      // esto cayendo silenciosamente en "Proxima apertura" (ver
      // _inferir_estado en el scraper), dando a entender que se sabe la
      // fecha cuando en realidad no se sabe nada. Badge distinto para no
      // mezclar "no sabemos" con "sabemos que abre pronto".
      return '<span class="badge cerrada"><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="none" stroke="currentColor" stroke-width="1.3"/><line x1="5" y1="3" x2="5" y2="5.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="5" cy="7" r="0.6" fill="currentColor"/></svg>Sin fecha</span>';
    }
    return '<span class="badge cerrada"><svg viewBox="0 0 10 10"><path d="M2 5l2 2 4-4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' + (estado || "Concluida") + '</span>';
  }

  // Se resetea a 1 solo cuando cambia un filtro (ver cargarDesdeFiltro),
  // NO en cada refresco de cargar() - /api/lotes y el historico completo
  // pollean cargar() cada 2-3 segundos mientras corre una sync (ver
  // pollearSync/pollearHistorico), y si eso reseteara la pagina, alguien
  // mirando la pagina 3 volveria a la 1 solo, cada par de segundos.
  var paginaActual = 1;

  function filtrosActuales() {
    return {
      fuente: mFuente.getValues(),
      estado: mEstado.getValues(),
      categoria_subasta: mTipoSub.getValues(),
      tipo_bien: mTipoBien.getValues(),
      provincia: mProv.getValues(),
      texto: fText.value.trim(),
      fecha_inicio_desde: fechaAIso(fFechaInicioDesde.value),
      fecha_inicio_hasta: fechaAIso(fFechaInicioHasta.value),
      fecha_conclusion_desde: fechaAIso(fFechaFinDesde.value),
      fecha_conclusion_hasta: fechaAIso(fFechaFinHasta.value),
    };
  }

  function paramsDeFiltros(f) {
    var p = new URLSearchParams();
    ["fuente", "estado", "categoria_subasta", "tipo_bien", "provincia"].forEach(function (k) {
      (f[k] || []).forEach(function (v) { p.append(k, v); });
    });
    if (f.texto) p.set("texto", f.texto);
    ["fecha_inicio_desde", "fecha_inicio_hasta", "fecha_conclusion_desde", "fecha_conclusion_hasta"].forEach(function (k) {
      if (f[k]) p.set(k, f[k]);
    });
    p.set("pagina", paginaActual);
    return p;
  }

  function renderKPIs(resumen) {
    // Se calculan en el servidor sobre TODAS las filas que matchean los
    // filtros (ver db.resumen_lotes), no solo sobre las que llegan para
    // pintar la tabla (esas si vienen limitadas - ver LIMITE_TABLA_PANTALLA
    // en app.py). Si esto tomara rows.length en vez del resumen, las
    // tarjetas KPI mostrarian como mucho el limite (ej. "500") en vez del
    // total real (ej. "10813") apenas hubiera mas filas que el limite.
    document.getElementById("kpi-total").textContent = resumen.total;
    document.getElementById("kpi-proxima").textContent = resumen.proxima_apertura;
    document.getElementById("kpi-celebrando").textContent = resumen.celebrandose;
    document.getElementById("kpi-avg").textContent = resumen.tasacion_promedio ? fmt(resumen.tasacion_promedio) : "-";
  }

  function pct(parte, total) {
    if (!parte || !total) return "";
    return (parte / total * 100).toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " %";
  }

  function detalleUrl(r) {
    // Seguridad Social exige haber pasado por una busqueda de verdad en
    // esa misma sesion antes de poder ver el detalle de un lote - una
    // sesion nueva (como la del navegador del usuario, sin pasar por
    // nuestro scraper) recibe la "Pagina de Error" del sitio (confirmado
    // en vivo). Enlazar solo a la busqueda avanzada (como se hacia antes)
    // no alcanzaba - pedido explicito del cliente: "debería llevar a la
    // subasta original", no a un formulario de busqueda vacio. Por eso
    // ahora pasa por /ss/detalle/<id>, que hace esa busqueda de
    // calentamiento del lado del servidor (donde si se puede armar la
    // sesion) y devuelve la pagina real de seg-social.es.
    if (r.id.indexOf("SS-") === 0) {
      return "/ss/detalle/" + encodeURIComponent(r.id.slice(3));
    }
    // Los lotes de una subasta con varios lotes se guardan como "<id
    // base>-L<n>" para poder distinguirlos en nuestra tabla (ver
    // detalle_lotes en boe.py) - pero esa forma de id no existe en BOE,
    // ahi el id real es solo la parte base y el lote es un parametro
    // aparte (idLote). Mandar el id CON el sufijo -L directo a idSub
    // tiraba "ERROR: Identificador de subasta incorrecto" en el sitio
    // real (confirmado en vivo) - se separa el sufijo y se arma la URL
    // real de ese lote puntual (ver=3&idLote=N, mismo patron que usa
    // detalle_bien() del scraper para pedir ese lote).
    var loteMatch = /^(.*)-L(\d+)$/.exec(r.id);
    if (loteMatch) {
      return "https://subastas.boe.es/detalleSubasta.php?idSub=" + encodeURIComponent(loteMatch[1]) + "&ver=3&idLote=" + loteMatch[2];
    }
    return "https://subastas.boe.es/detalleSubasta.php?idSub=" + encodeURIComponent(r.id);
  }

  function renderTable(rows) {
    var tbody = document.getElementById("lots-tbody");
    var empty = document.getElementById("empty-state");
    empty.style.display = rows.length ? "none" : "flex";
    tbody.innerHTML = rows.map(function (r) {
      var color = TIPO_BIEN_COLOR[r.tipo_bien] || "#898781";
      var url = detalleUrl(r);
      var idCell = url ? '<a href="' + url + '" target="_blank" rel="noopener">' + r.id + "</a>" : r.id;
      return "<tr>" +
        '<td class="lot-id">' + idCell + "</td>" +
        '<td class="col-opt">' + (r.tipo_subasta || "") + "</td>" +
        '<td class="col-opt3"><span class="type-dot" style="background:' + color + '"></span>' + (r.tipo_bien || "") + "</td>" +
        '<td class="col-opt">' + (r.provincia || "") + (r.localidad ? " / " + r.localidad : "") + "</td>" +
        '<td class="lot-desc wrap" title="' + (r.descripcion || "").replace(/"/g, "&quot;") + '">' + (r.descripcion || "").slice(0, 90) + "</td>" +
        '<td class="num-val">' + fmt(r.valor_tasacion) + "</td>" +
        '<td class="num-val col-opt2">' + fmt(r.valor_subasta) + "</td>" +
        '<td class="num-val col-opt3">' + pct(r.puja_minima, r.valor_subasta) + "</td>" +
        '<td class="num-val col-opt3">' + pct(r.cantidad_reclamada, r.valor_subasta) + "</td>" +
        '<td class="num-val col-opt2">' + fmt(r.cantidad_reclamada) + "</td>" +
        '<td class="col-opt2">' + (r.puja_minima ? fmt(r.puja_minima) : "Sin puja mínima") + "</td>" +
        '<td class="num-val col-opt2">' + fmt(r.importe_deposito) + "</td>" +
        '<td class="num-val col-opt2">' + fmt(r.tramos_entre_pujas) + "</td>" +
        '<td class="col-opt">' + (r.fecha_conclusion || "") + "</td>" +
        "<td>" + statusBadge(r.estado) + "</td>" +
        "</tr>";
    }).join("");
  }

  function renderPaginacion(pagina, totalPaginas, desde, hasta, total) {
    var row = document.getElementById("pagination-row");
    row.style.display = totalPaginas > 1 ? "flex" : "none";
    // Se repite el rango (no solo el numero de pagina) aca abajo, al lado
    // de los botones - antes solo estaba arriba del todo (junto a los
    // filtros), lejos de donde esta parado el usuario cuando llega al
    // final de la tabla para pasar de pagina. Pedido del cliente: no
    // quedaba claro en que pagina/rango estaba sin scrollear hasta arriba.
    document.getElementById("pag-info").textContent =
      desde + "–" + hasta + " de " + total.toLocaleString("es-ES") + " · Página " + pagina + " de " + totalPaginas;
    document.getElementById("pag-prev").disabled = pagina <= 1;
    document.getElementById("pag-next").disabled = pagina >= totalPaginas;
  }

  // Cambiar de filtro invalida la pagina en la que se estaba parado (un
  // filtro nuevo puede tener menos paginas, o las mismas paginas pero con
  // otro contenido) - siempre se vuelve a la 1. Los pollers de sync SI
  // usan cargar() directo (ver mas abajo), para no resetear la pagina en
  // cada tick mientras el usuario esta mirando resultados.
  function cargarDesdeFiltro() {
    paginaActual = 1;
    cargar();
  }

  function cargar() {
    var params = paramsDeFiltros(filtrosActuales());
    fetch("/api/lotes?" + params.toString())
      .then(function (r) {
        if (!r.ok) throw new Error("El servidor respondió " + r.status);
        return r.json();
      })
      .then(function (data) {
        var rows = data.lotes;
        var total = data.resumen.total;
        // El servidor recorta `pagina` a total_paginas si un cambio de
        // filtro la dejo fuera de rango (ej. estaba en la pagina 5 y el
        // nuevo filtro solo tiene 2) - hay que reflejar eso aca, si no
        // paginaActual queda desincronizada de lo que el servidor mando.
        paginaActual = data.pagina;
        var msg;
        var desde = (data.pagina - 1) * data.tamanio_pagina + 1;
        var hasta = desde + rows.length - 1;
        if (!total) {
          msg = 'Sin resultados. Si todavía no sincronizaste, tocá "Actualizar".';
        } else {
          msg = "Mostrando " + desde + "–" + hasta + " de " + total + " lotes";
        }
        document.getElementById("table-count").textContent = msg;
        renderKPIs(data.resumen);
        renderTable(rows);
        renderPaginacion(data.pagina, data.total_paginas, desde, hasta, total);
      })
      .catch(function (err) {
        // Sin esto, un error del servidor (ej. una base vieja sin migrar)
        // dejaba la tabla vacia sin ningun aviso - parecia que la app no
        // tenia datos en vez de mostrar que algo fallo.
        document.getElementById("table-count").textContent = "Error cargando los datos: " + err.message;
      });
  }

  var pollTimer = null;

  function actualizarEstadoConexion() {
    return fetch("/api/estado")
      .then(function (r) { return r.json(); })
      .then(function (e) {
        // Antes solo miraba boe_ultima_sync/seg_social_ultima_sync (los que
        // pisa "Actualizar") - un barrido de "Descargar histórico completo"
        // corriendo horas en segundo plano no lo actualizaba nunca, y el
        // cartel quedaba mostrando una fecha vieja aunque la app estuviera
        // trayendo datos activamente (confirmado en vivo: confundia al
        // cliente, parecia que la app no habia hecho nada reciente).
        var nota = document.getElementById("ultima-sync");
        var ultima = [
          e.boe_ultima_sync,
          e.seg_social_ultima_sync,
          e.historico_boe_ultima_pasada,
          e.historico_seg_social_ultima_pasada,
        ].filter(Boolean).sort().pop();
        nota.textContent = ultima
          ? "Última sincronización: " + ultima.replace("T", " ")
          : "Todavía no sincronizado";
        return e;
      });
  }

  function pollearSync() {
    var btn = document.getElementById("btn-sync");
    var label = btn.querySelector(".btn-label");
    var statusText = document.getElementById("status-text");
    var track = document.getElementById("progress-track");
    btn.disabled = true;
    label.textContent = "Actualizando... (puede tardar varios minutos)";
    track.classList.add("active");
    function tick() {
      actualizarEstadoConexion().then(function (e) {
        statusText.textContent = e.mensaje_sync || "Sincronizando...";
        // refrescar tabla/KPIs en cada tick, no solo al terminar, para que
        // se vea crecer en vivo en vez de quedar en "0" hasta el final
        cargar();
        if (!e.sincronizando) {
          clearInterval(pollTimer);
          btn.disabled = false;
          label.textContent = "Actualizar (traer subastas de hoy)";
          track.classList.remove("active");
        }
      });
    }
    // setInterval espera el intervalo entero antes del primer tick - sin
    // este llamado inmediato, el boton/barra ya se mostraban "activos" pero
    // el texto de estado se quedaba con el mensaje viejo (o el placeholder
    // estatico del HTML) hasta 2 segundos despues, sobre todo notorio al
    // retomar un sync que ya estaba corriendo de antes (confirmado en vivo
    // por el cliente con una captura de pantalla).
    tick();
    pollTimer = setInterval(tick, 2000);
  }

  var pollTimerHistorico = null;

  function pollearHistorico() {
    var btn = document.getElementById("btn-historico");
    var label = btn.querySelector(".btn-label");
    var statusText = document.getElementById("historico-status");
    var track = document.getElementById("progress-track-historico");
    btn.disabled = true;
    label.textContent = "Descargando histórico...";
    track.classList.add("active");
    function tick() {
      actualizarEstadoConexion().then(function (e) {
        statusText.textContent = e.historico_mensaje || "Descargando histórico...";
        cargar();
        if (!e.historico_en_progreso) {
          clearInterval(pollTimerHistorico);
          btn.disabled = false;
          label.textContent = "Descargar histórico completo";
          track.classList.remove("active");
        }
      });
    }
    // mismo fix que pollearSync: sin el llamado inmediato, el boton ya
    // decia "Descargando historico..." pero el texto de estado se quedaba
    // en el placeholder viejo hasta el primer tick, 3 segundos despues.
    tick();
    pollTimerHistorico = setInterval(tick, 3000);
  }

  document.getElementById("pag-prev").addEventListener("click", function () {
    if (paginaActual > 1) { paginaActual -= 1; cargar(); }
  });
  document.getElementById("pag-next").addEventListener("click", function () {
    paginaActual += 1;
    cargar();
  });

  mFuente.onChange(function () { actualizarFiltrosSegunFuente(); cargarDesdeFiltro(); });
  [mEstado, mTipoSub, mTipoBien, mProv].forEach(function (m) { m.onChange(cargarDesdeFiltro); });
  fText.addEventListener("input", cargarDesdeFiltro);
  [fFechaInicioDesde, fFechaInicioHasta, fFechaFinDesde, fFechaFinHasta].forEach(function (el) {
    el.addEventListener("change", cargarDesdeFiltro);
  });
  document.getElementById("f-clear").addEventListener("click", function () {
    [mFuente, mEstado, mTipoSub, mTipoBien, mProv].forEach(function (m) { m.clear(); });
    fText.value = "";
    [fFechaInicioDesde, fFechaInicioHasta, fFechaFinDesde, fFechaFinHasta].forEach(function (el) { el.value = ""; });
    actualizarFiltrosSegunFuente();
    cargarDesdeFiltro();
  });

  document.getElementById("export-xls").addEventListener("click", function () {
    var btn = document.getElementById("export-xls");
    var statusText = document.getElementById("status-text");
    // El dialogo nativo "Guardar como" es una ventana de Windows aparte,
    // no algo dibujado en esta pagina - si queda tapada detras de la
    // ventana principal, no hay ninguna pista visual de que ya aparecio.
    // Sin deshabilitar el boton, eso llevaba a clickear 10-20 veces
    // seguidas pensando que "no pasaba nada", disparando esa cantidad de
    // export_excel() en simultaneo - confirmado en vivo que eso termino
    // en "Error: database is locked" por la carga de lecturas encima del
    // histórico escribiendo de fondo.
    if (btn.disabled) return;
    btn.disabled = true;
    var filtros = filtrosActuales();
    statusText.textContent = "Elegí dónde guardar el archivo...";
    window.pywebview.api.export_excel(filtros)
      .then(function (res) {
        if (res.ok) {
          statusText.textContent = "Exportado: " + res.lotes + " lotes en " + res.ruta;
        } else if (res.cancelado) {
          statusText.textContent = "Listo";
        } else {
          // Antes, si fallaba escribir el archivo (permisos, carpeta
          // sincronizada, etc.), esto quedaba con el mensaje "Elegí dónde
          // guardar..." para siempre, sin decir que algo salio mal.
          statusText.textContent = "No se pudo guardar el Excel: " + (res.error || "error desconocido");
        }
      })
      .catch(function (err) {
        statusText.textContent = "No se pudo exportar: " + err;
      })
      .finally(function () {
        btn.disabled = false;
      });
  });

  document.getElementById("btn-sync").addEventListener("click", function () {
    var btn = document.getElementById("btn-sync");
    var label = btn.querySelector(".btn-label");
    var statusText = document.getElementById("status-text");
    var track = document.getElementById("progress-track");
    btn.disabled = true;
    label.textContent = "Actualizando... (puede tardar varios minutos)";
    track.classList.add("active");
    var provinciasElegidas = mProv.getValues();
    var params = new URLSearchParams();
    provinciasElegidas.forEach(function (p) { params.append("provincia", p); });
    mFuente.getValues().forEach(function (f) { params.append("fuente", f); });
    statusText.textContent = provinciasElegidas.length
      ? "Sincronizando " + provinciasElegidas.join(", ") + "..."
      : "Sincronizando con BOE Subastas y Seguridad Social...";
    fetch("/api/sync?" + params.toString(), { method: "POST" })
      .then(function (r) {
        if (r.status === 409) {
          // Ya habia una sincronizacion en curso (por ej. arrancada sin
          // filtros antes de tildar estos) - sin este chequeo, el boton
          // se quedaba mostrando/sondeando esa sync vieja sin avisar que
          // el pedido nuevo con filtros fue ignorado.
          btn.disabled = false;
          label.textContent = "Actualizar (traer subastas de hoy)";
          track.classList.remove("active");
          statusText.textContent = "Ya había una sincronización en curso (sin estos filtros) - esperá a que termine y probá de nuevo.";
          return null;
        }
        return r.json();
      })
      .then(function (data) { if (data) pollearSync(); })
      .catch(function () {
        btn.disabled = false;
        label.textContent = "Actualizar (traer subastas de hoy)";
        track.classList.remove("active");
        statusText.textContent = "Hubo un error al sincronizar, probá de nuevo";
      });
  });

  document.getElementById("btn-historico").addEventListener("click", function () {
    var btn = document.getElementById("btn-historico");
    var label = btn.querySelector(".btn-label");
    label.textContent = "Descargando histórico...";
    // Igual que "Actualizar": respeta el filtro de Fuente Y de Provincia
    // tildados en pantalla. Antes siempre bajaba las 52 provincias de las
    // 2 fuentes sin importar ningun filtro - confirmado con el cliente,
    // que tenia solo BOE Subastas tildado y de todos modos le corto todo
    // el barrido un error de Seguridad Social; y pidio poder probar con
    // una sola provincia (Malaga) en vez de esperar el barrido completo.
    var params = new URLSearchParams();
    mFuente.getValues().forEach(function (f) { params.append("fuente", f); });
    mProv.getValues().forEach(function (p) { params.append("provincia", p); });
    fetch("/api/sync_historico?" + params.toString(), { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () { pollearHistorico(); })
      .catch(function () {
        btn.disabled = false;
        label.textContent = "Descargar histórico completo";
        document.getElementById("historico-status").textContent = "Hubo un error, probá de nuevo";
      });
  });

  cargar();
  actualizarEstadoConexion().then(function (e) {
    // si el barrido historico ya estaba corriendo (p.ej. quedo prendido
    // de una sesion anterior de la app), retomar el poll en vez de
    // mostrar el boton como si no hubiera nada pasando
    if (e.historico_en_progreso) {
      pollearHistorico();
    } else {
      // El texto "Todavia no se corrio." de index.html es solo el
      // placeholder inicial del HTML - antes se quedaba ahi para siempre
      // en cada apertura de la app salvo que tocaras el boton en esa
      // misma sesion, aunque el historico ya tuviera avance real de
      // sesiones anteriores (guardado en sync_state, que ahora si
      // persiste gracias al fix de la base de datos). Un usuario podia
      // ver "Todavia no se corrio" y pensar que no habia arrancado nunca,
      // cuando en realidad ya tenia miles de lotes historicos guardados.
      // last_full_sync recien se escribe al terminar el barrido ENTERO
      // (puede tardar dias), por eso se chequea primero si ya hay avance
      // parcial guardado (historico_*_con_avance) antes que eso.
      var ultimaHist = [e.historico_boe_ultima_pasada, e.historico_seg_social_ultima_pasada]
        .filter(Boolean).sort().pop();
      var conAvance = e.historico_boe_con_avance || e.historico_seg_social_con_avance;
      if (ultimaHist) {
        document.getElementById("historico-status").textContent =
          "Última pasada completa: " + ultimaHist.replace("T", " ") + " (seguí corriéndolo para traer más)";
      } else if (conAvance) {
        document.getElementById("historico-status").textContent =
          "Ya tiene avance guardado de antes (todavía no una pasada completa) - tocá el botón para seguir";
      }
    }
    if (e.sincronizando) pollearSync();
  });
})();
