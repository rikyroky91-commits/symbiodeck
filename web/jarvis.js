/* Pannello di Jarvis sul tablet: scorrimento dal bordo sinistro verso destra.
   Parla con server/jarvis sul PC (porta 47802): flusso di eventi (SSE), impostazioni, comandi scritti.
   JavaScript semplice, come pannello.js (WebView 88 dello SHIELD Tablet). */
(function () {
  "use strict";

  var parametri = new URLSearchParams(location.search);
  var token = parametri.get("t") || localStorage.getItem("token") || "";
  var nativo = window.Pannello || null;
  var BASE = location.protocol + "//" + location.hostname + ":47802";
  var NOMI_STATO = { spento: "Spento", riposo: "In attesa", ascolto: "Ti ascolto", penso: "Elaboro",
    lavoro: "Al lavoro", parlo: "Parlo", conferma: "Conferma", seguito: "Ti ascolto" };

  var stato = "riposo", imp = {}, livello = 0, livelloVisto = 0, aperto = false, collegato = false;
  // sentinella del tablet attiva: la sfera diventa un allarme (rosso, a sirena) finché non si spegne
  var allarme = { attiva: false, armata: false };
  function impostaAllarme(a) {
    allarme = a || { attiva: false, armata: false };
    radice.classList.toggle("allarme", !!allarme.attiva);
    linguetta.classList.toggle("allarme", !!allarme.attiva);
    impostaStato(stato);
  }
  var corrente = null, confermaId = null, dispositivi = null, sottofondo = false, emergenza = false;
  var promemoria = [];

  // ------------------------------------------------------------ struttura
  var radice = document.createElement("div");
  radice.id = "jarvis";
  radice.innerHTML =
    '<div class="j-testa">' +
      '<div class="j-logo">JARVIS<small>ASSISTENTE VOCALE DEL PC</small></div>' +
      '<span class="j-chip" id="j-chip-pc">PC</span>' +
      '<span class="j-chip" id="j-chip-mic">MIC</span>' +
      '<span class="j-chip" id="j-chip-modo">CLAUDE CODE</span>' +
      '<span class="j-spazio"></span>' +
      '<button class="j-btn j-chiudi" id="j-chiudi" aria-label="Chiudi">✕</button>' +
    '</div>' +
    '<div class="j-corpo">' +
      '<div class="j-hud">' +
        '<canvas id="j-tela" width="380" height="380"></canvas>' +
        '<div class="j-stato" id="j-stato">—</div>' +
        '<div class="j-azione" id="j-azione"></div>' +
        '<div class="j-comandi">' +
          '<button class="j-btn forte" id="j-parla">◉ Parla</button>' +
          '<button class="j-btn" id="j-taci">■ Stop</button>' +
          '<button class="j-btn" id="j-nuova">↺ Nuova</button>' +
          '<button class="j-btn" id="j-app" title="Continua questa conversazione nell&#39;app Claude sul PC">⧉ Sul PC</button>' +
        '</div>' +
      '</div>' +
      '<div class="j-lato">' +
        '<div class="j-schede"><button class="attiva" data-pagina="j-p-chat">Conversazione</button>' +
          '<button data-pagina="j-p-imp">Impostazioni</button></div>' +
        '<div class="j-pagina attiva" id="j-p-chat">' +
          '<div class="j-chat" id="j-chat"><div class="j-vuoto" id="j-vuoto">Di\' <b>"Ehi Jarvis"</b> vicino al tablet,<br>' +
            'tocca <b>Parla</b> oppure scrivi qui sotto.</div></div>' +
          '<form class="j-scrivi" id="j-form"><input id="j-testo" type="text" autocomplete="off" ' +
            'placeholder="Scrivi a Jarvis…" enterkeyhint="send"><button class="j-btn forte" type="submit">Invia</button></form>' +
        '</div>' +
        '<div class="j-pagina" id="j-p-imp"><div class="j-impostazioni" id="j-imp"></div></div>' +
      '</div>' +
    '</div>' +
    // autorizzazioni e domande: a tutto schermo, pulsanti grandi per un tocco preciso
    '<div class="j-conferma" id="j-conferma">' +
      '<div class="j-c-alone"></div>' +
      '<div class="j-c-testa"><span class="j-c-anello"></span><span class="j-titolo" id="j-conferma-titolo">' +
        'AUTORIZZAZIONE RICHIESTA</span><span class="j-c-badge" id="j-conferma-badge">AZIONE DELICATA · SOLO TOCCO</span></div>' +
      '<p class="j-c-testo" id="j-conferma-testo"></p>' +
      '<p class="j-c-dettaglio" id="j-conferma-dettaglio"></p>' +
      '<div class="j-c-tempo"><i id="j-conferma-tempo"></i></div>' +
      '<div class="j-c-pulsanti" id="j-conferma-pulsanti">' +
        '<button class="j-c-btn si" id="j-si"><b>SÌ</b><small>procedi</small></button>' +
        '<button class="j-c-btn no" id="j-no"><b>NO</b><small>lascia stare</small></button>' +
      '</div>' +
      '<div class="j-c-secondari" id="j-conferma-secondari">' +
        '<button class="j-c-btn piccolo" id="j-sempre">Sì, sempre<small>solo questo comando esatto</small></button>' +
        '<button class="j-c-btn piccolo" id="j-mai">No, mai<small>non chiederlo più</small></button>' +
      '</div>' +
      '<div class="j-c-opzioni" id="j-conferma-opzioni"></div>' +
      '<div class="j-c-voce" id="j-conferma-voce">puoi rispondere anche a voce</div>' +
    '</div>' +
    '<div class="j-avviso" id="j-avviso"></div>' +
    '<div class="j-dettatura" id="j-dettatura">DETTATURA · scrivo nella finestra del PC · di\' <b>fine dettatura</b></div>';
  document.body.appendChild(radice);
  var linguetta = document.createElement("div");
  linguetta.id = "jarvis-linguetta";
  linguetta.className = "assente";
  document.body.appendChild(linguetta);
  var scia = document.createElement("div");
  scia.id = "scia-jarvis";
  document.body.appendChild(scia);
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------ rete
  function api(percorso, corpo) {
    var opz = { headers: { "X-Token": token } };
    if (corpo !== undefined) {
      opz.method = "POST";
      opz.headers["Content-Type"] = "application/json";
      opz.body = JSON.stringify(corpo);
    }
    return fetch(BASE + percorso, opz).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.errore || ("errore " + r.status));
        return j;
      });
    });
  }
  function avviso(t) { window.dispatchEvent(new CustomEvent("errore-nativo", { detail: t })); }

  var sorgente = null;
  function collega() {
    if (sorgente) sorgente.close();
    sorgente = new EventSource(BASE + "/jarvis/flusso?t=" + encodeURIComponent(token));
    sorgente.onmessage = function (e) {
      collegato = true;
      try { evento(JSON.parse(e.data)); } catch (err) { /* evento non valido */ }
    };
    sorgente.onerror = function () {
      collegato = false;
      linguetta.className = "assente";
      chip("j-chip-pc", "PC ✕", false);
    };
  }

  // ------------------------------------------------------------ eventi dal PC
  function evento(d) {
    if (d.tutto) {
      var s = d.tutto;
      imp = s.impostazioni || {};
      sottofondo = !!s.sottofondo;
      emergenza = !!(s.emergenza && s.emergenza.attiva);
      promemoria = s.promemoria || [];
      impostaStato(s.stato);
      impostaAllarme(s.allarme);
      mostraConferma(s.conferma);
      $("j-azione").textContent = s.azione || "";
      chip("j-chip-pc", s.claude ? "PC ✓" : "CLAUDE ✕", !!s.claude);
      aggiornaChip(s.tablet);
      disegnaImpostazioni();
      return;
    }
    if (d.sottofondo !== undefined) { sottofondo = !!d.sottofondo; impostaStato(stato); }
    if (d.stato) impostaStato(d.stato);
    if (d.allarme) impostaAllarme(d.allarme);
    if (d.livello !== undefined) livello = d.livello;
    if (d.utente) { chiudiCorrente(); messaggio("utente", d.utente); }
    if (d.jarvis_pezzo) {
      if (!corrente) corrente = messaggio("jarvis", "");
      corrente.textContent += d.jarvis_pezzo;
      scorri();
    }
    if (d.jarvis) {
      if (corrente) corrente.textContent = d.jarvis; else messaggio("jarvis", d.jarvis);
      corrente = null;
      scorri();
    }
    if (d.fine) corrente = null;
    if (d.azione) { chiudiCorrente(); messaggio("azione", d.azione); $("j-azione").textContent = d.azione; }
    if (d.errore) messaggio("errore", d.errore);
    if (d.conferma !== undefined) mostraConferma(d.conferma);
    if (d.impostazioni) { imp = d.impostazioni; disegnaImpostazioni(); }
    if (d.nuova) { $("j-chat").innerHTML = ""; corrente = null; }
    if (d.emergenza !== undefined && d.emergenza !== null) {
      emergenza = !!d.emergenza.attiva;
      messaggio("azione", emergenza ? "Modalità emergenza: " + d.emergenza.motivo + " (cervello locale)" : "Torno a Claude");
      aggiornaChip();
    }
    if (d.gioco !== undefined) {
      messaggio("azione", d.gioco ? "Modalità gioco: scheda video libera, ascolto con il processore" : "Fine partita: torno a usare la scheda video");
    }
    if (d.promemoria) { promemoria = d.promemoria; disegnaPromemoria(); }
    if (d.avviso) { chiudiCorrente(); messaggio("azione", "⚠ " + d.avviso); mostraAvviso(d.avviso); }
    if (d.dettatura !== undefined) $("j-dettatura").classList.toggle("vista", !!d.dettatura);
    if (d.nell_app) { messaggio("azione", "Conversazione visibile nell'app Claude sul PC (progetto pc-panel)"); corrente = null; }
  }

  var cambioStato = 0;
  function impostaStato(s) {
    if ((s || "riposo") !== stato) cambioStato = Date.now();
    stato = s || "riposo";
    radice.setAttribute("data-stato", stato);
    linguetta.setAttribute("data-stato", stato);
    linguetta.className = allarme.attiva ? "allarme" : "";
    $("j-stato").textContent = allarme.attiva && (stato === "riposo" || stato === "seguito")
      ? (allarme.armata ? "Sentinella: sorveglio" : "Sentinella: si attiva…")
      : (stato === "riposo" || stato === "seguito") && sottofondo ? "Lavoro in corso" : (NOMI_STATO[stato] || stato);
    if (stato === "riposo" || stato === "spento") $("j-azione").textContent = "";
    if (stato === "conferma" && !aperto && nativo) apri();
  }

  function chip(id, testo, ok) {
    var c = $(id);
    if (c.textContent !== testo) { c.classList.remove("lampo"); void c.offsetWidth; setTimeout(function () { c.classList.add("lampo"); }, 0); }
    c.textContent = testo;
    c.className = "j-chip " + (ok === undefined ? "" : ok ? "ok" : "no") + " lampo";
  }
  var ultimoTablet = null;
  function aggiornaChip(tablet) {
    if (tablet) ultimoTablet = tablet; else tablet = ultimoTablet;
    var mic = imp.microfono === "pc" ? "MIC PC" : "MIC TABLET";
    var micOk = imp.microfono === "pc" ? true : (tablet ? tablet.microfono : false);
    if (nativo && nativo.microfono && imp.microfono !== "pc" && !nativo.microfono()) { mic = "MIC: PERMESSO"; micOk = false; }
    chip("j-chip-mic", mic + (micOk ? " ✓" : " ✕"), micOk);
    radice.classList.toggle("privata", !!imp.privata);
    if (imp.privata) chip("j-chip-modo", emergenza ? "PRIVATA · SOLO PC" : "PRIVATA · CERVELLO SPENTO", !!emergenza);
    else if (emergenza) chip("j-chip-modo", "EMERGENZA · CERVELLO LOCALE", false);
    else chip("j-chip-modo", "VOCE " + String(imp.modello_voce || "").toUpperCase() + " · COMPITI " +
      String(imp.modello_compiti || "").toUpperCase());
  }
  setInterval(function () {
    if (aperto && collegato) api("/jarvis/stato").then(function (s) { aggiornaChip(s.tablet); }, function () {});
  }, 5000);

  function chiudiCorrente() { corrente = null; }
  function messaggio(tipo, testo) {
    var v = $("j-vuoto");
    if (v) v.parentNode.removeChild(v);
    var m = document.createElement("div");
    m.className = "j-msg " + tipo;
    m.textContent = testo;
    var chat = $("j-chat");
    chat.appendChild(m);
    while (chat.children.length > 80) chat.removeChild(chat.firstChild);
    scorri();
    return m;
  }
  function scorri() { var c = $("j-chat"); c.scrollTop = c.scrollHeight; }

  var confermaAperta = 0, tempoConferma = null;
  function mostraConferma(c) {
    var box = $("j-conferma");
    if (c && c.id === confermaId) return;          // già mostrata (arriva anche nell'istantanea)
    confermaId = c ? c.id : null;
    clearInterval(tempoConferma);
    if (!c) { box.classList.remove("vista"); return; }
    if (!aperto) apri();
    // cosa (prima dei due punti) grande, il comando sotto in piccolo
    var parti = String(c.descrizione || "").split(/:(.+)/);
    var domanda = !!c.domanda;
    $("j-conferma-titolo").textContent = domanda ? "JARVIS TI CHIEDE" : "AUTORIZZAZIONE RICHIESTA";
    $("j-conferma-testo").textContent = domanda ? c.descrizione : "Devo " + parti[0].trim() + ". Procedo?";
    $("j-conferma-dettaglio").textContent = domanda ? "" : (parti[1] || "").trim();
    $("j-conferma-badge").style.display = c.alto ? "" : "none";
    $("j-conferma-voce").textContent = c.alto ? "a voce puoi solo dire no: per il sì serve il tocco" :
      "puoi rispondere anche a voce";
    var opz = $("j-conferma-opzioni");
    opz.innerHTML = "";
    $("j-conferma-pulsanti").style.display = domanda && c.opzioni && c.opzioni.length ? "none" : "";
    $("j-conferma-secondari").style.display = domanda ? "none" : "";
    // "sì, sempre" solo dove la guardia lo ammette (stesso comando esatto, mai per dischi, registro, messaggi...)
    $("j-sempre").style.display = c.sempre_ok ? "" : "none";
    if (domanda) (c.opzioni || []).forEach(function (o) {
      var b = document.createElement("button");
      b.className = "j-c-btn opzione"; b.textContent = o;
      b.onclick = function () { rispondi(true, false, o); };
      opz.appendChild(b);
    });
    box.classList.toggle("alto", !!c.alto);
    box.classList.remove("vista"); void box.offsetWidth; box.classList.add("vista");
    // i pulsanti si attivano dopo un attimo: un tocco partito prima non conferma per sbaglio
    confermaAperta = Date.now();
    var scade = c.scade ? c.scade * 1000 : Date.now() + 45000, durata = Math.max(1000, scade - Date.now());
    var barra = $("j-conferma-tempo");
    tempoConferma = setInterval(function () {
      barra.style.width = Math.max(0, (scade - Date.now()) / durata * 100) + "%";
    }, 200);
  }
  function rispondi(ok, sempre, testo) {
    if (!confermaId || Date.now() - confermaAperta < 450) return;
    var corpo = { id: confermaId, ok: ok, sempre: !!sempre };
    if (testo !== undefined) corpo.testo = testo;
    api("/jarvis/conferma", corpo).catch(function (e) { avviso(e.message); });
    var box = $("j-conferma");
    box.classList.add(ok ? "esito-si" : "esito-no");
    setTimeout(function () { box.classList.remove("esito-si", "esito-no"); mostraConferma(null); }, 380);
  }
  var timerAvviso = null;
  function mostraAvviso(testo) {
    var a = $("j-avviso");
    a.textContent = testo;
    a.classList.remove("visto"); void a.offsetWidth; a.classList.add("visto");
    clearTimeout(timerAvviso);
    timerAvviso = setTimeout(function () { a.classList.remove("visto"); }, 9000);
  }
  $("j-avviso").onclick = function () { this.classList.remove("visto"); };
  $("j-si").onclick = function () { rispondi(true); };
  $("j-no").onclick = function () { rispondi(false); };
  $("j-sempre").onclick = function () { rispondi(true, true); };
  $("j-mai").onclick = function () { rispondi(false, true); };

  // ------------------------------------------------------------ comandi
  $("j-parla").onclick = function () { api("/jarvis/tocca", {}).catch(function (e) { avviso(e.message); }); };
  $("j-taci").onclick = function () { api("/jarvis/taci", {}).catch(function (e) { avviso(e.message); }); };
  $("j-nuova").onclick = function () {
    api("/jarvis/nuova", {}).then(function () { avviso("Nuova conversazione"); }, function (e) { avviso(e.message); });
  };
  function apriNellApp(id) {
    api("/jarvis/apri-app", id ? { id: id } : {}).then(function () {
      avviso("Aperta nell'app Claude sul PC");
    }, function (e) { avviso(e.message); });
  }
  $("j-app").onclick = function () { apriNellApp(null); };
  $("j-form").onsubmit = function (e) {
    e.preventDefault();
    var t = $("j-testo").value.trim();
    if (!t) return;
    $("j-testo").value = "";
    api("/jarvis/scrivi", { testo: t }).catch(function (err) { avviso(err.message); });
    if (nativo && nativo.chiudiTastiera) nativo.chiudiTastiera();
  };
  Array.prototype.forEach.call(radice.querySelectorAll(".j-schede button"), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(radice.querySelectorAll(".j-schede button"), function (x) { x.classList.toggle("attiva", x === b); });
      Array.prototype.forEach.call(radice.querySelectorAll(".j-pagina"), function (p) {
        p.classList.toggle("attiva", p.id === b.getAttribute("data-pagina"));
      });
      if (b.getAttribute("data-pagina") === "j-p-imp") caricaDispositivi();
    };
  });

  // ------------------------------------------------------------ impostazioni
  function cambia(k, v) {
    var d = {}; d[k] = v;
    imp[k] = v;
    disegnaImpostazioni();
    api("/jarvis/impostazioni", d).then(function (s) { imp = s.impostazioni; disegnaImpostazioni(); aggiornaChip(s.tablet); },
      function (e) { avviso(e.message); });
  }
  function caricaDispositivi() {
    api("/jarvis/dispositivi").then(function (d) { dispositivi = d; disegnaImpostazioni(); }, function () {});
  }

  var VOCI = [["it-IT-DiegoNeural", "Diego (uomo)"], ["it-IT-GiuseppeMultilingualNeural", "Giuseppe (uomo)"],
    ["it-IT-ElsaNeural", "Elsa (donna)"], ["it-IT-IsabellaNeural", "Isabella (donna)"]];

  function riga(titolo, nota, controllo) {
    var r = document.createElement("div");
    r.className = "j-riga";
    var t = document.createElement("div");
    t.innerHTML = titolo + (nota ? '<span class="j-nota">' + nota + "</span>" : "");
    r.appendChild(t);
    r.appendChild(controllo);
    return r;
  }
  function gruppo(contenitore, nome) {
    var g = document.createElement("div");
    g.className = "j-gruppo";
    g.textContent = nome;
    contenitore.appendChild(g);
  }
  function scelte(k, opzioni) {
    var s = document.createElement("div");
    s.className = "j-scelte";
    opzioni.forEach(function (o) {
      var b = document.createElement("button");
      b.textContent = o[1];
      if (imp[k] === o[0]) b.className = "si";
      b.onclick = function () { cambia(k, o[0]); };
      s.appendChild(b);
    });
    return s;
  }
  function interruttore(k) {
    var b = document.createElement("button");
    b.className = "j-interruttore" + (imp[k] ? " si" : "");
    b.onclick = function () { cambia(k, !imp[k]); };
    return b;
  }
  function cursore(k, min, max, passo, formato) {
    var box = document.createElement("div");
    box.style.display = "flex"; box.style.alignItems = "center"; box.style.gap = "8px";
    var r = document.createElement("input");
    r.type = "range"; r.min = min; r.max = max; r.step = passo; r.value = imp[k];
    var v = document.createElement("span");
    v.className = "j-valore";
    v.textContent = formato(imp[k]);
    r.oninput = function () { v.textContent = formato(Number(r.value)); };
    r.onchange = function () { cambia(k, Number(r.value)); };
    box.appendChild(r); box.appendChild(v);
    return box;
  }
  function campo(k, suggerimento) {
    var i = document.createElement("input");
    i.type = "text"; i.value = imp[k] || ""; i.placeholder = suggerimento || "";
    i.onchange = function () { cambia(k, i.value.trim()); };
    return i;
  }
  function tendina(k, opzioni) {
    var s = document.createElement("select");
    opzioni.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o[0]; op.textContent = o[1];
      if (String(imp[k]) === String(o[0])) op.selected = true;
      s.appendChild(op);
    });
    s.onchange = function () { cambia(k, s.value); };
    return s;
  }
  function pulsante(testo, fn) {
    var b = document.createElement("button");
    b.className = "j-btn"; b.textContent = testo; b.onclick = fn;
    return b;
  }

  function caricaConversazioni() {
    api("/jarvis/conversazioni").then(function (d) {
      var box = $("j-conversazioni");
      if (!box) return;
      box.innerHTML = "";
      d.conversazioni.slice(0, 10).forEach(function (cv) {
        var quando = new Date(cv.ultimo * 1000);
        var nota = quando.toLocaleDateString("it-IT", { day: "numeric", month: "short" }) + " " +
          quando.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) + (cv.id === d.attuale ? " · in corso" : "");
        box.appendChild(riga(cv.titolo || "(senza titolo)", nota, pulsante("⧉ Apri sul PC", function () { apriNellApp(cv.id); })));
      });
    }, function () {});
  }

  function disegnaPromemoria() {
    var box = $("j-promemoria");
    if (!box) return;
    box.innerHTML = "";
    if (!promemoria.length) box.appendChild(riga("Nessun promemoria", "\"ricordami tra 20 minuti di…\", \"metti un timer di 5 minuti\"",
      document.createElement("span")));
    promemoria.forEach(function (p) {
      var t = document.createElement("span"); t.textContent = p.testo;
      var r = riga("", p.ora, pulsante("✕", function () {
        api("/jarvis/promemoria", { togli: p.id }).then(function (d) { promemoria = d.promemoria; disegnaPromemoria(); });
      }));
      r.firstChild.insertBefore(t, r.firstChild.firstChild);
      box.appendChild(r);
    });
  }

  function caricaMemoria(d) {
    if (!d) return api("/jarvis/memoria").then(caricaMemoria, function () {});
    var box = $("j-memoria");
    if (!box) return;
    box.innerHTML = "";
    var togli = function (corpo) { return function () { api("/jarvis/memoria", corpo).then(caricaMemoria); }; };
    if (!d.fatti.length) box.appendChild(riga("Nessun fatto ricordato", "si riempie da sola parlando, o di' \"ricordati che…\"",
      document.createElement("span")));
    d.fatti.forEach(function (f) {
      var nota = f.fiducia === "utente" ? "detto da te" : f.fiducia === "auto" ? "annotato da Jarvis"
        : "in quarantena, non usato (" + (f.motivo || "sospetto") + ")";
      var t = document.createElement("span"); t.textContent = f.testo;   // testo: mai come HTML
      var r = riga("", nota, pulsante("✕", togli({ togli: f.id })));
      r.firstChild.insertBefore(t, r.firstChild.firstChild);
      box.appendChild(r);
    });
    if (d.fatti.length) box.appendChild(riga("Dimentica tutto", "", pulsante("Svuota la memoria", togli({ azzera: true }))));
    d.permessi.forEach(function (p) {
      var t = document.createElement("span");
      t.textContent = (p.decisione === "sempre_si" ? "Sì, sempre: " : "No, mai: ") + p.descrizione;
      var r = riga("", "deciso il " + p.quando, pulsante("✕", togli({ togli_permesso: p.impronta })));
      r.firstChild.insertBefore(t, r.firstChild.firstChild);
      box.appendChild(r);
    });
  }

  function disegnaImpostazioni() {
    var c = $("j-imp");
    if (!c || imp.attivo === undefined) return;
    var su = c.scrollTop;
    c.innerHTML = "";
    gruppo(c, "Generale");
    c.appendChild(riga("Jarvis attivo", "spento: non ascolta e non risponde", interruttore("attivo")));
    var MODELLI = [["opus", "Opus"], ["sonnet", "Sonnet"], ["haiku", "Haiku"]];
    var LIVELLI = [["low", "Basso"], ["medium", "Medio"], ["high", "Alto"], ["xhigh", "Molto"], ["max", "Max"]];
    gruppo(c, "Voce: chi ti risponde");
    c.appendChild(riga("Modello della voce", "risponde subito e conversa · Sonnet o Haiku per la velocità",
      scelte("modello_voce", MODELLI)));
    c.appendChild(riga("Ragionamento della voce", "basso: risposte più rapide", scelte("effort_voce", LIVELLI)));
    gruppo(c, "Compiti: chi lavora sul PC");
    c.appendChild(riga("Modello dei compiti", "esegue tutto sul PC · Opus: come Claude Code nell'app",
      scelte("modello_compiti", MODELLI)));
    c.appendChild(riga("Ragionamento dei compiti", "più alto: più accurato ma più lento", scelte("effort_compiti", LIVELLI)));
    c.appendChild(riga("Interrompibile", "mentre parla ti ascolta: se gli parli sopra si ferma", interruttore("interrompibile")));
    c.appendChild(riga("Cancellazione dell'eco sul PC", "toglie la sua voce dal microfono: puoi interromperlo o dire «Hey Jarvis» mentre parla", interruttore("aec_pc")));
    c.appendChild(riga("Cancellazione dell'eco del tablet", "quella di Android, se il tablet ce l'ha (lo SHIELD no)", interruttore("eco")));
    c.appendChild(riga("Conversazioni nell'app Claude", "ogni conversazione compare subito nell'app del PC, progetto pc-panel",
      interruttore("consegna_app")));
    c.appendChild(riga("Aggiornamenti mentre lavora", "se l'esecutore lavora in silenzio, ogni tanto ti dice a che punto è",
      interruttore("aggiornamenti")));
    if (imp.aggiornamenti) c.appendChild(riga("Ogni quanto", "secondi di silenzio prima di un aggiornamento",
      cursore("aggiornamenti_ogni", 15, 120, 5, function (v) { return v + " s"; })));
    c.appendChild(riga("Claude in Chrome", "naviga e usa i siti nel tuo Chrome", interruttore("chrome")));
    c.appendChild(riga("Conferme", "quando chiedere \"Confermi?\" prima di agire",
      scelte("conferme", [["irreversibili", "Irreversibili"], ["sempre", "Sempre"], ["mai", "Mai"]])));

    gruppo(c, "Audio");
    c.appendChild(riga("Microfono", "dove ascolta \"Ehi Jarvis\"", scelte("microfono", [["tablet", "Tablet"], ["pc", "PC"]])));
    if (imp.microfono === "pc") c.appendChild(riga("Microfono del PC", "", tendina("microfono_pc",
      [["", "Predefinito"]].concat(((dispositivi || {}).ingressi || []).map(function (d) { return [d.nome, d.nome]; })))));
    if (imp.microfono !== "pc" && nativo && nativo.microfono && !nativo.microfono())
      c.appendChild(riga("Permesso del microfono", "il tablet non l'ha ancora concesso",
        pulsante("Concedi", function () { nativo.chiediMicrofono(); })));
    c.appendChild(riga("Casse", "da dove parla Jarvis", scelte("casse", [["tablet", "Tablet"], ["pc", "PC"]])));
    if (imp.casse === "pc") c.appendChild(riga("Casse del PC", "", tendina("casse_pc",
      [["", "Predefinite"]].concat(((dispositivi || {}).uscite || []).map(function (d) { return [d.nome, d.nome]; })))));
    c.appendChild(riga("Volume", "", cursore("volume", 0, 150, 5, function (v) { return v + "%"; })));
    c.appendChild(riga("Sensibilità", "più a destra: si attiva più facilmente (e per sbaglio)",
      cursore("sensibilita", 0.2, 0.9, 0.05, function (v) { return Math.round((1 - v) * 100) + "%"; })));
    // la sensibilità mostrata è l'inverso della soglia: si gira il cursore
    var sens = c.querySelectorAll("input[type=range]");
    var ultimo = sens[sens.length - 1];
    ultimo.value = 1.1 - imp.sensibilita;
    ultimo.oninput = function () { ultimo.nextSibling.textContent = Math.round((1 - (1.1 - ultimo.value)) * 100) + "%"; };
    ultimo.onchange = function () { cambia("sensibilita", Math.round((1.1 - Number(ultimo.value)) * 100) / 100); };
    c.appendChild(riga("Pausa per finire la frase", "quanto silenzio aspetta prima di considerare finita la frase",
      cursore("pausa", 0.6, 3, 0.1, function (v) { return v.toFixed(1) + " s"; })));
    c.appendChild(riga("Ascolto dopo la risposta", "secondi in cui puoi continuare senza ridire \"Ehi Jarvis\"",
      cursore("seguito", 0, 20, 1, function (v) { return v + " s"; })));
    c.appendChild(riga("Accendi lo schermo", "quando lo chiami a tablet spento", interruttore("accendi_schermo")));

    gruppo(c, "Voce");
    c.appendChild(riga("Voce", "", tendina("voce", VOCI)));
    c.appendChild(riga("Velocità", "", cursore("velocita", -20, 40, 5, function (v) { return (v > 0 ? "+" : "") + v + "%"; })));
    c.appendChild(riga("Tono", "", cursore("tono", -15, 15, 1, function (v) { return (v > 0 ? "+" : "") + v + " Hz"; })));
    c.appendChild(riga("Prova", "", pulsante("▶ Prova la voce", function () { api("/jarvis/prova-voce", {}); })));
    c.appendChild(riga("Riconoscimento vocale", "auto: sulla scheda video se c'è",
      tendina("stt", [["auto", "Automatico"], ["small", "Piccolo (veloce)"], ["medium", "Medio"],
        ["large-v3-turbo", "Grande turbo"], ["large-v3", "Grande (lento)"]])));

    gruppo(c, "Emergenza");
    c.appendChild(riga("Modalità emergenza", "senza Internet o fuori dai limiti di Claude usa il modello locale sulla GPU",
      scelte("emergenza", [["auto", "Automatica"], ["sempre", "Sempre"], ["mai", "Mai"]])));
    c.appendChild(riga("Modalità gioco", "quando giochi lascia libera la scheda video e non accende il modello locale",
      interruttore("modalita_gioco")));
    c.appendChild(riga("Modello locale", "27B: più memoria per i compiti · MTP: il doppio più veloce, meno memoria",
      scelte("modello_emergenza", [["27b", "27B"], ["27b-mtp", "27B MTP"]])));
    gruppo(c, "Privacy e sicurezza");
    c.appendChild(riga("Modalità privata", "tutto resta su questo PC: cervello locale, voce Piper, niente Claude " +
      "né Internet · anche a voce: \"modalità privata\" / \"esci dalla modalità privata\"", interruttore("privata")));
    c.appendChild(riga("Azioni delicate", "dischi, registro, sicurezza, servizi: tocco = il sì vale solo dal tablet",
      scelte("conferma_alta", [["tocco", "Solo tocco"], ["voce", "Anche voce"]])));
    c.appendChild(riga("Avvisi di temperatura", "dice se processore (" + imp.soglia_cpu + " °C) o scheda video (" +
      imp.soglia_gpu + " °C) scaldano troppo per mezzo minuto", interruttore("avvisi_temperatura")));
    c.appendChild(riga("Città per il meteo", "nel briefing del mattino (\"buongiorno\"); mai in modalità privata",
      campo("citta", "es. Catania")));
    gruppo(c, "Promemoria");
    var pm = document.createElement("div");
    pm.id = "j-promemoria";
    c.appendChild(pm);
    disegnaPromemoria();
    gruppo(c, "Memoria");
    c.appendChild(riga("Memoria a lungo termine", "ricorda preferenze e fatti stabili tra una conversazione e l'altra",
      interruttore("memoria")));
    var mem = document.createElement("div");
    mem.id = "j-memoria";
    c.appendChild(mem);
    caricaMemoria();
    gruppo(c, "Conversazioni");
    c.appendChild(riga("Ricomincia da capo", "Jarvis dimentica la conversazione in corso",
      pulsante("↺ Nuova conversazione", function () { $("j-nuova").onclick(); })));
    var elenco = document.createElement("div");
    elenco.id = "j-conversazioni";
    c.appendChild(elenco);
    caricaConversazioni();
    c.scrollTop = su;
  }

  // ------------------------------------------------------------ HUD
  var tela = $("j-tela"), ctx = tela.getContext("2d");
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  tela.width = 380 * dpr; tela.height = 380 * dpr;
  var COLORI = { riposo: "57,213,255", ascolto: "143,240,255", seguito: "143,240,255", penso: "255,179,65",
    lavoro: "255,179,65", parlo: "77,255,184", conferma: "255,84,104", spento: "86,96,108" };
  var t0 = Date.now(), fase = 0;

  function arco(r, da, a, larghezza, colore) {
    ctx.beginPath(); ctx.arc(0, 0, r, da, a); ctx.lineWidth = larghezza; ctx.strokeStyle = colore; ctx.stroke();
  }
  function disegna() {
    if (!aperto) return;
    requestAnimationFrame(disegna);
    var t = (Date.now() - t0) / 1000;
    var sirena = allarme.attiva && (stato === "riposo" || stato === "seguito" || stato === "spento");
    var rgb = sirena ? "255,38,52" : COLORI[stato] || COLORI.riposo;
    var c = function (a) { return "rgba(" + rgb + "," + a + ")"; };
    var veloce = { lavoro: 3.2, penso: 2.4, ascolto: 1.4, seguito: 1.2, parlo: 1.6, conferma: 0.6 }[stato] || 0.5;
    fase += 0.016 * veloce;
    livelloVisto += (Math.max(livello, stato === "parlo" ? 0.35 + 0.25 * Math.sin(t * 9) * Math.sin(t * 3.3) : 0) - livelloVisto) * 0.25;

    ctx.setTransform(dpr, 0, 0, dpr, 190 * dpr, 190 * dpr);
    ctx.clearRect(-190, -190, 380, 380);

    // tacche esterne
    ctx.save(); ctx.rotate(fase * 0.15);
    for (var i = 0; i < 120; i++) {
      var lunga = i % 10 === 0;
      ctx.beginPath();
      ctx.moveTo(0, -182); ctx.lineTo(0, lunga ? -170 : -176);
      ctx.lineWidth = lunga ? 2 : 1; ctx.strokeStyle = c(lunga ? 0.8 : 0.3); ctx.stroke();
      ctx.rotate(Math.PI * 2 / 120);
    }
    ctx.restore();
    // archi segmentati che girano in senso opposto
    ctx.save(); ctx.rotate(-fase * 0.6);
    for (var k = 0; k < 3; k++) arco(158, k * 2.094, k * 2.094 + 1.3, 4, c(0.75));
    ctx.restore();
    ctx.save(); ctx.rotate(fase * 0.9);
    for (var k2 = 0; k2 < 6; k2++) arco(146, k2 * 1.047, k2 * 1.047 + 0.5, 1.5, c(0.45));
    ctx.restore();
    // radar quando pensa o lavora
    if (stato === "penso" || stato === "lavoro") {
      ctx.save(); ctx.rotate(t * 3);
      var g = ctx.createLinearGradient(0, 0, 140, 0);
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, 140, -0.5, 0); ctx.closePath();
      g.addColorStop(0, c(0)); g.addColorStop(1, c(0.25)); ctx.fillStyle = g; ctx.fill();
      ctx.restore();
    }
    // sentinella: due fasci da sirena che girano e un lampeggio del nucleo (più lento mentre si arma)
    if (sirena) {
      var giro = t * (allarme.armata ? 3.2 : 1.4);
      for (var f = 0; f < 2; f++) {
        ctx.save(); ctx.rotate(giro + f * Math.PI);
        var gf = ctx.createRadialGradient(0, 0, 20, 0, 0, 185);
        gf.addColorStop(0, "rgba(255,38,52,0.55)"); gf.addColorStop(1, "rgba(255,38,52,0)");
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, 185, -0.35, 0.35); ctx.closePath();
        ctx.fillStyle = gf; ctx.fill();
        ctx.restore();
      }
      livelloVisto = Math.max(livelloVisto, 0.25 + 0.25 * Math.max(0, Math.sin(t * (allarme.armata ? 6 : 3))));
    }
    // barre del livello audio
    var n = 72;
    for (var b = 0; b < n; b++) {
      var ang = b / n * Math.PI * 2;
      var onda = 0.5 + 0.5 * Math.sin(ang * 5 + t * 6) * Math.sin(ang * 3 - t * 4);
      var h = 4 + livelloVisto * 46 * (0.35 + 0.65 * onda);
      ctx.save(); ctx.rotate(ang);
      ctx.beginPath(); ctx.moveTo(0, -112); ctx.lineTo(0, -112 - h);
      ctx.lineWidth = 2.2; ctx.strokeStyle = c(0.35 + livelloVisto * 0.6); ctx.stroke();
      ctx.restore();
    }
    arco(108, 0, Math.PI * 2, 1, c(0.5));
    // onda d'urto quando cambia stato
    var dal = (Date.now() - cambioStato) / 1000;
    if (dal < 0.9) {
      var ro = 60 + dal * 150;
      arco(ro, 0, Math.PI * 2, 3 * (1 - dal), c(0.7 * (1 - dal / 0.9)));
    }
    // particelle in orbita: più veloci quando lavora
    for (var p = 0; p < 28; p++) {
      var raggio = 120 + (p * 37 % 50), velo = 0.15 + (p % 7) * 0.05;
      var angp = p * 2.39996 + fase * velo * (p % 2 ? 1 : -1);
      ctx.beginPath();
      ctx.arc(Math.cos(angp) * raggio, Math.sin(angp) * raggio, p % 5 === 0 ? 2 : 1.1, 0, Math.PI * 2);
      ctx.fillStyle = c(0.25 + 0.5 * (0.5 + 0.5 * Math.sin(t * 2 + p)));
      ctx.fill();
    }
    // nucleo
    var rn = 52 + livelloVisto * 22 + Math.sin(t * 2) * 2;
    var gn = ctx.createRadialGradient(0, 0, 4, 0, 0, rn * 1.6);
    gn.addColorStop(0, "rgba(255,255,255,0.95)"); gn.addColorStop(0.25, c(0.85)); gn.addColorStop(0.6, c(0.18));
    gn.addColorStop(1, c(0));
    ctx.beginPath(); ctx.arc(0, 0, rn * 1.6, 0, Math.PI * 2); ctx.fillStyle = gn; ctx.fill();
    ctx.save(); ctx.rotate(-fase);
    for (var s = 0; s < 6; s++) arco(rn * 0.72, s * 1.047 + 0.12, s * 1.047 + 0.92, 3, "rgba(255,255,255,0.55)");
    ctx.restore();
    arco(rn, 0, Math.PI * 2, 1.5, c(0.9));
    livello *= 0.9;
  }

  // ------------------------------------------------------------ apri e chiudi
  function apri() {
    if (aperto) return;
    aperto = true;
    radice.classList.add("aperto");
    if (!collegato) collega();
    requestAnimationFrame(disegna);
    disegnaImpostazioni();
  }
  function chiudi() {
    aperto = false;
    radice.classList.remove("aperto");
    if (nativo && nativo.chiudiTastiera) nativo.chiudiTastiera();
  }
  $("j-chiudi").onclick = chiudi;
  linguetta.onclick = apri;
  window.addEventListener("apri-jarvis", apri);

  // scorrimento dal bordo sinistro verso destra (apre) e da destra verso sinistra dentro Jarvis (chiude)
  (function () {
    var x0 = null, y0 = 0, chiudendo = false;
    document.addEventListener("touchstart", function (e) {
      var t = e.touches[0];
      chiudendo = aperto;
      x0 = (!aperto && t.clientX < 40 && !document.body.classList.contains("modifica")) ? t.clientX :
        (aperto && t.target.closest && !t.target.closest(".j-lato, input, select, button") ? t.clientX : null);
      y0 = t.clientY;
    }, { passive: true });
    document.addEventListener("touchmove", function (e) {
      if (x0 === null) return;
      var dx = e.touches[0].clientX - x0;
      if (!chiudendo) scia.style.width = Math.max(0, dx) + "px";
    }, { passive: true });
    document.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var t = e.changedTouches[0], dx = t.clientX - x0, dy = Math.abs(t.clientY - y0);
      scia.style.width = "0";
      x0 = null;
      if (dy > 120) return;
      if (!chiudendo && dx > window.innerWidth * 0.2) apri();
      else if (chiudendo && dx < -window.innerWidth * 0.2) chiudi();
    });
  })();

  // la linguetta segue lo stato anche a pannello chiuso (si collega subito)
  collega();
  setInterval(function () { if (!collegato) collega(); }, 15000);
})();
