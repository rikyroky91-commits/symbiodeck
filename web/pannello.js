/* Pannello PC: dati in streaming (Server-Sent Events) e comandi verso il server sul PC.
   JavaScript semplice, compatibile con il WebView 88 dello SHIELD Tablet. */
(function () {
  "use strict";

  var parametri = new URLSearchParams(location.search);
  var token = parametri.get("t") || localStorage.getItem("token") || "";
  if (parametri.get("t")) { try { localStorage.setItem("token", token); } catch (e) {} }
  var nativo = window.Pannello || null;  // interfaccia dell'app Android, se c'è

  var $ = function (id) { return document.getElementById(id); };
  var CIRC = 314.16;
  var STORIA = 120;
  var storia = { fps: [], cpu: [], gpu: [] };
  var config = { app: [], scorciatoie: [] };
  var modifica = false;

  function api(percorso, corpo) {
    var opz = { headers: { "X-Token": token } };
    if (corpo !== undefined) {
      opz.method = "POST";
      opz.headers["Content-Type"] = "application/json";
      opz.body = JSON.stringify(corpo);
    }
    return fetch(percorso, opz).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.errore || ("errore " + r.status));
        return j;
      });
    });
  }

  function avviso(testo) {
    var a = $("avviso");
    a.textContent = testo;
    a.classList.remove("nascosto");
    clearTimeout(avviso.t);
    avviso.t = setTimeout(function () { a.classList.add("nascosto"); }, 3500);
  }

  // ------------------------------------------------------------ orologio
  var GIORNI = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
  var MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];
  function orologio() {
    var d = new Date();
    $("ora").textContent = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    $("data").textContent = GIORNI[d.getDay()] + " " + d.getDate() + " " + MESI[d.getMonth()];
  }
  orologio();
  setInterval(orologio, 10000);

  // ------------------------------------------------------------ metriche
  function testo(id, v) { var e = $(id); if (e.textContent !== v) e.textContent = v; }
  function num(v, cifre) { return v === null || v === undefined ? "—" : (cifre ? v.toFixed(cifre) : String(Math.round(v))); }
  function classeTemp(id, t, caldo, rovente) {
    var e = $(id);
    e.className = "num" + (t === null || t === undefined ? "" : t >= rovente ? " rovente" : t >= caldo ? " caldo" : " fresco");
  }
  function anello(id, p) {
    var v = p === null || p === undefined ? 0 : Math.max(0, Math.min(100, p));
    $(id).style.strokeDashoffset = (CIRC * (1 - v / 100)).toFixed(1);
  }
  function aggiungi(serie, v) {
    serie.push(v === undefined ? null : v);
    if (serie.length > STORIA) serie.shift();
  }

  function mostra(d) {
    if (d && d.config && config.versione && d.config !== config.versione) caricaConfig();  // cambiata dal companion
    if (!d || !d.cpu) return;
    var c = d.cpu, g = d.gpu, r = d.ram, f = d.fps;
    testo("cpu-carico", num(c.carico));
    anello("cpu-anello", c.carico);
    testo("cpu-temp", c.temp === null ? "—" : num(c.temp) + "°");
    classeTemp("cpu-temp", c.temp, 75, 88);
    testo("cpu-watt", c.watt === null ? "—" : num(c.watt) + " W");
    testo("cpu-clock", c.clock ? (c.clock / 1000).toFixed(2) + " GHz" : "— GHz");
    if (r.usata !== null && r.totale) {
      testo("ram", (r.usata / 1024).toFixed(1) + " GB");
      testo("ram-tot", "RAM / " + Math.round(r.totale / 1024));
      $("ram-barra").style.width = (100 * r.usata / r.totale).toFixed(1) + "%";
    }

    testo("gpu-carico", num(g.carico));
    anello("gpu-anello", g.carico);
    testo("gpu-temp", g.temp === null ? "—" : num(g.temp) + "°");
    classeTemp("gpu-temp", g.temp, 72, 83);
    testo("gpu-watt", g.watt === null ? "—" : num(g.watt) + " W");
    testo("gpu-clock", g.clock ? num(g.clock) + " MHz" + (g.ventola !== null ? " · ventola " + num(g.ventola) + "%" : "") : "— MHz");
    if (g.vram !== null && g.vram_tot) {
      testo("vram", (g.vram / 1024).toFixed(1) + " GB");
      testo("vram-tot", "VRAM / " + Math.round(g.vram_tot / 1024));
      $("vram-barra").style.width = (100 * g.vram / g.vram_tot).toFixed(1) + "%";
    }

    var gioco = f.ora !== null;
    $("fps-ora").parentNode.parentNode.classList.toggle("vuoto", !gioco);
    testo("fps-ora", gioco ? num(f.ora) : "—");
    testo("fps-low", num(f.low1));
    testo("fps-media", num(f.media));
    var pp = d.primo_piano || {};
    var nomePP = pp.titolo || pp.processo || "—";
    testo("fps-gioco", gioco ? nomePP : (d.fonte === "base" ? "Afterburner chiuso: niente FPS" : "nessun gioco"));
    testo("pp-etichetta", gioco ? "In gioco" : "In primo piano");
    testo("pp-nome", nomePP);

    aggiungi(storia.fps, f.ora);
    aggiungi(storia.cpu, c.temp);
    aggiungi(storia.gpu, g.temp);
    disegna();
  }

  // ------------------------------------------------------------ grafico (canvas, una volta al secondo)
  var tela = $("grafico"), ctx = tela.getContext("2d");
  function adatta() {
    var r = tela.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    tela.width = Math.max(1, Math.round(r.width * dpr));
    tela.height = Math.max(1, Math.round(r.height * dpr));
    disegna();
  }
  function linea(serie, massimo, colore, spessore) {
    var w = tela.width, h = tela.height, passo = w / (STORIA - 1), inizio = STORIA - serie.length, primo = true;
    ctx.beginPath();
    for (var i = 0; i < serie.length; i++) {
      var v = serie[i];
      if (v === null || v === undefined) { primo = true; continue; }
      var x = (inizio + i) * passo, y = h - 4 - (Math.min(v, massimo) / massimo) * (h - 8);
      if (primo) { ctx.moveTo(x, y); primo = false; } else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = colore;
    ctx.lineWidth = spessore;
    ctx.stroke();
  }
  function disegna() {
    var w = tela.width, h = tela.height;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    for (var k = 1; k < 4; k++) { ctx.beginPath(); ctx.moveTo(0, h * k / 4); ctx.lineTo(w, h * k / 4); ctx.stroke(); }
    var maxFps = 60;
    storia.fps.forEach(function (v) { if (v !== null && v > maxFps) maxFps = v; });
    maxFps = Math.ceil(maxFps / 30) * 30;
    var dpr = window.devicePixelRatio || 1;
    linea(storia.cpu, 100, "rgba(255,106,61,0.75)", 1.5 * dpr);
    linea(storia.gpu, 100, "rgba(118,185,0,0.75)", 1.5 * dpr);
    linea(storia.fps, maxFps, "#35c8ff", 2.5 * dpr);
  }
  window.addEventListener("resize", adatta);
  setTimeout(adatta, 50);

  // ------------------------------------------------------------ collegamento
  var sorgente = null, ultimoDato = 0;
  function collega() {
    if (sorgente) sorgente.close();
    sorgente = new EventSource("/api/flusso?t=" + encodeURIComponent(token));
    sorgente.onmessage = function (e) {
      ultimoDato = Date.now();
      $("punto").classList.add("vivo");
      document.body.classList.remove("scollegato");
      try { mostra(JSON.parse(e.data)); } catch (err) { /* lettura incompleta: si aspetta la prossima */ }
    };
    sorgente.onerror = function () { $("punto").classList.remove("vivo"); };
  }
  // se il flusso tace (PC in sospensione, Wi-Fi caduto) lo si riapre
  var paginaAperta = Date.now(), ricaricata = Date.now();
  setInterval(function () {
    if (Date.now() - ultimoDato > 8000) {
      // niente dati: i valori a schermo sono vecchi, lo si dice chiaramente invece di lasciarli "fermi"
      $("punto").classList.remove("vivo");
      document.body.classList.add("scollegato");
      collega();
    }
    // La pagina parla solo con l'indirizzo da cui è stata caricata: dopo la sospensione del PC il collegamento
    // USB (adb) può restare bloccato mentre il Wi-Fi funziona. Dopo 20 s di silenzio si chiede all'app di
    // ricaricare il pannello provando tutti gli indirizzi (USB, poi rete), al massimo ogni 30 s e solo a schermo
    // acceso.
    var silenzio = Date.now() - Math.max(ultimoDato, paginaAperta);
    if (silenzio > 20000 && Date.now() - ricaricata > 30000 && !document.hidden &&
        window.Pannello && window.Pannello.riprova) {
      ricaricata = Date.now();
      window.Pannello.riprova();
    }
  }, 5000);

  // ------------------------------------------------------------ comandi
  var COLORI = ["#3b82f6", "#8b5cf6", "#ef4444", "#10b981", "#f59e0b", "#ec4899", "#14b8a6", "#6366f1"];
  function colore(nome) { var h = 0; for (var i = 0; i < nome.length; i++) h = (h * 31 + nome.charCodeAt(i)) >>> 0; return COLORI[h % COLORI.length]; }

  function lampeggia(el, ok) {
    el.classList.add(ok ? "lampo" : "errore");
    setTimeout(function () { el.classList.remove("lampo", "errore"); }, 350);
  }

  function costruisci() {
    var app = $("app"), sc = $("scorciatoie");
    app.innerHTML = ""; sc.innerHTML = "";
    var nApp = config.app.length + (modifica ? 1 : 0);
    var nSc = config.scorciatoie.length + (modifica ? 1 : 0);
    // in modifica c'è una colonna in più per il "+", così la griglia non va a capo
    var massimo = modifica ? 7 : 6;
    app.style.setProperty("--colonne", Math.max(3, Math.min(massimo, nApp)));
    sc.style.setProperty("--colonne", Math.max(3, Math.min(massimo, nSc)));

    config.app.forEach(function (a, i) {
      var b = document.createElement("button");
      b.className = "tessera";
      var img = document.createElement("img");
      img.alt = "";
      img.src = "/api/icona?t=" + encodeURIComponent(token) + "&target=" + encodeURIComponent(a.target);
      img.onerror = function () {
        var l = document.createElement("span");
        l.className = "lettera"; l.textContent = a.nome.charAt(0).toUpperCase();
        l.style.setProperty("--colore", colore(a.nome));
        img.replaceWith(l);
      };
      var n = document.createElement("span"); n.className = "nome-t"; n.textContent = a.nome;
      b.appendChild(img); b.appendChild(n);
      aggiungiVia(b, function () { config.app.splice(i, 1); salva({ app: config.app }); });
      b.onclick = function () {
        if (modifica) return scegliApp(i);
        api("/api/azione", { tipo: "app", id: a.id }).then(function () { lampeggia(b, true); },
          function (e) { lampeggia(b, false); avviso(e.message); });
      };
      app.appendChild(b);
    });
    if (modifica && config.app.length < 12) app.appendChild(tesseraAggiungi(function () { scegliApp(config.app.length); }));

    config.scorciatoie.forEach(function (s, i) {
      var b = document.createElement("button");
      b.className = "tessera";
      b.innerHTML = '<span class="ic"></span><span class="nome-t"><span class="n"></span><span class="tasti"></span></span>';
      b.querySelector(".ic").textContent = s.icona || "⌨️";
      b.querySelector(".n").textContent = s.nome;
      b.querySelector(".tasti").textContent = s.tasti;
      aggiungiVia(b, function () { config.scorciatoie.splice(i, 1); salva({ scorciatoie: config.scorciatoie }); });
      b.onclick = function () {
        if (modifica) return modificaTasti(i);
        api("/api/azione", { tipo: "scorciatoia", id: s.id }).then(function () { lampeggia(b, true); },
          function (e) { lampeggia(b, false); avviso(e.message); });
      };
      sc.appendChild(b);
    });
    if (modifica && config.scorciatoie.length < 12) sc.appendChild(tesseraAggiungi(function () { modificaTasti(config.scorciatoie.length); }));
    setTimeout(adatta, 0);
  }

  function aggiungiVia(b, fn) {
    var x = document.createElement("span");
    x.className = "via"; x.textContent = "✕";
    x.onclick = function (e) { e.stopPropagation(); fn(); };
    b.appendChild(x);
  }
  function tesseraAggiungi(fn) {
    var b = document.createElement("button");
    b.className = "tessera aggiungi"; b.textContent = "+";
    b.onclick = fn;
    return b;
  }

  function salva(parziale) {
    return api("/api/config", parziale).then(function (c) { config = c; costruisci(); },
      function (e) { avviso(e.message); caricaConfig(); });
  }
  function caricaConfig() {
    return api("/api/config").then(function (c) { config = c; costruisci(); }, function (e) { avviso("Config: " + e.message); });
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-media]"), function (b) {
    b.onclick = function () {
      api("/api/azione", { tipo: "media", tasto: b.getAttribute("data-media") }).catch(function (e) { avviso(e.message); });
    };
  });

  // ------------------------------------------------------------ secondo monitor
  function secondoMonitor() {
    // spento finché in Apollo il monitor virtuale non è impostato come schermo aggiuntivo (config: "monitor": true)
    if (!config.monitor) return avviso("Secondo monitor in preparazione: va prima sistemato Apollo sul PC");
    if (nativo && nativo.secondoMonitor) nativo.secondoMonitor();
    else avviso("Il secondo monitor si apre dall'app del tablet");
  }
  $("monitor").onclick = secondoMonitor;
  window.addEventListener("errore-nativo", function (e) { avviso(e.detail); });
  // scorrimento dal bordo destro verso sinistra (come per "tirare dentro" lo schermo del PC)
  (function () {
    var scia = document.createElement("div"); scia.id = "scia"; document.body.appendChild(scia);
    var x0 = null, y0 = 0;
    document.addEventListener("touchstart", function (e) {
      var t = e.touches[0];
      x0 = (!modifica && t.clientX > window.innerWidth - 40) ? t.clientX : null; y0 = t.clientY;
    }, { passive: true });
    document.addEventListener("touchmove", function (e) {
      if (x0 === null) return;
      scia.style.width = Math.max(0, x0 - e.touches[0].clientX) + "px";
    }, { passive: true });
    document.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var t = e.changedTouches[0], dx = x0 - t.clientX, dy = Math.abs(t.clientY - y0);
      scia.style.width = "0";
      x0 = null;
      if (dx > window.innerWidth * 0.25 && dy < 120) secondoMonitor();
    });
  })();

  // ------------------------------------------------------------ modalità modifica
  function impostaModifica(v) {
    modifica = v;
    document.body.classList.toggle("modifica", v);
    $("barra-modifica").classList.toggle("nascosto", !v);
    costruisci();
  }
  $("modifica").onclick = function () { impostaModifica(!modifica); };
  $("fine").onclick = function () { impostaModifica(false); };
  $("apri-android").onclick = function () {
    if (nativo && nativo.apriAndroid) nativo.apriAndroid(); else avviso("Disponibile solo nell'app del tablet");
  };
  $("luminosita").oninput = function () {
    try { localStorage.setItem("luce", this.value); } catch (e) {}
    if (nativo && nativo.luminosita) nativo.luminosita(Number(this.value) / 100);
  };
  (function () {
    var l = localStorage.getItem("luce");
    if (l) { $("luminosita").value = l; if (nativo && nativo.luminosita) nativo.luminosita(Number(l) / 100); }
  })();

  // ------------------------------------------------------------ tastiera
  function chiudiTastiera() {
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (nativo && nativo.chiudiTastiera) nativo.chiudiTastiera();
    document.body.classList.remove("tastiera");
  }
  document.addEventListener("focusin", function (e) {
    if (e.target.tagName === "INPUT" && e.target.type !== "range") document.body.classList.add("tastiera");
  });
  document.addEventListener("focusout", function () {
    setTimeout(function () {
      var a = document.activeElement;
      if (!a || a.tagName !== "INPUT") document.body.classList.remove("tastiera");
    }, 50);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); chiudiTastiera(); }
  });
  Array.prototype.forEach.call(document.querySelectorAll(".via-tastiera"), function (b) { b.onclick = chiudiTastiera; });

  function apriFinestra(id) {
    $("velo").classList.remove("nascosto");
    Array.prototype.forEach.call(document.querySelectorAll(".finestra"), function (f) { f.classList.toggle("aperta", f.id === id); });
  }
  function chiudiFinestre() { chiudiTastiera(); clearInterval(controlloCerca); $("velo").classList.add("nascosto"); }
  Array.prototype.forEach.call(document.querySelectorAll(".chiudi"), function (b) { b.onclick = chiudiFinestre; });

  // tasto di spegnimento: sospendi, iberna, riavvia o spegni (il server aspetta un attimo prima di farlo)
  var FRASI_ENERGIA = { sospendi: "Sospendo il PC…", iberna: "Iberno il PC…", riavvia: "Riavvio il PC…", spegni: "Spengo il PC…" };
  $("energia").onclick = function () { apriFinestra("finestra-energia"); };
  Array.prototype.forEach.call(document.querySelectorAll("[data-energia]"), function (b) {
    b.onclick = function () {
      var modo = b.getAttribute("data-energia");
      chiudiFinestre();
      api("/api/azione", { tipo: "energia", modo: modo }).then(function () { avviso(FRASI_ENERGIA[modo]); },
        function (e) { avviso(e.message); });
    };
  });

  var elencoApp = null, controlloCerca = null;
  function scegliApp(posizione) {
    apriFinestra("finestra-app");
    var cerca = $("cerca");
    cerca.value = "";
    $("elenco").innerHTML = "<div class=\"vuoto-elenco\">Carico l’elenco…</div>";
    var pronto = elencoApp ? Promise.resolve(elencoApp) : api("/api/elenco-app").then(function (l) { elencoApp = l; return l; });
    pronto.then(function (l) {
      var ultimo = null;
      function aggiorna() {
        if (cerca.value === ultimo) return;
        ultimo = cerca.value;
        riempiElenco(l, posizione);
      }
      aggiorna();
      // il WebView del tablet non sempre manda "input" mentre si scrive: si controlla anche il valore
      cerca.oninput = cerca.onkeyup = cerca.onchange = aggiorna;
      clearInterval(controlloCerca);
      controlloCerca = setInterval(aggiorna, 300);
    }, function (e) { avviso(e.message); });
  }
  function normalizza(s) {
    return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  }
  function riempiElenco(lista, posizione) {
    var q = normalizza($("cerca").value), box = $("elenco");
    var parole = q ? q.split(" ") : [];
    box.innerHTML = "";
    var trovate = lista.filter(function (a) {
      var n = normalizza(a.nome);
      return parole.every(function (p) { return n.indexOf(p) >= 0; });
    });
    if (!trovate.length) { box.innerHTML = '<div class="vuoto-elenco">Nessun risultato per “' + $("cerca").value.replace(/[<&]/g, "") + '”</div>'; return; }
    trovate.slice(0, 90).forEach(function (a) {
      var b = document.createElement("button");
      var img = document.createElement("img");
      img.loading = "lazy"; img.alt = "";
      img.src = "/api/icona?t=" + encodeURIComponent(token) + "&target=" + encodeURIComponent(a.target);
      img.onerror = function () { img.style.visibility = "hidden"; };
      var n = document.createElement("span"); n.textContent = a.nome;
      b.appendChild(img); b.appendChild(n);
      b.onclick = function () {
        var nuove = config.app.slice();
        nuove[posizione] = { nome: a.nome, target: a.target };
        chiudiFinestre();
        salva({ app: nuove });
      };
      box.appendChild(b);
    });
  }

  var ICONE = ["⌨️", "📷", "🎬", "🎙️", "🔇", "🖥️", "📊", "🎯", "🎮", "🔴", "⏺️", "💬", "🌙", "🔒", "📁", "🌐", "⚡", "🎧"];
  function modificaTasti(posizione) {
    var s = config.scorciatoie[posizione] || { nome: "", tasti: "", icona: "⌨️" };
    var scelta = s.icona;
    $("t-nome").value = s.nome;
    $("t-tasti").value = s.tasti;
    var box = $("t-icone");
    box.innerHTML = "";
    ICONE.forEach(function (ic) {
      var b = document.createElement("button");
      b.textContent = ic;
      if (ic === scelta) b.className = "scelta";
      b.onclick = function () {
        scelta = ic;
        Array.prototype.forEach.call(box.children, function (c) { c.className = c === b ? "scelta" : ""; });
      };
      box.appendChild(b);
    });
    $("t-salva").onclick = function () {
      var nuove = config.scorciatoie.slice();
      nuove[posizione] = { nome: $("t-nome").value.trim(), tasti: $("t-tasti").value.trim().toLowerCase(), icona: scelta };
      chiudiFinestre();
      salva({ scorciatoie: nuove });
    };
    apriFinestra("finestra-tasti");
  }

  // ------------------------------------------------------------ avvio
  if (!token) avviso("Manca il token: apri il pannello dall'app o dal link del PC");
  caricaConfig();
  collega();
  // se la config non è arrivata (PC spento all'avvio) la si richiede finché arriva
  setInterval(function () { if (!config.app.length && !config.scorciatoie.length) caricaConfig(); }, 10000);
})();
