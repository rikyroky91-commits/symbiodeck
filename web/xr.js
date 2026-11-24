/* Jarvis XR: il pannello del PC e Jarvis come ologrammi nella stanza, per Meta Quest 3 (WebXR, realtà mista).
   Si apre nel browser del visore: https://<ip del PC>:47443/xr?t=<chiave> (QR nelle impostazioni del PC).

   Con le mani (come in Minority Report):
     - tocca i pulsanti con la punta dell'indice, come uno schermo sospeso;
     - pizzica (pollice + indice) un pannello e trascinalo dove vuoi; con due mani lo ingrandisci o rimpicciolisci;
     - pizzica e tieni la sfera di Jarvis per parlargli (si registra finché tieni, poi la frase va al PC);
   Con i controller: raggio + grilletto per premere (tenuto sulla sfera = parla), impugnatura per spostare.
   Sul PC (senza visore) la stessa scena si vede in anteprima: clic per premere, trascina per guardarti intorno,
   tieni premuto sulla sfera per parlare.

   Dati: tutto dalla stessa origine: /api/* del pannello e /jarvis/*, che il pannello inoltra a Jarvis. Le posizioni
   dei pannelli e la chiave restano salvate nel visore (dopo la prima volta basta https://<ip del PC>:47443/xr). */
import * as THREE from "./lib/three.min.js";

// ------------------------------------------------------------------ indirizzi e chiave
const q = new URLSearchParams(location.search);
let TOKEN = q.get("t") || leggi("token") || "";
if (q.get("t")) scrivi("token", TOKEN);
const PANNELLO = location.origin;
// Jarvis passa dal pannello stesso (/jarvis/* inoltrato alla porta 47802): un indirizzo solo, anche in HTTPS
const JARVIS = q.get("jarvis") || location.origin;

function leggi(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function scrivi(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* niente memoria nel browser */ } }

function api(base, percorso, corpo) {
  const opz = { headers: { "X-Token": TOKEN } };
  if (corpo !== undefined) {
    opz.method = "POST";
    opz.headers["Content-Type"] = "application/json";
    opz.body = JSON.stringify(corpo);
  }
  return fetch(base + percorso, opz).then((r) => r.json().then((j) => {
    if (!r.ok) throw new Error(j.errore || "errore " + r.status);
    return j;
  }));
}
const apiJ = (p, c) => api(JARVIS, p, c);
const apiP = (p, c) => api(PANNELLO, p, c);

function flusso(url, suMessaggio, suStato) {
  let es = null;
  const apri = () => {
    es = new EventSource(url + (url.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN));
    es.onopen = () => suStato(true);
    es.onmessage = (e) => { try { suMessaggio(JSON.parse(e.data)); } catch (err) { /* evento non valido */ } };
    es.onerror = () => { suStato(false); es.close(); setTimeout(apri, 3000); };
  };
  apri();
}

// ------------------------------------------------------------------ stato condiviso
const COLORI = { riposo: 0x39d5ff, ascolto: 0x8ff0ff, seguito: 0x8ff0ff, penso: 0xffb341, lavoro: 0xffb341,
  parlo: 0x4dffb8, conferma: 0xff5468, spento: 0x56606c };
const NOMI = { spento: "SPENTO", riposo: "IN ATTESA", ascolto: "TI ASCOLTO", penso: "ELABORO", lavoro: "AL LAVORO",
  parlo: "PARLO", conferma: "CONFERMA", seguito: "TI ASCOLTO" };
const S = {
  stato: "riposo", livello: 0, imp: {}, messaggi: [], corrente: null, azione: "", conferma: null, promemoria: [],
  emergenza: false, pc: null, storia: { fps: [], gpu: [], cpu: [] }, config: { app: [], scorciatoie: [] },
  jarvisOk: false, pannelloOk: false, registra: false, dettatura: false,
};
const pannelli = [];
const sporca = (...ids) => pannelli.forEach((p) => { if (!ids.length || ids.includes(p.id)) p.sporco = true; });

// ------------------------------------------------------------------ scena
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType("local-floor");
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scena = new THREE.Scene();
const sfondoAnteprima = new THREE.Color(0x02060b);
scena.background = sfondoAnteprima;
const camera = new THREE.PerspectiveCamera(68, innerWidth / innerHeight, 0.01, 60);
camera.position.set(0, 1.58, 0.62);   // anteprima: un passo indietro, per vedere tutto
const ancora = new THREE.Group();
scena.add(ancora);
let altezzaOcchi = 1.6;

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const ADD = { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false };

// pavimento olografico: un anello attorno ai piedi e una griglia (la griglia solo in anteprima)
const pedana = new THREE.Group();
ancora.add(pedana);
const anelloPedana = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.565, 96),
  new THREE.MeshBasicMaterial({ color: 0x39d5ff, opacity: 0.45, ...ADD, side: THREE.DoubleSide }));
anelloPedana.rotation.x = -Math.PI / 2;
pedana.add(anelloPedana);
const tacche = new THREE.Mesh(new THREE.RingGeometry(0.6, 0.64, 72, 1, 0, Math.PI * 2),
  new THREE.MeshBasicMaterial({ color: 0x39d5ff, opacity: 0.18, ...ADD, side: THREE.DoubleSide, wireframe: true }));
tacche.rotation.x = -Math.PI / 2;
pedana.add(tacche);
const griglia = new THREE.GridHelper(14, 56, 0x0f3a4a, 0x0a2230);
griglia.material.transparent = true;
griglia.material.opacity = 0.6;
scena.add(griglia);

// pulviscolo luminoso nella stanza
const pulviscolo = (() => {
  const n = 260, pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const r = 0.6 + Math.random() * 2.2, a = Math.random() * Math.PI * 2;
    pos.set([Math.cos(a) * r, 0.2 + Math.random() * 2.2, Math.sin(a) * r], i * 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const p = new THREE.Points(g, new THREE.PointsMaterial({ color: 0x39d5ff, size: 0.006, opacity: 0.55, ...ADD }));
  ancora.add(p);
  return p;
})();

// ------------------------------------------------------------------ la sfera di Jarvis
const sfera = (() => {
  const g = new THREE.Group();
  const colore = new THREE.Color(COLORI.riposo);
  const nucleoMat = new THREE.ShaderMaterial({
    uniforms: { colore: { value: colore }, t: { value: 0 }, livello: { value: 0 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; uniform float t; uniform float livello;
      void main() { vN = normalize(normalMatrix * normal);
        vec3 p = position * (1.0 + 0.06 * sin(t * 3.0 + position.y * 40.0) * (0.3 + livello));
        vec4 mv = modelViewMatrix * vec4(p, 1.0); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `varying vec3 vN; varying vec3 vV; uniform vec3 colore; uniform float t;
      void main() { float f = pow(1.0 - abs(dot(vN, vV)), 2.2);
        float bande = 0.5 + 0.5 * sin(vN.y * 30.0 - t * 4.0);
        vec3 c = colore * (0.35 + 1.6 * f) + vec3(1.0) * pow(1.0 - f, 6.0) * 0.55 + colore * bande * 0.12;
        gl_FragColor = vec4(c, 0.35 + f * 0.65); }`,
    ...ADD,
  });
  const nucleo = new THREE.Mesh(new THREE.SphereGeometry(0.055, 48, 32), nucleoMat);
  g.add(nucleo);
  const reticolo = new THREE.Mesh(new THREE.IcosahedronGeometry(0.072, 1),
    new THREE.MeshBasicMaterial({ color: colore, wireframe: true, opacity: 0.35, ...ADD }));
  g.add(reticolo);
  const anelli = [0.095, 0.115, 0.14].map((r, i) => {
    const a = new THREE.Mesh(new THREE.TorusGeometry(r, i === 1 ? 0.0012 : 0.0022, 8, 128, Math.PI * (1.2 + i * 0.3)),
      new THREE.MeshBasicMaterial({ color: colore, opacity: 0.8 - i * 0.18, ...ADD }));
    a.rotation.set(Math.PI / 2 + i * 0.5, i * 0.7, 0);
    g.add(a);
    return a;
  });
  // barre del livello audio attorno
  const n = 56;
  const barre = new THREE.InstancedMesh(new THREE.BoxGeometry(0.0025, 1, 0.0025),
    new THREE.MeshBasicMaterial({ color: colore, opacity: 0.85, ...ADD }), n);
  g.add(barre);
  const alone = new THREE.Sprite(new THREE.SpriteMaterial({ map: alonetexture(), color: colore, opacity: 0.55, ...ADD }));
  alone.scale.set(0.42, 0.42, 1);
  g.add(alone);
  const onda = new THREE.Mesh(new THREE.RingGeometry(0.06, 0.064, 64),
    new THREE.MeshBasicMaterial({ color: colore, opacity: 0, ...ADD, side: THREE.DoubleSide }));
  g.add(onda);
  const etichetta = new Etichetta(0.3, 0.05);
  etichetta.mesh.position.y = -0.2;
  g.add(etichetta.mesh);
  const collisione = new THREE.Mesh(new THREE.SphereGeometry(0.13, 12, 8), new THREE.MeshBasicMaterial({ visible: false }));
  collisione.userData.sfera = true;
  g.add(collisione);

  const m = new THREE.Matrix4(), qq = new THREE.Quaternion(), v = new THREE.Vector3(), sc = new THREE.Vector3();
  let fase = 0, livelloVisto = 0, statoVisto = "", ondaT = 9;
  return {
    gruppo: g, collisione,
    aggiorna(dt, t, testa) {
      const st = S.registra ? "ascolto" : S.stato;
      if (st !== statoVisto) { statoVisto = st; ondaT = 0; etichetta.scrivi(S.registra ? "REGISTRO… LASCIA PER INVIARE" : NOMI[st] || st); }
      colore.lerp(new THREE.Color(COLORI[st] || COLORI.riposo), Math.min(1, dt * 6));
      const veloce = { lavoro: 3.2, penso: 2.4, ascolto: 1.6, seguito: 1.2, parlo: 1.8, conferma: 0.7 }[st] || 0.5;
      fase += dt * veloce;
      const parla = st === "parlo" ? 0.35 + 0.25 * Math.sin(t * 9) * Math.sin(t * 3.3) : 0;
      livelloVisto += (Math.max(S.livello, parla, S.registra ? micLivello * 4 : 0) - livelloVisto) * 0.25;
      S.livello *= 0.92;
      nucleoMat.uniforms.t.value = t;
      nucleoMat.uniforms.livello.value = livelloVisto;
      nucleo.scale.setScalar(1 + livelloVisto * 0.35 + Math.sin(t * 2) * 0.02);
      reticolo.rotation.set(fase * 0.4, fase * 0.6, 0);
      anelli.forEach((a, i) => { a.rotation.z += dt * veloce * (i % 2 ? -0.8 : 0.6); a.material.color = colore; });
      for (let i = 0; i < n; i++) {
        const ang = i / n * Math.PI * 2;
        const h = 0.004 + livelloVisto * 0.06 * (0.35 + 0.65 * (0.5 + 0.5 * Math.sin(ang * 5 + t * 6) * Math.sin(ang * 3 - t * 4)));
        v.set(Math.cos(ang) * (0.16 + h / 2), Math.sin(ang) * (0.16 + h / 2), 0);
        qq.setFromAxisAngle(new THREE.Vector3(0, 0, 1), ang - Math.PI / 2);
        m.compose(v, qq, sc.set(1, h, 1));
        barre.setMatrixAt(i, m);
      }
      barre.instanceMatrix.needsUpdate = true;
      ondaT += dt;
      onda.material.opacity = Math.max(0, 0.8 - ondaT * 1.1);
      onda.scale.setScalar(1 + ondaT * 4);
      // le barre, l'onda e l'etichetta guardano sempre verso di te
      if (testa) { barre.lookAt(testa); onda.lookAt(testa); etichetta.mesh.lookAt(testa); }
    },
  };
})();

function alonetexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const x = c.getContext("2d");
  const gr = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, "rgba(255,255,255,0.9)"); gr.addColorStop(0.25, "rgba(255,255,255,0.25)");
  gr.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = gr; x.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// testo semplice su un piano (etichette)
function Etichetta(w, h) {
  const PX = 1400;
  const c = document.createElement("canvas");
  c.width = Math.round(w * PX); c.height = Math.round(h * PX);
  const x = c.getContext("2d");
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, ...ADD }));
  this.scrivi = (testo) => {
    x.clearRect(0, 0, c.width, c.height);
    x.font = "600 " + Math.round(c.height * 0.42) + "px Consolas, 'Roboto Mono', monospace";
    x.textAlign = "center"; x.textBaseline = "middle";
    x.fillStyle = "#bff4ff"; x.shadowColor = "#39d5ff"; x.shadowBlur = 14;
    x.letterSpacing = "6px";
    x.fillText(testo, c.width / 2, c.height / 2);
    tex.needsUpdate = true;
  };
}

// ------------------------------------------------------------------ pannelli olografici
const PX_M = 1500;                  // pixel per metro delle texture: nitido anche da vicino nel Quest 3
const FONT = "'Segoe UI', Roboto, 'Inter', system-ui, sans-serif";
const MONO = "Consolas, 'Roboto Mono', monospace";

class Pannello {
  constructor(id, titolo, w, h, opz = {}) {
    Object.assign(this, { id, titolo, w, h, sporco: true, bottoni: [], hover: -1, premuto: -1, premutoT: 0,
      sempreDavanti: !!opz.sempreDavanti, comparsa: 0, visibile: opz.visibile !== false, colore: opz.colore || "57,213,255",
      chiudibile: !!opz.chiudibile, denso: !!opz.denso, volo: null });
    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d");
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.tex.anisotropy = 8;
    this.gruppo = new THREE.Group();
    this.mat = new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, depthWrite: false, opacity: 0 });
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), this.mat);
    this.mesh.userData.pannello = this;
    this.gruppo.add(this.mesh);
    // angoli luminosi in 3D, appena davanti al vetro
    this.angoli = new THREE.LineSegments(new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: new THREE.Color("rgb(" + this.colore + ")"), opacity: 0, ...ADD }));
    this.gruppo.add(this.angoli);
    // linea di scansione che scorre
    this.scansione = new THREE.Mesh(new THREE.PlaneGeometry(1, 0.004),
      new THREE.MeshBasicMaterial({ color: new THREE.Color("rgb(" + this.colore + ")"), opacity: 0.22, ...ADD }));
    this.scansione.position.z = 0.001;
    this.gruppo.add(this.scansione);
    // cursore del dito sul vetro
    this.cursore = new THREE.Mesh(new THREE.RingGeometry(0.006, 0.0085, 32),
      new THREE.MeshBasicMaterial({ color: 0xffffff, opacity: 0, ...ADD }));
    this.cursore.position.z = 0.003;
    this.gruppo.add(this.cursore);
    if (this.sempreDavanti) {
      [this.mesh, this.angoli, this.scansione, this.cursore].forEach((o) => { o.renderOrder = 20; o.material.depthTest = false; });
    }
    this.gruppo.visible = this.visibile;
    this.dimensioni(w, h);
    pannelli.push(this);
  }

  // misure del vetro (si possono cambiare: le finestre del PC prendono le proporzioni della finestra vera)
  dimensioni(w, h) {
    this.w = w; this.h = h;
    this.cw = Math.round(w * PX_M); this.ch = Math.round(h * PX_M);
    this.canvas.width = this.cw; this.canvas.height = this.ch;
    this.tex.dispose();
    this.mesh.geometry.dispose();
    this.mesh.geometry = new THREE.PlaneGeometry(w, h);
    const a = Math.min(w, h) * 0.12, pts = [];
    for (const [sx, sy] of [[-1, 1], [1, 1], [1, -1], [-1, -1]]) {
      const x = sx * w / 2, y = sy * h / 2;
      pts.push(x, y, 0.002, x - sx * a, y, 0.002, x, y, 0.002, x, y - sy * a, 0.002);
    }
    this.angoli.geometry.dispose();
    this.angoli.geometry = new THREE.BufferGeometry();
    this.angoli.geometry.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.scansione.scale.x = w * 0.98;
    this.sporco = true;
  }

  chiudi() {
    const i = pannelli.indexOf(this);
    if (i >= 0) pannelli.splice(i, 1);
    if (this.gruppo.parent) this.gruppo.parent.remove(this.gruppo);
    this.alla_chiusura && this.alla_chiusura();
    this.tex.dispose(); this.mesh.geometry.dispose(); this.angoli.geometry.dispose();
    bip(330, 0.09, 0.05);
  }

  // ---- disegno
  disegna() {
    const c = this.ctx, W = this.cw, H = this.ch;
    this.bottoni = [];
    c.clearRect(0, 0, W, H);
    const col = this.colore;
    const g = c.createLinearGradient(0, 0, 0, H);
    const fondo = this.sempreDavanti || this.denso ? [0.96, 0.93] : [0.78, 0.66];   // conferme, foto, finestre: opachi
    g.addColorStop(0, "rgba(" + (this.sempreDavanti ? "24,6,12," : "6,22,34,") + fondo[0] + ")");
    g.addColorStop(1, "rgba(" + (this.sempreDavanti ? "12,2,6," : "2,10,18,") + fondo[1] + ")");
    rettangolo(c, 3, 3, W - 6, H - 6, 26); c.fillStyle = g; c.fill();
    c.lineWidth = 3; c.strokeStyle = "rgba(" + col + ",0.45)"; c.stroke();
    // trama a righe sottili
    c.save(); rettangolo(c, 3, 3, W - 6, H - 6, 26); c.clip();
    c.fillStyle = "rgba(" + col + ",0.035)";
    for (let y = 0; y < H; y += 6) c.fillRect(0, y, W, 1);
    c.restore();
    // titolo
    c.font = "600 30px " + MONO; c.letterSpacing = "6px"; c.fillStyle = "rgba(" + col + ",1)";
    c.shadowColor = "rgba(" + col + ",0.8)"; c.shadowBlur = 12; c.textBaseline = "middle";
    c.fillText(this.titolo, 34, 46);
    c.shadowBlur = 0; c.letterSpacing = "0px";
    c.fillStyle = "rgba(" + col + ",0.35)"; c.fillRect(34, 76, W - 68, 2);
    this.contenuto(c, W, H);
    if (this.chiudibile) this.bottone(c, W - 92, 16, 64, 56, "✕", () => this.chiudi(), { dim: 30, colore: "255,84,104" });
    this.tex.needsUpdate = true;
    this.sporco = false;
  }
  contenuto() {}

  bottone(c, x, y, w, h, testo, azione, o = {}) {
    const i = this.bottoni.length;
    this.bottoni.push({ x, y, w, h, azione });
    const col = o.colore || this.colore;
    const sopra = this.hover === i, premuto = this.premuto === i && performance.now() - this.premutoT < 260;
    rettangolo(c, x, y, w, h, o.raggio || 18);
    c.fillStyle = o.pieno ? "rgba(" + col + "," + (premuto ? 1 : sopra ? 0.95 : 0.82) + ")"
      : "rgba(" + col + "," + (premuto ? 0.55 : sopra ? 0.28 : 0.1) + ")";
    c.fill();
    c.lineWidth = sopra ? 4 : 2.5; c.strokeStyle = "rgba(" + col + "," + (sopra ? 1 : 0.7) + ")";
    if (sopra) { c.shadowColor = "rgba(" + col + ",0.9)"; c.shadowBlur = 22; }
    c.stroke(); c.shadowBlur = 0;
    c.fillStyle = o.pieno ? "#02131a" : "#e8fbff";
    c.textAlign = "center"; c.textBaseline = "middle";
    c.font = (o.peso || 600) + " " + (o.dim || 32) + "px " + (o.mono ? MONO : FONT);
    if (o.sotto) {
      c.fillText(testo, x + w / 2, y + h / 2 - (o.dim || 32) * 0.35);
      c.font = "400 " + Math.round((o.dim || 32) * 0.45) + "px " + FONT; c.globalAlpha = 0.75;
      c.fillText(o.sotto, x + w / 2, y + h / 2 + (o.dim || 32) * 0.5); c.globalAlpha = 1;
    } else c.fillText(testo, x + w / 2, y + h / 2 + 1);
    if (o.immagine && o.immagine.complete && o.immagine.naturalWidth) {
      const s = Math.min(w, h) * 0.42;
      c.drawImage(o.immagine, x + w / 2 - s / 2, y + h * 0.16, s, s);
    }
    c.textAlign = "left";
  }

  // ---- interazione (u, v in 0..1 sul vetro)
  indiceIn(u, v) {
    const x = u * this.cw, y = (1 - v) * this.ch;
    return this.bottoni.findIndex((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
  }
  sopra(u, v) {
    const i = u === null ? -1 : this.indiceIn(u, v);
    if (i !== this.hover) { this.hover = i; this.sporco = true; if (i >= 0) bip(1500, 0.015, 0.015); }
  }
  // un pannello piccolo di lato, toccato, torna davanti (e il tocco non fa altro)
  portaAvanti() {
    const k = dinamici.indexOf(this);
    if (k < 0 || k === dinamici.length - 1 || this.spostatoDaTe || this.gruppo.scale.x >= 0.9) return false;
    dinamici.splice(k, 1); dinamici.push(this); aVentaglio(); bip(700, 0.06, 0.05);
    return true;
  }
  premi(u, v, origine) {
    if (this.portaAvanti()) return true;
    const i = this.indiceIn(u, v);
    if (i < 0) return false;
    this.premuto = i; this.premutoT = performance.now(); this.sporco = true;
    setTimeout(() => { this.sporco = true; }, 280);
    bip(1100, 0.05, 0.06); vibra(origine);
    try { this.bottoni[i].azione(); } catch (e) { avviso("Errore: " + e.message); }
    return true;
  }
  mostraCursore(local, vicino) {
    if (!local) { this.cursore.material.opacity = 0; return; }
    this.cursore.position.set(local.x, local.y, 0.003);
    this.cursore.material.opacity = 0.9 * (1 - vicino);
    this.cursore.scale.setScalar(1 + vicino * 2.5);
  }

  aggiorna(dt, t) {
    if (!this.gruppo.visible) return;
    if (this.volo) {   // esce dalla sfera e vola al suo posto
      this.volo.t = Math.min(1, this.volo.t + dt * 2.4);
      const k = 1 - Math.pow(1 - this.volo.t, 3);
      this.gruppo.position.lerpVectors(this.volo.da, this.volo.a, k);
      if (!this.volato) this.gruppo.scale.setScalar((0.15 + 0.85 * k) * (this.volo.s1 || 1));
      else if (this.volo.s1) this.gruppo.scale.setScalar(this.volo.s0 + (this.volo.s1 - this.volo.s0) * k);
      this.gruppo.lookAt(posizioneTesta());
      if (this.volo.t >= 1) { this.volo = null; this.volato = true; }
    }
    if (this.ogni) this.ogni(dt, t);
    if (this.sporco) this.disegna();
    this.comparsa = Math.min(1, this.comparsa + dt * 2.2);
    const c = Math.max(0, this.comparsa), k = c ? uscitaElastica(c) : 0;
    // mentre c'è una conferma, il resto si attenua: l'attenzione va lì
    this.attenua = THREE.MathUtils.lerp(this.attenua || 1, S.conferma && !this.sempreDavanti ? 0.22 : 1, Math.min(1, dt * 8));
    this.mat.opacity = Math.min(1, c * 1.4) * this.attenua;
    this.angoli.material.opacity = 0.9 * c * this.attenua;
    this.mesh.scale.set(1, 0.02 + 0.98 * k, 1);
    this.scansione.position.y = this.h / 2 - ((t * 0.12 + this.w) % 1) * this.h;
    this.scansione.material.opacity = 0.18 * c;
  }
}

function rettangolo(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
function uscitaElastica(x) { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); }
function aCapo(c, testo, larghezza) {
  const righe = [];
  for (const paragrafo of String(testo).split("\n")) {
    let riga = "";
    for (const parola of paragrafo.split(" ")) {
      const prova = riga ? riga + " " + parola : parola;
      if (c.measureText(prova).width > larghezza && riga) { righe.push(riga); riga = parola; } else riga = prova;
    }
    righe.push(riga);
  }
  return righe;
}

// ---- conversazione e comandi di Jarvis
const pConversazione = new Pannello("conversazione", "JARVIS · CONVERSAZIONE", 0.66, 0.4);
pConversazione.contenuto = function (c, W, H) {
  const modo = S.imp.privata ? (S.emergenza ? "PRIVATA · SOLO PC" : "PRIVATA · CERVELLO SPENTO")
    : S.emergenza ? "EMERGENZA · LOCALE" : "CLAUDE";
  c.font = "600 22px " + MONO; c.textAlign = "right";
  c.fillStyle = S.imp.privata ? "#4dffb8" : S.emergenza ? "#ffb341" : "rgba(223,246,255,0.6)";
  c.fillText((S.jarvisOk ? "● " : "○ ") + modo, W - 34, 46);
  c.textAlign = "left";
  // messaggi dal basso verso l'alto
  const basso = H - 150, sinistra = 40, larg = W - 80;
  let y = basso;
  const msg = S.messaggi.slice(-12);
  for (let i = msg.length - 1; i >= 0 && y > 110; i--) {
    const m = msg[i];
    c.font = (m.tipo === "azione" ? "400 22px " + MONO : "400 27px " + FONT);
    const righe = aCapo(c, m.testo, larg * 0.82).slice(-6);
    const h = righe.length * 34 + 22;
    y -= h + 10;
    if (y < 96) break;
    const destra = m.tipo === "utente";
    const bw = Math.min(larg * 0.86, Math.max(...righe.map((r) => c.measureText(r).width)) + 34);
    const x = destra ? W - sinistra - bw : sinistra;
    if (m.tipo !== "azione") {
      rettangolo(c, x, y, bw, h, 16);
      c.fillStyle = destra ? "rgba(57,213,255,0.16)" : "rgba(255,255,255,0.06)"; c.fill();
      c.strokeStyle = destra ? "rgba(57,213,255,0.45)" : "rgba(255,255,255,0.12)"; c.lineWidth = 2; c.stroke();
    }
    c.fillStyle = m.tipo === "azione" ? "#ffb341" : m.tipo === "errore" ? "#ff5468" : "#e8fbff";
    righe.forEach((r, k) => c.fillText((m.tipo === "azione" && k === 0 ? "▸ " : "") + r, x + 17, y + 28 + k * 34));
  }
  if (!S.messaggi.length) {
    c.font = "400 28px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)"; c.textAlign = "center";
    c.fillText("Pizzica e tieni la sfera per parlare a Jarvis", W / 2, H / 2 - 20);
    c.fillText("o di' \"Ehi Jarvis\" vicino al tablet", W / 2, H / 2 + 22);
    c.textAlign = "left";
  }
  const by = H - 118, bh = 84, gap = 18, n = 4, bw = (W - 68 - gap * (n - 1)) / n;
  this.bottone(c, 34, by, bw, bh, "■ STOP", () => apiJ("/jarvis/taci", {}));
  this.bottone(c, 34 + (bw + gap), by, bw, bh, "↺ NUOVA", () => { S.messaggi = []; apiJ("/jarvis/nuova", {}); sporca("conversazione"); });
  this.bottone(c, 34 + (bw + gap) * 2, by, bw, bh, S.imp.privata ? "🔒 PRIVATA" : "🔓 PRIVATA",
    () => apiJ("/jarvis/impostazioni", { privata: !S.imp.privata }), { pieno: !!S.imp.privata, colore: "77,255,184" });
  this.bottone(c, 34 + (bw + gap) * 3, by, bw, bh, "⟲ RIORDINA", () => disposizioneIniziale(true));
};

// ---- telemetria del PC
const pTelemetria = new Pannello("telemetria", "PC · TELEMETRIA", 0.62, 0.44);
pTelemetria.contenuto = function (c, W, H) {
  const d = S.pc;
  if (!d) {
    c.font = "400 28px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)"; c.textAlign = "center";
    c.fillText(S.pannelloOk ? "In attesa dei dati…" : "Pannello del PC non raggiungibile", W / 2, H / 2);
    c.textAlign = "left"; return;
  }
  const quadrante = (cx, cy, r, nome, carico, temp, colore) => {
    c.lineCap = "round";
    c.lineWidth = 16; c.strokeStyle = "rgba(" + colore + ",0.14)";
    c.beginPath(); c.arc(cx, cy, r, Math.PI * 0.75, Math.PI * 2.25); c.stroke();
    const f = Math.max(0, Math.min(1, (carico || 0) / 100));
    c.strokeStyle = "rgba(" + colore + ",1)"; c.shadowColor = "rgba(" + colore + ",0.9)"; c.shadowBlur = 18;
    c.beginPath(); c.arc(cx, cy, r, Math.PI * 0.75, Math.PI * (0.75 + 1.5 * f)); c.stroke();
    c.shadowBlur = 0; c.lineCap = "butt";
    c.textAlign = "center"; c.fillStyle = "#e8fbff";
    c.font = "700 64px " + FONT; c.fillText(carico == null ? "—" : Math.round(carico) + "%", cx, cy - 6);
    c.font = "600 24px " + MONO; c.fillStyle = "rgba(" + colore + ",1)"; c.fillText(nome, cx, cy + 44);
    c.font = "400 26px " + FONT; c.fillStyle = temp >= 85 ? "#ff5468" : "rgba(223,246,255,0.75)";
    c.fillText(temp == null ? "" : Math.round(temp) + " °C", cx, cy + 80);
    c.textAlign = "left";
  };
  quadrante(170, 250, 118, "CPU", d.cpu && d.cpu.carico, d.cpu && d.cpu.temp, "57,213,255");
  quadrante(450, 250, 118, "GPU", d.gpu && d.gpu.carico, d.gpu && d.gpu.temp, "77,255,184");
  // FPS grande con la sua storia
  const fx = 620, fw = W - fx - 40;
  c.font = "600 22px " + MONO; c.fillStyle = "rgba(255,179,65,1)"; c.fillText("FPS", fx, 130);
  c.font = "700 92px " + FONT; c.fillStyle = "#fff1d6";
  c.fillText(d.fps && d.fps.ora ? Math.round(d.fps.ora) : "—", fx, 205);
  grafico(c, S.storia.fps, fx, 250, fw, 90, "255,179,65", 0);
  grafico(c, S.storia.gpu, fx, 360, fw, 60, "77,255,184", 100);
  c.font = "400 20px " + MONO; c.fillStyle = "rgba(223,246,255,0.5)"; c.fillText("GPU 90 s", fx, 446);
  // barre di RAM e VRAM
  const barra = (y, nome, usata, tot, colore) => {
    const f = usata && tot ? Math.min(1, usata / tot) : 0;
    c.font = "600 22px " + MONO; c.fillStyle = "rgba(" + colore + ",1)"; c.fillText(nome, 40, y);
    c.fillStyle = "rgba(" + colore + ",0.15)"; c.fillRect(150, y - 12, W - 400, 18);
    c.fillStyle = "rgba(" + colore + ",0.9)"; c.fillRect(150, y - 12, (W - 400) * f, 18);
    c.font = "400 22px " + FONT; c.fillStyle = "rgba(223,246,255,0.75)";
    c.fillText(usata && tot ? (usata / 1024).toFixed(1) + " / " + (tot / 1024).toFixed(0) + " GB" : "—", W - 230, y + 2);
  };
  barra(H - 150, "RAM", d.ram && d.ram.usata, d.ram && d.ram.totale, "57,213,255");
  barra(H - 108, "VRAM", d.gpu && d.gpu.vram, d.gpu && d.gpu.vram_tot, "77,255,184");
  c.font = "400 22px " + FONT; c.fillStyle = "rgba(223,246,255,0.55)";
  c.fillText("In primo piano: " + (d.primo_piano || "—"), 40, H - 50);
};
function grafico(c, serie, x, y, w, h, colore, massimo) {
  c.strokeStyle = "rgba(" + colore + ",0.15)"; c.lineWidth = 1; c.strokeRect(x, y, w, h);
  const v = serie.filter((n) => n != null);
  if (v.length < 2) return;
  const top = massimo || Math.max(30, ...v) * 1.15;
  c.beginPath();
  serie.forEach((n, i) => {
    const px = x + i / 89 * w, py = y + h - Math.min(1, (n || 0) / top) * h;
    i ? c.lineTo(px, py) : c.moveTo(px, py);
  });
  c.strokeStyle = "rgba(" + colore + ",1)"; c.lineWidth = 3; c.shadowColor = "rgba(" + colore + ",0.8)"; c.shadowBlur = 10;
  c.stroke(); c.shadowBlur = 0;
  c.lineTo(x + (serie.length - 1) / 89 * w, y + h); c.lineTo(x, y + h); c.closePath();
  c.fillStyle = "rgba(" + colore + ",0.08)"; c.fill();
}

// ---- app e scorciatoie del pannello
const icone = {};
const pApp = new Pannello("app", "APP · SCORCIATOIE", 0.6, 0.46);
pApp.pagina = 0;
pApp.contenuto = function (c, W, H) {
  const app = S.config.app || [], sc = S.config.scorciatoie || [];
  const col = 4, per = 8, gap = 16, tw = (W - 68 - gap * (col - 1)) / col, th = 122;
  const pagine = Math.max(1, Math.ceil(app.length / per));
  this.pagina = Math.min(this.pagina, pagine - 1);
  app.slice(this.pagina * per, this.pagina * per + per).forEach((a, i) => {
    if (!icone[a.target]) {
      const img = new Image();
      img.onload = () => sporca("app");
      img.src = PANNELLO + "/api/icona?t=" + encodeURIComponent(TOKEN) + "&target=" + encodeURIComponent(a.target);
      icone[a.target] = img;
    }
    const x = 34 + (i % col) * (tw + gap), y = 100 + Math.floor(i / col) * (th + gap);
    this.bottone(c, x, y, tw, th, "", () => apiP("/api/azione", { tipo: "app", id: a.id }).then(() => avviso("Apro " + a.nome)),
      { immagine: icone[a.target] });
    c.font = "500 20px " + FONT; c.fillStyle = "#dff6ff"; c.textAlign = "center";
    c.fillText(taglia(c, a.nome, tw - 16), x + tw / 2, y + th - 20); c.textAlign = "left";
  });
  if (!app.length) {
    c.font = "400 26px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)";
    c.fillText("Nessuna app: aggiungile dal pannello del tablet.", 40, 170);
  }
  if (pagine > 1) {
    this.bottone(c, W - 180, 22, 64, 48, "‹", () => { this.pagina = (this.pagina + pagine - 1) % pagine; this.sporco = true; }, { dim: 34 });
    this.bottone(c, W - 100, 22, 64, 48, "›", () => { this.pagina = (this.pagina + 1) % pagine; this.sporco = true; }, { dim: 34 });
  }
  const sy = 100 + 2 * (th + gap) + 8, sw = (W - 68 - gap * 3) / 4;
  sc.slice(0, 8).forEach((s, i) => {
    const x = 34 + (i % 4) * (sw + gap), y = sy + Math.floor(i / 4) * 84;
    this.bottone(c, x, y, sw, 70, (s.icona || "⌨") + " " + taglia(c, s.nome, sw - 60),
      () => apiP("/api/azione", { tipo: "scorciatoia", id: s.id }), { dim: 22, colore: "255,179,65" });
  });
};
function taglia(c, t, w) {
  t = String(t || "");
  if (c.measureText(t).width <= w) return t;
  while (t.length > 1 && c.measureText(t + "…").width > w) t = t.slice(0, -1);
  return t + "…";
}

// ---- media
const pMedia = new Pannello("media", "MEDIA", 0.5, 0.17);
pMedia.contenuto = function (c, W, H) {
  const tasti = [["⏮", "media_prev"], ["⏯", "media_play_pause"], ["⏭", "media_next"],
    ["🔉", "volume_down"], ["🔇", "volume_mute"], ["🔊", "volume_up"]];
  const gap = 14, bw = (W - 68 - gap * 5) / 6;
  tasti.forEach(([s, k], i) => this.bottone(c, 34 + i * (bw + gap), 100, bw, H - 130, s,
    () => apiP("/api/azione", { tipo: "media", tasto: k }), { dim: 46, colore: "143,240,255" }));
};

// ---- promemoria e comandi rapidi di Jarvis
const pPromemoria = new Pannello("promemoria", "PROMEMORIA · OGGI", 0.46, 0.42);
pPromemoria.contenuto = function (c, W, H) {
  const ora = new Date();
  c.font = "700 70px " + FONT; c.fillStyle = "#e8fbff";
  c.fillText(ora.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }), 36, 150);
  c.font = "400 24px " + FONT; c.fillStyle = "rgba(223,246,255,0.6)";
  c.fillText(ora.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" }), 40, 196);
  let y = 236;
  const lista = S.promemoria.slice(0, 4);
  if (!lista.length) {
    c.font = "400 24px " + FONT; c.fillStyle = "rgba(223,246,255,0.45)";
    c.fillText("Nessun promemoria", 40, y + 26);
  }
  lista.forEach((p) => {
    c.font = "600 22px " + MONO; c.fillStyle = "#ffb341"; c.fillText(p.ora, 40, y + 26);
    c.font = "400 24px " + FONT; c.fillStyle = "#e8fbff"; c.fillText(taglia(c, p.testo, W - 330), 210, y + 26);
    this.bottone(c, W - 96, y, 60, 50, "✕", () => apiJ("/jarvis/promemoria", { togli: p.id }).then((d) => {
      S.promemoria = d.promemoria; sporca("promemoria"); }), { dim: 24, colore: "255,84,104" });
    y += 62;
  });
  const bw = (W - 68 - 16) / 2;
  this.bottone(c, 34, H - 108, bw, 80, "☀ BRIEFING", () => apiJ("/jarvis/scrivi", { testo: "buongiorno" }), { dim: 26 });
  this.bottone(c, 50 + bw, H - 108, bw, 80, S.dettatura ? "✎ FINE DETTATURA" : "✎ DETTATURA",
    () => apiJ("/jarvis/scrivi", { testo: S.dettatura ? "fine dettatura" : "dettatura" }), { dim: 24, pieno: S.dettatura });
};

// ---- autorizzazioni e domande: grandi, davanti agli occhi
const pConferma = new Pannello("conferma", "AUTORIZZAZIONE RICHIESTA", 0.72, 0.46,
  { sempreDavanti: true, visibile: false, colore: "255,84,104" });
pConferma.contenuto = function (c, W, H) {
  const k = S.conferma;
  if (!k) return;
  this.titolo = k.domanda ? "JARVIS TI CHIEDE" : "AUTORIZZAZIONE RICHIESTA";
  const parti = String(k.descrizione || "").split(/:(.+)/);
  c.textAlign = "center"; c.fillStyle = "#ffffff"; c.font = "600 44px " + FONT;
  const testo = k.domanda ? k.descrizione : "Devo " + parti[0].trim() + ". Procedo?";
  aCapo(c, testo, W - 120).slice(0, 2).forEach((r, i) => c.fillText(r, W / 2, 146 + i * 54));
  if (!k.domanda && parti[1]) {
    c.font = "400 24px " + MONO; c.fillStyle = "rgba(255,211,217,0.7)";
    c.fillText(taglia(c, parti[1].trim(), W - 120), W / 2, 252);
  }
  if (k.alto) {
    c.font = "700 22px " + MONO; c.fillStyle = "#ff5468";
    c.fillText("AZIONE DELICATA · SERVE IL TOCCO, A VOCE SOLO NO", W / 2, 292);
  }
  c.textAlign = "left";
  const top = 316, gap = 22;
  if (k.domanda && k.opzioni && k.opzioni.length) {
    const n = k.opzioni.length, bw = (W - 68 - gap * (n - 1)) / n;
    k.opzioni.forEach((o, i) => this.bottone(c, 34 + i * (bw + gap), top, bw, H - top - 34, o,
      () => rispondiConferma(true, false, o), { dim: 34, colore: "57,213,255" }));
    return;
  }
  const secondari = !k.domanda;
  const bh = secondari ? H - top - 34 - 86 : H - top - 34, bw = (W - 68 - gap) / 2;
  this.bottone(c, 34, top, bw, bh, "SÌ", () => rispondiConferma(true), { dim: 64, peso: 800, colore: "77,255,184", sotto: "procedi" });
  this.bottone(c, 34 + bw + gap, top, bw, bh, "NO", () => rispondiConferma(false), { dim: 64, peso: 800, colore: "255,84,104", sotto: "lascia stare" });
  if (secondari) {
    if (k.sempre_ok) this.bottone(c, 34, H - 108, bw, 74, "Sì, sempre", () => rispondiConferma(true, true), { dim: 26 });
    this.bottone(c, 34 + bw + gap, H - 108, bw, 74, "No, mai", () => rispondiConferma(false, true), { dim: 26 });
  }
};
let confermaAperta = 0;
function rispondiConferma(ok, sempre, testo) {
  const k = S.conferma;
  if (!k || performance.now() - confermaAperta < 500) return;   // niente conferme per un tocco di passaggio
  const corpo = { id: k.id, ok, sempre: !!sempre };
  if (testo !== undefined) corpo.testo = testo;
  apiJ("/jarvis/conferma", corpo).catch((e) => avviso(e.message));
  mostraConferma(null);
}
function mostraConferma(k) {
  if (k && S.conferma && k.id === S.conferma.id) return;
  S.conferma = k;
  pConferma.gruppo.visible = !!k;
  if (!k) return;
  confermaAperta = performance.now();
  pConferma.comparsa = 0; pConferma.sporco = true;
  // davanti agli occhi, a 55 cm, appena sotto la linea dello sguardo
  const testa = posizioneTesta(), avanti = new THREE.Vector3(0, 0, -1).applyQuaternion(orientamentoTesta());
  avanti.y = 0; avanti.normalize();
  const p = testa.clone().addScaledVector(avanti, 0.55); p.y = testa.y - 0.08;
  ancora.worldToLocal(p);
  pConferma.gruppo.position.copy(p);
  pConferma.gruppo.lookAt(testa);
  bip(660, 0.12, 0.08); setTimeout(() => bip(880, 0.12, 0.08), 140);
}

// ---- avvisi volanti (promemoria, temperature, errori)
const pAvviso = new Pannello("avviso", "AVVISO", 0.5, 0.12, { sempreDavanti: true, visibile: false, colore: "255,179,65" });
pAvviso.testo = "";
pAvviso.contenuto = function (c, W) {
  c.font = "600 34px " + FONT; c.fillStyle = "#fff1d6";
  c.fillText(taglia(c, this.testo, W - 80), 36, 126);
};
let avvisoFino = 0;
function avviso(testo) {
  pAvviso.testo = testo; pAvviso.sporco = true; pAvviso.comparsa = 0; pAvviso.gruppo.visible = true;
  avvisoFino = performance.now() + 6000;
  const testa = posizioneTesta(), avanti = new THREE.Vector3(0, 0, -1).applyQuaternion(orientamentoTesta());
  avanti.y = 0; avanti.normalize();
  const p = testa.clone().addScaledVector(avanti, 0.75); p.y = testa.y + 0.22;
  ancora.worldToLocal(p);
  pAvviso.gruppo.position.copy(p);
  pAvviso.gruppo.lookAt(testa);
}

// ------------------------------------------------------------------ il PC nel visore: cartelle, foto, video, finestre
const INTESTAZIONI = { "X-Token": TOKEN };
const urlJ = (percorso, parametri) => JARVIS + percorso + "?" + new URLSearchParams(parametri).toString();
async function bitmapDa(url) {
  const r = await fetch(url, { headers: INTESTAZIONI });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).errore || "errore " + r.status);
  return createImageBitmap(await r.blob());
}
// anteprime: al massimo 4 richieste insieme, in cache per percorso
const anteprime = new Map(), codaAnteprime = [];
let anteprimeInCorso = 0;
function anteprimaDi(percorso, lato, pannello) {
  const k = lato + "|" + percorso, c = anteprime.get(k);
  if (c && c !== "carico") return c === "errore" ? null : c;
  if (!c) { anteprime.set(k, "carico"); codaAnteprime.push({ k, percorso, lato, pannello }); pompa(); }
  return null;
}
function pompa() {
  while (anteprimeInCorso < 4 && codaAnteprime.length) {
    const x = codaAnteprime.shift();
    anteprimeInCorso++;
    bitmapDa(urlJ("/jarvis/file/anteprima", { percorso: x.percorso, lato: x.lato }))
      .then((b) => { anteprime.set(x.k, b); x.pannello.sporco = true; })
      .catch(() => anteprime.set(x.k, "errore"))
      .finally(() => { anteprimeInCorso--; pompa(); });
  }
  if (anteprime.size > 500) {
    for (const [k, v] of anteprime) { if (v && v.close) v.close(); anteprime.delete(k); if (anteprime.size < 400) break; }
  }
}
function disegnaCopri(c, img, x, y, w, h) {   // l'immagine riempie il riquadro (ritagliata al centro)
  const s = Math.max(w / img.width, h / img.height), sw = w / s, sh = h / s;
  c.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
}
function disegnaContieni(c, img, x, y, w, h) {   // l'immagine intera nel riquadro
  const s = Math.min(w / img.width, h / img.height), dw = img.width * s, dh = img.height * s;
  c.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  return { x: x + (w - dw) / 2, y: y + (h - dh) / 2, w: dw, h: dh };
}
function iconaCartella(c, x, y, w, h, col) {
  c.save();
  c.fillStyle = "rgba(" + col + ",0.18)"; c.strokeStyle = "rgba(" + col + ",0.95)"; c.lineWidth = 4;
  c.shadowColor = "rgba(" + col + ",0.8)"; c.shadowBlur = 16;
  c.beginPath();
  c.moveTo(x, y + h * 0.18); c.lineTo(x + w * 0.38, y + h * 0.18); c.lineTo(x + w * 0.46, y + h * 0.04);
  c.lineTo(x + w, y + h * 0.04); c.lineTo(x + w, y + h); c.lineTo(x, y + h); c.closePath();
  c.fill(); c.stroke();
  c.shadowBlur = 0; c.fillStyle = "rgba(" + col + ",0.35)"; c.fillRect(x + 6, y + h * 0.3, w - 12, 3);
  c.restore();
}
function iconaFile(c, x, y, w, h, etichetta, col) {
  c.save();
  c.strokeStyle = "rgba(" + col + ",0.9)"; c.fillStyle = "rgba(" + col + ",0.12)"; c.lineWidth = 4;
  const p = w * 0.28;
  c.beginPath(); c.moveTo(x, y); c.lineTo(x + w - p, y); c.lineTo(x + w, y + p); c.lineTo(x + w, y + h); c.lineTo(x, y + h);
  c.closePath(); c.fill(); c.stroke();
  c.beginPath(); c.moveTo(x + w - p, y); c.lineTo(x + w - p, y + p); c.lineTo(x + w, y + p); c.stroke();
  c.fillStyle = "rgba(" + col + ",1)"; c.font = "700 " + Math.round(w * 0.24) + "px " + MONO; c.textAlign = "center";
  c.fillText(etichetta, x + w / 2, y + h * 0.66); c.textAlign = "left";
  c.restore();
}
const COLORE_TIPO = { cartella: "57,213,255", disco: "57,213,255", immagine: "77,255,184", video: "255,179,65",
  testo: "223,246,255", pdf: "255,84,104", audio: "190,140,255", file: "143,180,200" };

// pannelli che si aprono a richiesta: davanti a te, uscendo dalla sfera
const dinamici = [];
function apriDinamico(p, larghezzaOccupata) {
  while (dinamici.length >= 5) dinamici.shift().chiudi();
  dinamici.push(p);
  const vecchio = p.alla_chiusura;
  p.alla_chiusura = () => { const i = dinamici.indexOf(p); if (i >= 0) dinamici.splice(i, 1); vecchio && vecchio(); aVentaglio(); };
  ancora.add(p.gruppo);
  p.gruppo.position.copy(ancora.worldToLocal(sfera.gruppo.getWorldPosition(new THREE.Vector3())));
  p.gruppo.lookAt(posizioneTesta());
  p.comparsa = 0.25;
  aVentaglio();
  bip(520, 0.06, 0.05); setTimeout(() => bip(780, 0.08, 0.05), 70);
  return p;
}
// il più recente davanti agli occhi, gli altri si spostano ai lati ad arco (come Tony Stark che sposta gli schermi)
function aVentaglio() {
  const testa = posizioneTesta(), avanti = new THREE.Vector3(0, 0, -1).applyQuaternion(orientamentoTesta());
  avanti.y = 0; avanti.normalize();
  const ANGOLI = [0, -60, 60, -92, 92];
  [...dinamici].reverse().forEach((p, k) => {
    if (p.spostatoDaTe) return;   // quelli che hai messo tu con le mani restano dove sono
    const a = THREE.MathUtils.degToRad(ANGOLI[k] || 0), r = k === 0 ? 0.64 : 0.86;
    const dir = avanti.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), -a);
    const dest = testa.clone().addScaledVector(dir, r);
    dest.y = testa.y - (k === 0 ? 0.06 : 0.02);
    // quello davanti a grandezza piena, gli altri più piccoli ai lati: si riprendono toccandoli o col pizzico
    p.volo = { t: 0, da: p.gruppo.position.clone(), a: ancora.worldToLocal(dest),
      s0: p.gruppo.scale.x, s1: k === 0 ? 1 : 0.62 };
  });
}
function chiudiDinamici() { [...dinamici].forEach((p) => p.chiudi()); }

// ---- esploratore delle cartelle
class Esplora extends Pannello {
  constructor(percorso) {
    super("esplora", "FILE", 0.9, 0.64, { chiudibile: true, denso: true });
    this.dati = null; this.pagina = 0; this.errore = "";
    this.vai(percorso || "");
  }
  vai(percorso) {
    this.percorso = percorso; this.pagina = 0; this.dati = null; this.errore = ""; this.sporco = true;
    api(JARVIS, "/jarvis/file/elenco?percorso=" + encodeURIComponent(percorso))
      .then((d) => { this.dati = d; this.sporco = true; })
      .catch((e) => { this.errore = e.message; this.sporco = true; });
  }
  swipe(dir) {
    const pagine = this.dati ? Math.ceil(this.dati.voci.length / 18) : 1;
    this.pagina = Math.max(0, Math.min(pagine - 1, this.pagina + dir)); this.sporco = true;
  }
  contenuto(c, W, H) {
    const d = this.dati;
    this.titolo = "FILE · " + (d ? d.nome : "…").toUpperCase();
    this.bottone(c, W - 270, 16, 76, 56, "⌂", () => this.vai(""), { dim: 30 });
    this.bottone(c, W - 180, 16, 76, 56, "⬆", () => d && d.su !== null && this.vai(d.su || ""), { dim: 30 });
    if (this.errore || !d) {
      c.font = "400 30px " + FONT; c.fillStyle = this.errore ? "#ff5468" : "rgba(223,246,255,0.5)";
      c.fillText(this.errore || "Leggo la cartella…", 40, 170); return;
    }
    c.font = "400 20px " + MONO; c.fillStyle = "rgba(223,246,255,0.45)";
    c.fillText(taglia(c, d.percorso || "Questo PC", W - 80), 36, 104);
    const col = 6, righe = 3, gap = 16, tw = (W - 68 - gap * (col - 1)) / col, th = (H - 250 - gap * (righe - 1)) / righe;
    const per = col * righe, pagine = Math.max(1, Math.ceil(d.voci.length / per));
    const immagini = d.voci.filter((v) => v.tipo === "immagine");
    d.voci.slice(this.pagina * per, this.pagina * per + per).forEach((v, i) => {
      const x = 34 + (i % col) * (tw + gap), y = 126 + Math.floor(i / col) * (th + gap), tc = COLORE_TIPO[v.tipo] || COLORE_TIPO.file;
      this.bottone(c, x, y, tw, th, "", () => {
        if (v.tipo === "cartella" || v.tipo === "disco") this.vai(v.percorso);
        else if (v.tipo === "immagine") apriDinamico(new Visualizzatore(immagini, immagini.indexOf(v)));
        else if (v.tipo === "video" || v.tipo === "testo") apriDinamico(new Visualizzatore([v], 0));
        else apriSulPc(v);
      }, { colore: tc, raggio: 14 });
      const area = { x: x + 10, y: y + 10, w: tw - 20, h: th - 54 };
      if (v.tipo === "immagine") {
        const img = anteprimaDi(v.percorso, 256, this);
        if (img) { c.save(); rettangolo(c, area.x, area.y, area.w, area.h, 10); c.clip(); disegnaCopri(c, img, area.x, area.y, area.w, area.h); c.restore(); }
        else iconaFile(c, area.x + area.w * 0.3, area.y + 10, area.w * 0.4, area.h - 20, "IMG", tc);
      } else if (v.tipo === "cartella" || v.tipo === "disco") {
        iconaCartella(c, area.x + area.w * 0.18, area.y + area.h * 0.18, area.w * 0.64, area.h * 0.64, tc);
      } else {
        const est = (v.nome.split(".").pop() || "").slice(0, 4).toUpperCase();
        iconaFile(c, area.x + area.w * 0.3, area.y + 10, area.w * 0.4, area.h - 20, v.tipo === "video" ? "▶" : est, tc);
      }
      c.font = "500 20px " + FONT; c.fillStyle = "#e8fbff"; c.textAlign = "center";
      c.fillText(taglia(c, v.nome, tw - 16), x + tw / 2, y + th - 20); c.textAlign = "left";
    });
    if (!d.voci.length) { c.font = "400 28px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)"; c.fillText("Cartella vuota", 40, 190); }
    const by = H - 104;
    this.bottone(c, 34, by, 150, 76, "◀", () => this.swipe(-1), { dim: 34 });
    this.bottone(c, W - 184, by, 150, 76, "▶", () => this.swipe(1), { dim: 34 });
    c.font = "500 24px " + MONO; c.fillStyle = "rgba(223,246,255,0.7)"; c.textAlign = "center";
    c.fillText("pagina " + (this.pagina + 1) + " / " + pagine + " · " + d.voci.length + " elementi · scorri con la mano", W / 2, by + 40);
    c.textAlign = "left";
  }
}
function apriSulPc(v) {
  api(JARVIS, "/jarvis/file/apri", { percorso: v.percorso }).then(() => avviso("Aperto sul PC: " + v.nome))
    .catch((e) => avviso(e.message));
}

// ---- foto, video e testi
class Visualizzatore extends Pannello {
  constructor(elenco, indice) {
    super("visualizzatore", "FOTO", 1.02, 0.7, { chiudibile: true, denso: true, colore: "77,255,184" });
    this.elenco = elenco; this.indice = Math.max(0, indice); this.img = null; this.testo = null; this.scorri = 0;
    this.video = null; this.pianoVideo = null;
    this.carica();
  }
  get voce() { return this.elenco[this.indice]; }
  swipe(dir) { if (this.elenco.length > 1) { this.indice = (this.indice + dir + this.elenco.length) % this.elenco.length; this.carica(); bip(900 + dir * 120, 0.05, 0.04); } }
  carica() {
    const v = this.voce;
    this.img = null; this.testo = null; this.scorri = 0; this.sporco = true;
    this.fermaVideo();
    if (v.tipo === "immagine") {
      const k = this.indice;
      bitmapDa(urlJ("/jarvis/file/anteprima", { percorso: v.percorso, lato: 2048 }))
        .then((b) => { if (k === this.indice) { this.img = b; this.sporco = true; } }).catch((e) => avviso(e.message));
      // la successiva si prepara già
      const s = this.elenco[(this.indice + 1) % this.elenco.length];
      if (s && s.tipo === "immagine") anteprimaDi(s.percorso, 256, this);
    } else if (v.tipo === "testo") {
      fetch(urlJ("/jarvis/file/dati", { percorso: v.percorso }), { headers: INTESTAZIONI })
        .then((r) => r.text()).then((t) => { this.testo = t; this.sporco = true; });
    } else if (v.tipo === "video") {
      const el = document.createElement("video");
      el.crossOrigin = "anonymous"; el.playsInline = true; el.loop = false;
      el.src = urlJ("/jarvis/file/dati", { percorso: v.percorso, t: TOKEN });
      el.play().catch(() => {});
      el.addEventListener("loadedmetadata", () => { this.posizionaVideo(); this.sporco = true; });
      el.addEventListener("play", () => { if (renderer.xr.isPresenting && !(cinema.attivo && cinema.sorgente === this)) cinema.entra(this); });
      this.video = el;
      const tex = new THREE.VideoTexture(el);
      tex.colorSpace = THREE.SRGBColorSpace;
      this.pianoVideo = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
      this.pianoVideo.position.z = 0.002;
      this.pianoVideo.visible = false;
      this.gruppo.add(this.pianoVideo);
    }
  }
  posizionaVideo() {
    const el = this.video;
    if (!el || !el.videoWidth) return;
    const aw = this.w - 0.07, ah = this.h - 0.22;
    const s = Math.min(aw / el.videoWidth, ah / el.videoHeight);
    this.pianoVideo.scale.set(el.videoWidth * s, el.videoHeight * s, 1);
    this.pianoVideo.position.y = 0.01;
    this.pianoVideo.visible = true;
  }
  fermaVideo() {
    if (this.video) { this.video.pause(); this.video.removeAttribute("src"); this.video.load(); this.video = null; }
    if (this.pianoVideo) { this.gruppo.remove(this.pianoVideo); this.pianoVideo.material.map.dispose(); this.pianoVideo = null; }
  }
  ogni() { if (this.video && !this.video.paused) this.sporcoVideo = (this.sporcoVideo || 0) + 1; if (this.sporcoVideo > 15) { this.sporcoVideo = 0; this.sporco = true; } }
  contenuto(c, W, H) {
    const v = this.voce;
    this.titolo = (v.tipo === "video" ? "VIDEO" : v.tipo === "testo" ? "TESTO" : "FOTO") +
      (this.elenco.length > 1 ? " · " + (this.indice + 1) + " / " + this.elenco.length : "");
    c.font = "400 22px " + FONT; c.fillStyle = "rgba(223,246,255,0.7)"; c.textAlign = "right";
    c.fillText(taglia(c, v.nome, W * 0.45), W - 120, 46); c.textAlign = "left";
    const area = { x: 34, y: 92, w: W - 68, h: H - 92 - 118 };
    if (v.tipo === "immagine") {
      if (this.img) { c.save(); rettangolo(c, area.x, area.y, area.w, area.h, 14); c.clip(); disegnaContieni(c, this.img, area.x, area.y, area.w, area.h); c.restore(); }
      else { c.font = "400 30px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)"; c.textAlign = "center"; c.fillText("Carico la foto…", W / 2, H / 2); c.textAlign = "left"; }
      // striscia di miniature: tocca per saltare
      const n = Math.min(9, this.elenco.length);
      if (n > 1) {
        const tw = 100, gap = 10, x0 = W / 2 - (n * (tw + gap) - gap) / 2, y0 = H - 104;
        for (let k = 0; k < n; k++) {
          const idx = (this.indice - Math.floor(n / 2) + k + this.elenco.length) % this.elenco.length, e = this.elenco[idx];
          const x = x0 + k * (tw + gap);
          this.bottone(c, x, y0, tw, 76, "", () => { this.indice = idx; this.carica(); },
            { colore: idx === this.indice ? "255,255,255" : "77,255,184", raggio: 10 });
          const mini = anteprimaDi(e.percorso, 256, this);
          if (mini) { c.save(); rettangolo(c, x + 4, y0 + 4, tw - 8, 68, 8); c.clip(); disegnaCopri(c, mini, x + 4, y0 + 4, tw - 8, 68); c.restore(); }
        }
        this.bottone(c, 34, y0, 120, 76, "‹", () => this.swipe(-1), { dim: 44 });
        this.bottone(c, W - 154, y0, 120, 76, "›", () => this.swipe(1), { dim: 44 });
      } else this.bottone(c, W - 260, H - 104, 226, 76, "⧉ APRI SUL PC", () => apriSulPc(v), { dim: 24 });
    } else if (v.tipo === "testo") {
      c.font = "400 24px " + MONO; c.fillStyle = "#dff6ff";
      const righe = aCapo(c, this.testo == null ? "Carico…" : this.testo, area.w - 20);
      const per = Math.floor(area.h / 32);
      this.scorri = Math.max(0, Math.min(this.scorri, Math.max(0, righe.length - per)));
      righe.slice(this.scorri, this.scorri + per).forEach((r, k) => c.fillText(r, area.x + 10, area.y + 26 + k * 32));
      this.bottone(c, 34, H - 104, 160, 76, "▲", () => { this.scorri -= per - 2; this.sporco = true; }, { dim: 30 });
      this.bottone(c, 210, H - 104, 160, 76, "▼", () => { this.scorri += per - 2; this.sporco = true; }, { dim: 30 });
      this.bottone(c, W - 260, H - 104, 226, 76, "⧉ APRI SUL PC", () => apriSulPc(v), { dim: 24 });
    } else if (v.tipo === "video") {
      const el = this.video;
      c.fillStyle = "#000"; c.fillRect(area.x, area.y, area.w, area.h);
      const y0 = H - 104, durata = el && el.duration || 0, ora = el ? el.currentTime : 0;
      this.bottone(c, 34, y0, 110, 76, "−10", () => { if (el) el.currentTime = Math.max(0, el.currentTime - 10); }, { dim: 26 });
      this.bottone(c, 154, y0, 110, 76, el && !el.paused ? "❚❚" : "▶", () => { if (el) { el.paused ? el.play() : el.pause(); this.sporco = true; } }, { dim: 30 });
      this.bottone(c, 274, y0, 110, 76, "+10", () => { if (el) el.currentTime = Math.min(durata, el.currentTime + 10); }, { dim: 26 });
      // barra di avanzamento: toccala per saltare
      const bx = 404, bw = W - bx - 270;
      this.bottone(c, bx, y0, bw, 76, "", () => {}, { colore: "255,179,65" });
      const i = this.bottoni.length - 1;
      this.bottoni[i].azione = () => { if (el && durata) el.currentTime = durata * this.ultimaX; };
      this.bottoni[i].barra = { x: bx, w: bw };
      c.fillStyle = "rgba(255,179,65,0.9)"; c.fillRect(bx + 10, y0 + 34, (bw - 20) * (durata ? ora / durata : 0), 8);
      c.font = "500 22px " + MONO; c.fillStyle = "#fff1d6";
      c.fillText(tempo(ora) + " / " + tempo(durata), bx + 14, y0 + 26);
      this.bottone(c, W - 254, y0, 220, 76, "⧉ SUL PC", () => apriSulPc(v), { dim: 24 });
      if (el) this.bottone(c, W - 330, 16, 220, 56, "🎬 CINEMA", () => cinema.entra(this), { dim: 22, colore: "255,179,65" });
      if (!this.pianoVideo || !this.pianoVideo.visible) {
        c.font = "400 26px " + FONT; c.fillStyle = "rgba(223,246,255,0.6)"; c.textAlign = "center";
        c.fillText(/\.(mkv)$/i.test(v.nome) ? "MKV non si riproduce nel visore: aprilo sul PC" : "Carico il video…", W / 2, H / 2);
        c.textAlign = "left";
      }
    }
  }
  premi(u, v, origine) {
    const i = this.indiceIn(u, v), b = this.bottoni[i];
    if (b && b.barra) this.ultimaX = Math.max(0, Math.min(1, (u * this.cw - b.x - 10) / (b.barra.w - 20)));
    return super.premi(u, v, origine);
  }
  chiudi() { this.fermaVideo(); super.chiudi(); }
}
function tempo(s) { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }

// ---- una finestra del PC (o lo schermo intero) in diretta: si usa toccandola
class FinestraPc extends Pannello {
  constructor(hwnd, titolo, numeroSchermo) {
    super("finestra", "FINESTRA", 1.0, 0.66, { chiudibile: true, denso: true, colore: "143,240,255" });
    this.hwnd = hwnd || 0; this.nome = titolo || "Schermo"; this.schermo = numeroSchermo || 0;
    this.img = null; this.carico = false; this.ultimo = 0; this.errore = ""; this.modo = "clic"; this.proporzione = 0;
    this.barra = 84;   // altezza in pixel della barra dei comandi sopra l'immagine
  }
  ogni(dt, t) {
    // un fotogramma ogni ~180 ms finché è aperta (più lento se sta caricando)
    if (this.carico || performance.now() - this.ultimo < 180) return;
    this.carico = true; this.ultimo = performance.now();
    const url = this.schermo ? urlJ("/jarvis/schermo/immagine", { numero: this.schermo, lato: 1600 })
      : urlJ("/jarvis/finestre/immagine", { hwnd: this.hwnd, lato: 1600 });
    bitmapDa(url).then((b) => {
      if (this.img && this.img.close) this.img.close();
      this.img = b; this.errore = "";
      const p = b.width / b.height;
      if (Math.abs(p - this.proporzione) > 0.02) {   // il vetro prende le proporzioni della finestra
        this.proporzione = p;
        const w = Math.min(1.25, Math.max(0.7, 0.62 * p));
        this.dimensioni(w, w / p + (84 + this.barra) / PX_M);
      }
      this.sporco = true;
    }).catch((e) => { this.errore = e.message; this.sporco = true; this.ultimo += 1500; })
      .finally(() => { this.carico = false; });
  }
  contenuto(c, W, H) {
    this.titolo = (this.schermo ? "SCHERMO " + this.schermo : "FINESTRA") + " · " + this.nome.toUpperCase();
    const b = this.barra, y = H - b + 8;   // comandi in basso, l'immagine sopra
    if (this.img) c.drawImage(this.img, 6, 84, W - 12, H - 84 - b);
    else { c.font = "400 28px " + FONT; c.fillStyle = this.errore ? "#ff5468" : "rgba(223,246,255,0.5)"; c.fillText(this.errore || "Collego la finestra…", 40, 160); }
    const bw = 140, gap = 10;
    [["clic", "TOCCO"], ["doppio", "DOPPIO"], ["destro", "DESTRO"]].forEach(([m, n], i) =>
      this.bottone(c, 34 + i * (bw + gap), y, bw, b - 16, n, () => { this.modo = m; this.sporco = true; },
        { dim: 22, pieno: this.modo === m }));
    this.bottone(c, 34 + 3 * (bw + gap) + 20, y, 90, b - 16, "▲", () => this.manda({ tipo: "rotella", scatti: 5, x: 0.5, y: 0.5 }), { dim: 26 });
    this.bottone(c, 34 + 3 * (bw + gap) + 120, y, 90, b - 16, "▼", () => this.manda({ tipo: "rotella", scatti: -5, x: 0.5, y: 0.5 }), { dim: 26 });
    this.bottone(c, 34 + 3 * (bw + gap) + 220, y, 120, b - 16, "INVIO", () => this.manda({ tipo: "tasti", tasti: "enter" }), { dim: 22 });
    this.bottone(c, W - 190, y, 156, b - 16, "⟳ DAVANTI", () => this.manda({ tipo: "davanti" }), { dim: 22 });
    // modalità cinema: diretta fluida a schermo grande, stanza oscurata (giochi e video)
    this.bottone(c, W - 330, 16, 220, 56, "🎮 CINEMA", () => cinema.entra(this), { dim: 22, colore: "255,179,65" });
  }
  manda(azione) {
    return api(JARVIS, "/jarvis/finestre/azione", Object.assign({ hwnd: this.hwnd }, azione))
      .then(() => { this.ultimo = 0; }).catch((e) => avviso(e.message));
  }
  // un tocco sull'immagine diventa un clic nello stesso punto della finestra vera
  premi(u, v, origine) {
    if (this.portaAvanti()) return true;
    const x = u * this.cw, y = (1 - v) * this.ch;
    if (y > 84 && y < this.ch - this.barra && this.indiceIn(u, v) < 0) {
      const rx = (x - 6) / (this.cw - 12), ry = (y - 84) / (this.ch - 84 - this.barra);
      bip(1300, 0.04, 0.05); vibra(origine);
      this.manda({ tipo: this.modo, x: rx, y: ry });
      if (this.modo !== "clic") { this.modo = "clic"; this.sporco = true; }
      return true;
    }
    return super.premi(u, v, origine);
  }
  swipe(dir) { this.manda({ tipo: "rotella", scatti: dir * 4, x: 0.5, y: 0.5 }); }
  chiudi() { if (this.img && this.img.close) this.img.close(); super.chiudi(); }
}

// ---- elenco delle finestre aperte sul PC
class ElencoFinestre extends Pannello {
  constructor() {
    super("finestre", "FINESTRE DEL PC", 0.6, 0.62, { chiudibile: true, denso: true, colore: "143,240,255" });
    this.elenco = null; this.aggiorna_();
  }
  aggiorna_() { api(JARVIS, "/jarvis/finestre").then((d) => { this.elenco = d.finestre; this.sporco = true; }).catch((e) => avviso(e.message)); }
  contenuto(c, W, H) {
    this.bottone(c, W - 180, 16, 76, 56, "⟳", () => this.aggiorna_(), { dim: 30 });
    const l = this.elenco;
    if (!l) { c.font = "400 28px " + FONT; c.fillStyle = "rgba(223,246,255,0.5)"; c.fillText("Leggo le finestre…", 40, 160); return; }
    l.slice(0, 7).forEach((f, i) => {
      const y = 100 + i * 106;
      this.bottone(c, 34, y, W - 68, 92, "", () => { apriDinamico(new FinestraPc(f.hwnd, f.titolo)); this.chiudi(); }, { colore: f.davanti ? "77,255,184" : "143,240,255" });
      c.font = "600 28px " + FONT; c.fillStyle = "#e8fbff"; c.fillText(taglia(c, f.titolo, W - 140), 60, y + 40);
      c.font = "400 20px " + MONO; c.fillStyle = "rgba(223,246,255,0.55)";
      c.fillText((f.processo || "") + (f.ridotta ? " · ridotta a icona" : "") + (f.davanti ? " · davanti" : ""), 60, y + 72);
    });
    c.font = "400 26px " + MONO; c.fillStyle = "rgba(223,246,255,0.6)"; c.textAlign = "center";
    c.fillText("SCHERMO INTERO", W / 2, H - 140); c.textAlign = "left";
    this.bottone(c, 34, H - 112, W - 68, 84, "🖥  MOSTRA LO SCHERMO DEL PC", () => { apriDinamico(new FinestraPc(0, "Schermo 1", 1)); this.chiudi(); }, { dim: 26 });
  }
}

// ---- barra dei comandi sopra la sfera
const pDock = new Pannello("dock", "PC", 0.56, 0.12);
pDock.contenuto = function (c, W, H) {
  const voci = [["📁 FILE", () => apriEsplora("")], ["🪟 FINESTRE", () => apriDinamico(new ElencoFinestre())],
    ["🖥 SCHERMO", () => apriDinamico(new FinestraPc(0, "Schermo 1", 1))], ["✕ CHIUDI", chiudiDinamici]];
  const gap = 12, bw = (W - 150 - gap * (voci.length - 1)) / voci.length;
  voci.forEach(([n, f], i) => this.bottone(c, 116 + i * (bw + gap), 22, bw, H - 44, n, f, { dim: 24, colore: i === 3 ? "255,84,104" : "57,213,255" }));
};
pDock.disegna = function () {   // niente titolo grande: è una barra
  const c = this.ctx, W = this.cw, H = this.ch;
  this.bottoni = [];
  c.clearRect(0, 0, W, H);
  rettangolo(c, 3, 3, W - 6, H - 6, 30); c.fillStyle = "rgba(4,18,28,0.72)"; c.fill();
  c.lineWidth = 3; c.strokeStyle = "rgba(57,213,255,0.45)"; c.stroke();
  c.font = "600 26px " + MONO; c.fillStyle = "#39d5ff"; c.textBaseline = "middle"; c.fillText("PC", 34, H / 2);
  this.contenuto(c, W, H);
  this.tex.needsUpdate = true; this.sporco = false;
};

function apriEsplora(percorso) {
  const gia = dinamici.find((p) => p instanceof Esplora);
  if (gia) { gia.vai(percorso); gia.comparsa = 0.4; return gia; }
  return apriDinamico(new Esplora(percorso));
}
// richieste dalla voce di Jarvis ("aprimi la cartella delle foto sul desktop")
function eventoVisore(v) {
  if (v.cosa === "cartella") apriEsplora(v.percorso);
  else if (v.cosa === "file_radici") apriEsplora("");
  else if (v.cosa === "file") {
    const voce = { percorso: v.percorso, nome: v.percorso.split(/[\\/]/).pop(), tipo: v.tipo };
    if (v.tipo === "immagine") {   // con le altre foto della stessa cartella, da sfogliare
      const cartella = v.percorso.replace(/[\\/][^\\/]*$/, "");
      api(JARVIS, "/jarvis/file/elenco?percorso=" + encodeURIComponent(cartella)).then((d) => {
        const foto = d.voci.filter((x) => x.tipo === "immagine");
        const i = foto.findIndex((x) => x.percorso === v.percorso);
        apriDinamico(new Visualizzatore(i >= 0 ? foto : [voce], Math.max(0, i)));
      }).catch(() => apriDinamico(new Visualizzatore([voce], 0)));
    } else apriDinamico(new Visualizzatore([voce], 0));
  } else if (v.cosa === "finestra") apriDinamico(new FinestraPc(v.hwnd, v.titolo));
  else if (v.cosa === "schermo") apriDinamico(new FinestraPc(0, "Schermo " + (v.numero || 1), v.numero || 1));
  else if (v.cosa === "finestre") apriDinamico(new ElencoFinestre());
  else if (v.cosa === "chiudi") chiudiDinamici();
}

// ------------------------------------------------------------------ disposizione
const FISSI = [pConversazione, pTelemetria, pApp, pMedia, pPromemoria, pDock];
const DEFAULT = {   // [angolo in gradi, distanza, altezza rispetto agli occhi]
  conversazione: [0, 0.8, -0.46], telemetria: [-40, 0.82, -0.06], app: [40, 0.82, -0.06],
  media: [40, 0.78, -0.43], promemoria: [-40, 0.78, -0.53], dock: [0, 0.8, 0.27],
};
FISSI.forEach((p) => ancora.add(p.gruppo));
ancora.add(pConferma.gruppo, pAvviso.gruppo, sfera.gruppo);

function disposizioneIniziale(dimentica) {
  if (dimentica) scrivi("xr-disposizione", "");
  centraAncora();
  let salvata = {};
  try { salvata = JSON.parse(leggi("xr-disposizione") || "{}"); } catch (e) { salvata = {}; }
  const occhi = new THREE.Vector3(0, altezzaOcchi, 0);
  FISSI.forEach((p, i) => {
    if (p.nelCestino) { p.nelCestino = false; p.gruppo.visible = true; }
    const s = salvata[p.id];
    if (s) {
      p.gruppo.position.fromArray(s.p); p.gruppo.quaternion.fromArray(s.q); p.gruppo.scale.setScalar(s.s || 1);
    } else {
      const [ang, r, dy] = DEFAULT[p.id], a = THREE.MathUtils.degToRad(ang);
      p.gruppo.position.set(Math.sin(a) * r, altezzaOcchi + dy, -Math.cos(a) * r);
      p.gruppo.scale.setScalar(1);
      p.gruppo.lookAt(ancora.localToWorld(occhi.clone()));   // lookAt tiene già conto della rotazione dell'ancora
    }
    p.comparsa = -i * 0.12;   // entrano uno dopo l'altro
  });
  sfera.gruppo.position.set(0, altezzaOcchi + 0.02, -0.74);
  if (dimentica) { bip(520, 0.1, 0.06); avviso("Pannelli rimessi attorno a te"); }
}
function salvaDisposizione() {
  const d = {};
  FISSI.forEach((p) => { d[p.id] = { p: p.gruppo.position.toArray(), q: p.gruppo.quaternion.toArray(), s: p.gruppo.scale.x }; });
  scrivi("xr-disposizione", JSON.stringify(d));
}
function centraAncora() {
  if (!renderer.xr.isPresenting) {   // anteprima: la scena attorno all'origine, la camera un passo indietro
    ancora.position.set(0, 0, 0); ancora.rotation.set(0, 0, 0); ancora.updateMatrixWorld(true); altezzaOcchi = 1.6;
    return;
  }
  const t = posizioneTesta(), o = orientamentoTesta();
  const yaw = new THREE.Euler().setFromQuaternion(o, "YXZ").y;
  ancora.position.set(t.x, 0, t.z);
  ancora.rotation.set(0, yaw, 0);
  ancora.updateMatrixWorld(true);
  altezzaOcchi = Math.max(1.0, Math.min(2.0, t.y));
}
const _tp = new THREE.Vector3(), _tq = new THREE.Quaternion();
function camAttiva() { return renderer.xr.isPresenting ? renderer.xr.getCamera() : camera; }
function posizioneTesta() { return camAttiva().getWorldPosition(_tp).clone(); }
function orientamentoTesta() { return camAttiva().getWorldQuaternion(_tq).clone(); }

// ------------------------------------------------------------------ suoni e vibrazione
let audioUI = null;
function bip(freq, durata, volume) {
  try {
    audioUI = audioUI || new AudioContext();
    const o = audioUI.createOscillator(), g = audioUI.createGain(), t = audioUI.currentTime;
    o.type = "sine"; o.frequency.setValueAtTime(freq, t); o.frequency.exponentialRampToValueAtTime(freq * 1.25, t + durata);
    g.gain.setValueAtTime(volume, t); g.gain.exponentialRampToValueAtTime(0.0001, t + durata);
    o.connect(g); g.connect(audioUI.destination); o.start(t); o.stop(t + durata + 0.02);
  } catch (e) { /* audio non disponibile */ }
}
function vibra(sorgente) {
  const h = sorgente && sorgente.gamepad && sorgente.gamepad.hapticActuators && sorgente.gamepad.hapticActuators[0];
  if (h && h.pulse) h.pulse(0.45, 35);
}

// ------------------------------------------------------------------ microfono del visore (tieni e parla)
let micCtx = null, micCampioni = [], micLivello = 0;
async function preparaMicrofono() {
  if (micCtx) return true;
  try {
    const flusso = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true,
      noiseSuppression: true, autoGainControl: true } });
    try { micCtx = new AudioContext({ sampleRate: 16000 }); } catch (e) { micCtx = new AudioContext(); }
    const sorgente = micCtx.createMediaStreamSource(flusso);
    const proc = micCtx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => {
      const d = e.inputBuffer.getChannelData(0);
      let s = 0;
      for (let i = 0; i < d.length; i += 8) s += d[i] * d[i];
      micLivello = Math.sqrt(s / (d.length / 8));
      if (S.registra) micCampioni.push(new Float32Array(d));
    };
    sorgente.connect(proc); proc.connect(micCtx.destination);
    return true;
  } catch (e) {
    avviso("Microfono non disponibile: " + e.message);
    return false;
  }
}
function iniziaParlato() {
  if (!micCtx) { preparaMicrofono(); avviso("Concedi il microfono e riprova"); return false; }
  micCtx.resume();
  micCampioni = []; S.registra = true;
  bip(880, 0.07, 0.06);
  return true;
}
function fineParlato() {
  if (!S.registra) return;
  S.registra = false;
  bip(620, 0.08, 0.06);
  const n = micCampioni.reduce((a, b) => a + b.length, 0), freq = micCtx.sampleRate;
  if (n < freq * 0.35) { avviso("Troppo breve: tieni il pizzico mentre parli"); return; }
  const tutto = new Float32Array(n);
  let o = 0;
  micCampioni.forEach((b) => { tutto.set(b, o); o += b.length; });
  const pcm = a16k(tutto, freq);
  fetch(JARVIS + "/jarvis/voce", { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/octet-stream" },
    body: pcm.buffer }).then((r) => { if (!r.ok) r.json().then((j) => avviso(j.errore || "Errore " + r.status)); })
    .catch((e) => avviso("Jarvis non raggiungibile: " + e.message));
}
function a16k(f32, freq) {
  const passo = freq / 16000, n = Math.floor(f32.length / passo), out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const v = f32[Math.floor(i * passo)];
    out[i] = Math.max(-32768, Math.min(32767, v * 32767));
  }
  return out;
}

// ------------------------------------------------------------------ mani: tocco, pizzico, presa a due mani
const GIUNTI = ["wrist", "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
  "index-finger-metacarpal", "index-finger-phalanx-proximal", "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
  "middle-finger-metacarpal", "middle-finger-phalanx-proximal", "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
  "ring-finger-metacarpal", "ring-finger-phalanx-proximal", "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
  "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal", "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip"];
const OSSA = [];
[[0, 1, 2, 3, 4], [0, 5, 6, 7, 8, 9], [0, 10, 11, 12, 13, 14], [0, 15, 16, 17, 18, 19], [0, 20, 21, 22, 23, 24]]
  .forEach((dito) => { for (let i = 0; i < dito.length - 1; i++) OSSA.push(dito[i], dito[i + 1]); });

const mani = [0, 1].map((i) => {
  const mano = renderer.xr.getHand(i);
  scena.add(mano);
  const punti = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 10, 8),
    new THREE.MeshBasicMaterial({ color: 0x8ff0ff, opacity: 0.9, ...ADD }), GIUNTI.length);
  punti.frustumCulled = false;
  const zero = new THREE.Matrix4().makeScale(0, 0, 0);
  for (let k = 0; k < GIUNTI.length; k++) punti.setMatrixAt(k, zero);
  const gOssa = new THREE.BufferGeometry();
  gOssa.setAttribute("position", new THREE.BufferAttribute(new Float32Array(OSSA.length * 3), 3));
  const ossa = new THREE.LineSegments(gOssa, new THREE.LineBasicMaterial({ color: 0x39d5ff, opacity: 0.55, ...ADD }));
  ossa.frustumCulled = false;
  punti.visible = ossa.visible = false;
  scena.add(punti, ossa);
  return { mano, punti, ossa, pizzica: false, presa: null, parla: false, zPrima: new Map(), ultimoTocco: 0,
    punta: new THREE.Vector3(), pollice: new THREE.Vector3(), centro: new THREE.Vector3(), offset: new THREE.Vector3(),
    sorgente: null, indice: i, puntatore: null, scia3d: [], polsi: [], velocita: new THREE.Vector3(),
    aperta: false, pugno: 0, polso: new THREE.Vector3() };
});
mani.forEach((m) => m.mano.addEventListener("connected", (e) => { m.sorgente = e.data; }));

const _m = new THREE.Matrix4(), _v = new THREE.Vector3(), _q = new THREE.Quaternion(), _s = new THREE.Vector3();
const visibili = () => pannelli.filter((p) => p.gruppo.visible && p.comparsa > 0.6 && p !== pAvviso);

function aggiornaMani() {
  const testa = posizioneTesta();
  for (const m of mani) {
    const j = m.mano.joints, tip = j && j["index-finger-tip"], th = j && j["thumb-tip"];
    const visibile = tip && th && tip.visible !== false && m.mano.visible !== false;
    m.punti.visible = m.ossa.visible = !!visibile;
    if (!visibile) { rilascia(m); continue; }
    // disegno della mano olografica
    const pos = m.ossa.geometry.attributes.position.array;
    GIUNTI.forEach((nome, k) => {
      const g = j[nome];
      if (!g || g.visible === false) { m.punti.setMatrixAt(k, _m.makeScale(0, 0, 0)); return; }
      g.getWorldPosition(_v);
      const r = nome.endsWith("tip") ? 0.0055 : 0.0042;
      m.punti.setMatrixAt(k, _m.compose(_v, _q.identity(), _s.setScalar(r)));
    });
    OSSA.forEach((k, idx) => { const g = j[GIUNTI[k]]; if (g) { g.getWorldPosition(_v); pos.set([_v.x, _v.y, _v.z], idx * 3); } });
    m.punti.instanceMatrix.needsUpdate = true;
    m.ossa.geometry.attributes.position.needsUpdate = true;

    tip.getWorldPosition(m.punta); th.getWorldPosition(m.pollice);
    m.centro.copy(m.punta).add(m.pollice).multiplyScalar(0.5);
    formaMano(m, j);
    const dist = m.punta.distanceTo(m.pollice);
    const pizzicaOra = m.pizzica ? dist < 0.03 : dist < 0.018;   // isteresi: niente sfarfallio
    if (pizzicaOra && !m.pizzica) iniziaPizzico(m);
    if (!pizzicaOra && m.pizzica) rilascia(m);
    m.pizzica = pizzicaOra;
    m.punti.material.color.set(m.parla ? 0x4dffb8 : m.presa ? 0xffb341 : 0x8ff0ff);

    // tocco con l'indice (solo se non sta pizzicando) e gesto di scorrimento
    if (!m.pizzica) { toccoIndice(m); gestoScorri(m); gestoScaccia(m); }
    if (m.presa) { trascina(m, testa); gestoAppallottola(m); }
    if (m.puntatore) trascinaConRaggio(m, testa);
  }
  // presa a due mani sullo stesso pannello (anche col puntatore da lontano): si ingrandisce
  const [a, b] = mani;
  const pa = a.presa || (a.puntatore && a.puntatore.trascina && a.puntatore.p);
  const pb = b.presa || (b.puntatore && b.puntatore.trascina && b.puntatore.p);
  if (pa && pa === pb && !pa.pallottola) {
    const p = pa, d = a.centro.distanceTo(b.centro);
    if (!p.dueMani) p.dueMani = { d0: d, s0: p.gruppo.scale.x };
    p.gruppo.scale.setScalar(THREE.MathUtils.clamp(p.dueMani.s0 * d / p.dueMani.d0, 0.45, 3.2));
  }
}

function toccoIndice(m) {
  let migliore = null;
  for (const p of visibili()) {
    const l = p.mesh.worldToLocal(m.punta.clone());
    const dentro = Math.abs(l.x) <= p.w / 2 && Math.abs(l.y) <= p.h / 2;
    if (!dentro || l.z > 0.05 || l.z < -0.03) { m.zPrima.delete(p); p.mostraCursore(null); continue; }
    if (!migliore || l.z < migliore.l.z) migliore = { p, l };
  }
  for (const p of visibili()) if (!migliore || p !== migliore.p) if (p.hoverDa === m) { p.sopra(null); p.hoverDa = null; }
  if (!migliore) return;
  const { p, l } = migliore, u = l.x / p.w + 0.5, v = l.y / p.h + 0.5;
  p.hoverDa = m;
  p.sopra(u, v);
  p.mostraCursore(l, Math.max(0, Math.min(1, l.z / 0.05)));
  const prima = m.zPrima.get(p);
  // pressione: la punta attraversa il vetro da davanti (soglia 6 mm), al massimo una ogni 350 ms
  if (prima !== undefined && prima > 0.006 && l.z <= 0.006 && performance.now() - m.ultimoTocco > 350) {
    m.ultimoTocco = performance.now();
    p.premi(u, v, m.sorgente);
  }
  m.zPrima.set(p, l.z);
}

// scorrimento come in Minority Report: la mano passa veloce davanti a un pannello (senza toccarlo), da un lato
// all'altro: foto successiva o precedente, pagina dopo nelle cartelle, rotella nelle finestre
function gestoScorri(m) {
  const ora = performance.now();
  let sotto = null, l = null;
  for (const p of visibili()) {
    if (!p.swipe) continue;
    const q = p.mesh.worldToLocal(m.punta.clone());
    if (Math.abs(q.x) <= p.w / 2 + 0.05 && Math.abs(q.y) <= p.h / 2 && q.z > 0.02 && q.z < 0.2) { sotto = p; l = q; break; }
  }
  if (!sotto) { m.scia = null; return; }
  if (!m.scia || m.scia.p !== sotto) m.scia = { p: sotto, punti: [] };
  const s = m.scia;
  s.punti.push({ t: ora, x: l.x, y: l.y });
  while (s.punti.length && ora - s.punti[0].t > 350) s.punti.shift();
  const a = s.punti[0], b = s.punti[s.punti.length - 1], dx = b.x - a.x, dy = b.y - a.y;
  if (s.bloccato) {
    // un gesto lungo vale una volta sola: si riarma quando la mano si ferma (o esce da davanti al pannello)
    const recenti = s.punti.filter((p) => ora - p.t < 160);
    if (recenti.length > 1 && Math.abs(recenti[recenti.length - 1].x - recenti[0].x) < 0.02) s.bloccato = false;
    return;
  }
  if (Math.abs(dx) > 0.16 && Math.abs(dy) < Math.abs(dx) * 0.5) {
    sotto.swipe(dx < 0 ? 1 : -1);   // mano verso sinistra = avanti, come sfogliare
    s.punti = []; s.bloccato = true;
    vibra(m.sorgente);
  }
}

function iniziaPizzico(m) {
  const sferaPos = sfera.gruppo.getWorldPosition(new THREE.Vector3());
  if (m.centro.distanceTo(sferaPos) < 0.14) { m.parla = iniziaParlato(); return; }
  let presa = null, zMin = Infinity;   // il pannello più vicino alle dita, se ce ne sono di sovrapposti
  for (const p of visibili()) {
    if (p.sempreDavanti) continue;   // conferme e avvisi non si spostano
    const l = p.mesh.worldToLocal(m.centro.clone());
    const s = p.gruppo.scale.x;
    if (Math.abs(l.x) <= p.w / 2 + 0.04 / s && Math.abs(l.y) <= p.h / 2 + 0.04 / s && Math.abs(l.z) < 0.09 / s
        && Math.abs(l.z) * s < zMin) { presa = p; zMin = Math.abs(l.z) * s; }
  }
  if (!presa) { iniziaPuntatore(m); return; }
  m.presa = presa;
  presa.lancio = null;
  m.offset.copy(presa.gruppo.getWorldPosition(new THREE.Vector3())).sub(m.centro);
  presa.dueMani = null;
  bip(420, 0.08, 0.05); vibra(m.sorgente);
}
function trascina(m, testa) {
  const p = m.presa, altra = mani.find((x) => x !== m);
  const dest = altra.presa === p ? m.centro.clone().add(altra.centro).multiplyScalar(0.5).add(m.offset)
    : m.centro.clone().add(m.offset);
  if (altra.presa === p && mani.indexOf(m) === 1) return;   // con due mani muove la prima
  p.gruppo.position.copy(ancora.worldToLocal(dest));
  p.gruppo.lookAt(testa);
}
function rilascia(m) {
  if (m.parla) { m.parla = false; fineParlato(); }
  if (m.puntatore) fineraggio(m);
  if (m.presa) {
    const p = m.presa; m.presa = null; p.dueMani = null; p.spostatoDaTe = true;
    if (p.pallottola) { lasciaPallottola(p, m.velocita); return; }
    if (m.velocita.length() > 1.1) { lancia(p, m.velocita); return; }   // lanciata più in là
    salvaDisposizione(); bip(300, 0.06, 0.04);
  }
}

// ------------------------------------------------------------------ controller: raggio, grilletto, impugnatura
const raggio = new THREE.Raycaster();
const controller = [0, 1].map((i) => {
  const c = renderer.xr.getController(i);
  const laser = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]),
    new THREE.LineBasicMaterial({ color: 0x39d5ff, opacity: 0.8, ...ADD }));
  laser.scale.z = 2;
  laser.visible = false;
  c.add(laser);
  scena.add(c);
  const st = { c, laser, sorgente: null, colpo: null, parla: false, tiene: null };
  c.addEventListener("connected", (e) => { st.sorgente = e.data; laser.visible = !e.data.hand; });
  c.addEventListener("disconnected", () => { st.sorgente = null; });
  c.addEventListener("selectstart", () => {
    if (!st.sorgente || st.sorgente.hand) return;   // le mani usano tocco e pizzico
    const h = st.colpo;
    if (!h) return;
    if (h.object.userData.sfera) { st.parla = iniziaParlato(); return; }
    if (h.object.userData.cinema && h.uv) { cinema.tocca(h.uv.x, h.uv.y); return; }
    const p = h.object.userData.pannello;
    if (p && h.uv) p.premi(h.uv.x, h.uv.y, st.sorgente);
  });
  c.addEventListener("selectend", () => { if (st.parla) { st.parla = false; fineParlato(); } });
  c.addEventListener("squeezestart", () => {
    const p = st.colpo && st.colpo.object.userData.pannello;
    if (!p || p.sempreDavanti) return;
    st.tiene = p; c.attach(p.gruppo); vibra(st.sorgente); bip(420, 0.08, 0.05);
  });
  c.addEventListener("squeezeend", () => {
    if (!st.tiene) return;
    ancora.attach(st.tiene.gruppo); st.tiene.gruppo.lookAt(posizioneTesta()); st.tiene = null; salvaDisposizione();
  });
  return st;
});
function bersagli() {
  const b = visibili().map((p) => p.mesh).concat([sfera.collisione]);
  if (cinema.attivo) b.push(cinema.schermo);
  return b;
}
function aggiornaController() {
  for (const st of controller) {
    if (!st.sorgente) continue;
    if (st.sorgente.hand) { raggioMano(st, mani[controller.indexOf(st)]); continue; }
    _m.identity().extractRotation(st.c.matrixWorld);
    raggio.ray.origin.setFromMatrixPosition(st.c.matrixWorld);
    raggio.ray.direction.set(0, 0, -1).applyMatrix4(_m);
    const h = raggio.intersectObjects(bersagli(), false)[0] || null;
    if (st.colpo && st.colpo.object.userData.pannello && (!h || h.object !== st.colpo.object)) st.colpo.object.userData.pannello.sopra(null);
    st.colpo = h;
    st.laser.scale.z = h ? h.distance : 2;
    if (h && h.object.userData.pannello && h.uv) h.object.userData.pannello.sopra(h.uv.x, h.uv.y);
  }
}

// ------------------------------------------------------------------ puntatore delle mani (raggio come nel Quest)
// Da lontano la mano ha il suo raggio (quello del sistema, dal palmo). Pizzico breve = tocco dove punta; pizzico
// tenuto = trascini il pannello lungo il raggio; due mani sullo stesso pannello = zoom. Da vicino (a 25 cm da un
// pannello) il raggio sparisce e restano tocco con l'indice e presa diretta.
function vicinoAiPannelli(m) {
  for (const p of visibili()) {
    const l = p.mesh.worldToLocal(m.punta.clone()), s = p.gruppo.scale.x;
    if (Math.abs(l.x) <= p.w / 2 + 0.12 / s && Math.abs(l.y) <= p.h / 2 + 0.12 / s && l.z > -0.06 / s && l.z < 0.25 / s) return true;
  }
  return false;
}
function raggioMano(st, m) {
  const libera = m && m.punti.visible && !m.presa && !m.parla && (m.puntatore || !vicinoAiPannelli(m));
  st.laser.visible = !!libera;
  if (!libera) {
    if (st.colpo && st.colpo.object.userData.pannello) st.colpo.object.userData.pannello.sopra(null);
    st.colpo = null;
    return;
  }
  _m.identity().extractRotation(st.c.matrixWorld);
  raggio.ray.origin.setFromMatrixPosition(st.c.matrixWorld);
  raggio.ray.direction.set(0, 0, -1).applyMatrix4(_m);
  m.raggioO = raggio.ray.origin.clone(); m.raggioD = raggio.ray.direction.clone();
  if (m.puntatore && m.puntatore.trascina) { st.laser.scale.z = m.puntatore.dist; return; }
  const h = raggio.intersectObjects(bersagli(), false)[0] || null;
  if (st.colpo && st.colpo.object.userData.pannello && (!h || h.object !== st.colpo.object)) st.colpo.object.userData.pannello.sopra(null);
  st.colpo = h;
  st.laser.scale.z = h ? h.distance : 1.2;
  st.laser.material.opacity = m.pizzica ? 1 : 0.55;
  if (h && h.object.userData.pannello && h.uv) h.object.userData.pannello.sopra(h.uv.x, h.uv.y);
}
function iniziaPuntatore(m) {
  const st = controller[m.indice], h = st && st.colpo;
  if (!h) return;
  if (h.object.userData.sfera) { m.parla = iniziaParlato(); return; }
  const p = h.object.userData.pannello;
  m.puntatore = { p, cinema: !!h.object.userData.cinema, uv: h.uv ? h.uv.clone() : null, t0: performance.now(),
    dist: h.distance, trascina: false, inizio: m.centro.clone(),
    offset: p ? p.gruppo.getWorldPosition(new THREE.Vector3()).sub(h.point) : null };
  vibra(m.sorgente);
}
function trascinaConRaggio(m, testa) {
  const q = m.puntatore;
  if (!q.p || q.p.sempreDavanti || q.cinema || !m.raggioO) return;
  if (!q.trascina && (performance.now() - q.t0 > 450 || m.centro.distanceTo(q.inizio) > 0.06)) {
    q.trascina = true; q.p.lancio = null; bip(420, 0.08, 0.05);
  }
  if (!q.trascina) return;
  const altra = mani[1 - m.indice];
  if (altra.puntatore && altra.puntatore.p === q.p && m.indice === 1) return;   // con due mani muove la prima
  const dest = m.raggioO.clone().addScaledVector(m.raggioD, q.dist).add(q.offset);
  q.p.gruppo.position.copy(ancora.worldToLocal(dest));
  q.p.gruppo.lookAt(testa);
}
function fineraggio(m) {
  const q = m.puntatore;
  m.puntatore = null;
  if (q.trascina) { q.p.dueMani = null; q.p.spostatoDaTe = true; salvaDisposizione(); bip(300, 0.06, 0.04); return; }
  if (q.cinema && q.uv) { cinema.tocca(q.uv.x, q.uv.y); return; }
  if (q.p && q.uv) q.p.premi(q.uv.x, q.uv.y, m.sorgente);   // pizzico breve = tocco da lontano
}

// ------------------------------------------------------------------ forma e velocità della mano
const _w = new THREE.Vector3(), _d = new THREE.Vector3();
function formaMano(m, j) {
  const ora = performance.now();
  // velocità del pizzico (per i lanci): media sugli ultimi 100 ms
  m.scia3d.push({ t: ora, p: m.centro.clone() });
  while (m.scia3d.length > 2 && ora - m.scia3d[0].t > 100) m.scia3d.shift();
  const a = m.scia3d[0], b = m.scia3d[m.scia3d.length - 1];
  if (b.t > a.t) m.velocita.copy(b.p).sub(a.p).multiplyScalar(1000 / (b.t - a.t));
  // mano aperta (dita distese) o pugno (medio, anulare e mignolo chiusi verso il palmo)
  const polso = j.wrist;
  if (!polso) return;
  polso.getWorldPosition(m.polso);
  let somma = 0, chiuse = 0;
  for (const nome of ["index-finger-tip", "middle-finger-tip", "ring-finger-tip", "pinky-finger-tip"]) {
    const g = j[nome];
    if (!g) return;
    g.getWorldPosition(_w);
    const d = _w.distanceTo(m.polso);
    somma += d;
    if (nome !== "index-finger-tip" && d < 0.095) chiuse++;
  }
  m.aperta = somma / 4 > 0.14;
  m.pugno = chiuse === 3 ? m.pugno + 1 : 0;
  m.polsi.push({ t: ora, p: m.polso.clone() });
  while (m.polsi.length > 2 && ora - m.polsi[0].t > 300) m.polsi.shift();
}

// scacciare via con la mano aperta (veloce, di lato, lontano dai pannelli): pannelli rimessi come all'inizio;
// nel cinema: si esce dal cinema
let ultimoScaccia = 0;
function gestoScaccia(m) {
  const ora = performance.now();
  if (!m.aperta || m.puntatore || ora - ultimoScaccia < 1500 || m.polsi.length < 4) return;
  if (!cinema.attivo && vicinoAiPannelli(m)) return;
  const a = m.polsi[0], b = m.polsi[m.polsi.length - 1], dt = (b.t - a.t) / 1000;
  if (dt < 0.12) return;
  _d.copy(b.p).sub(a.p);
  const orizz = Math.hypot(_d.x, _d.z);
  if (orizz < 0.32 || orizz / dt < 1.3 || Math.abs(_d.y) > orizz * 0.6) return;
  ultimoScaccia = ora; m.polsi = [];
  vibra(m.sorgente);
  if (cinema.attivo) { cinema.esci(); return; }
  disposizioneIniziale(true);
  dinamici.forEach((p) => { p.spostatoDaTe = false; });
  aVentaglio();
}

// ------------------------------------------------------------------ lanci, palle di carta e cestino
// Pannello lasciato con la mano in corsa: vola più in là e rallenta. Pannello stretto nel pugno: diventa una palla
// di carta; lasciata nel cestino (o lanciata dentro) il pannello si chiude, altrove torna com'era.
const lanciati = new Set();
function lancia(p, v) {
  p.lancio = { v: v.clone().multiplyScalar(1.6) };
  lanciati.add(p);
  bip(500, 0.06, 0.05);
}
function aggiornaLanci(dt) {
  const testa = posizioneTesta();
  for (const p of lanciati) {
    const l = p.lancio;
    if (!l || !p.gruppo.parent) { lanciati.delete(p); continue; }
    const pos = p.gruppo.getWorldPosition(new THREE.Vector3());
    if (p.pallottola) {
      l.v.y -= 9.8 * dt;                       // la palla cade
      pos.addScaledVector(l.v, dt);
      if (cestino.dentro(pos)) { lanciati.delete(p); butta(p); continue; }
      if (pos.y < 0.05) { lanciati.delete(p); srotola(p, "Mancato! Riprova"); continue; }
    } else {
      pos.addScaledVector(l.v, dt);
      l.v.multiplyScalar(Math.exp(-2.6 * dt));
      const lontano = Math.hypot(pos.x - testa.x, pos.z - testa.z);
      if (l.v.length() < 0.12 || lontano > 4) { p.lancio = null; lanciati.delete(p); salvaDisposizione(); }
    }
    p.gruppo.position.copy(ancora.worldToLocal(pos));
    if (!p.pallottola) p.gruppo.lookAt(testa);
    else p.gruppo.rotateX(dt * 9).rotateZ(dt * 5);
  }
}
const cestino = (() => {
  const g = new THREE.Group();
  const corpo = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.13, 0.32, 28, 1, true),
    new THREE.MeshBasicMaterial({ color: 0xff5468, opacity: 0.35, wireframe: true, ...ADD }));
  const bordo = new THREE.Mesh(new THREE.TorusGeometry(0.17, 0.006, 8, 40),
    new THREE.MeshBasicMaterial({ color: 0xff5468, opacity: 0.9, ...ADD }));
  bordo.rotation.x = Math.PI / 2; bordo.position.y = 0.16;
  g.add(corpo, bordo);
  g.visible = false;
  scena.add(g);
  return {
    g,
    mostra() {
      if (g.visible) return;
      const testa = posizioneTesta(), avanti = new THREE.Vector3(0, 0, -1).applyQuaternion(orientamentoTesta());
      avanti.y = 0; avanti.normalize();
      const destra = new THREE.Vector3(-avanti.z, 0, avanti.x);
      g.position.copy(testa).addScaledVector(avanti, 0.55).addScaledVector(destra, 0.35);
      g.position.y = Math.max(0.2, testa.y - 0.85);
      g.visible = true;
    },
    nascondi() { g.visible = false; },
    dentro(pos) {
      return g.visible && Math.hypot(pos.x - g.position.x, pos.z - g.position.z) < 0.22
        && pos.y < g.position.y + 0.24 && pos.y > g.position.y - 0.25;
    },
  };
})();
function gestoAppallottola(m) {
  const p = m.presa;
  if (p.pallottola || m.pugno < 8) return;   // pugno chiuso per ~8 fotogrammi
  p.pallottola = { s0: p.gruppo.scale.x, posizione: p.gruppo.position.clone(), q: p.gruppo.quaternion.clone() };
  p.gruppo.scale.setScalar(0.16 * p.pallottola.s0);
  m.offset.set(0, 0, 0);
  cestino.mostra();
  bip(180, 0.12, 0.06); vibra(m.sorgente);
  avviso("Palla di carta: buttala nel cestino");
}
function lasciaPallottola(p, v) {
  if (cestino.dentro(p.gruppo.getWorldPosition(new THREE.Vector3()))) { butta(p); return; }
  if (v.length() > 0.8) { lancia(p, v); p.lancio.v.multiplyScalar(0.8); return; }
  srotola(p);
}
function srotola(p, testo) {
  const b = p.pallottola;
  p.pallottola = null; p.lancio = null;
  p.gruppo.scale.setScalar(b.s0);
  p.gruppo.position.copy(b.posizione); p.gruppo.quaternion.copy(b.q);
  cestino.nascondi();
  if (testo) avviso(testo);
}
function butta(p) {
  cestino.nascondi();
  bip(240, 0.1, 0.07); setTimeout(() => bip(160, 0.14, 0.06), 90);
  if (FISSI.includes(p)) {   // i pannelli fissi tornano con "Riordina" o scacciando via
    const b = p.pallottola;
    p.pallottola = null; p.gruppo.scale.setScalar(b.s0); p.gruppo.visible = false; p.nelCestino = true;
    avviso("Nel cestino: torna con Riordina");
  } else {
    p.pallottola = null;
    p.chiudi();
  }
}

// ------------------------------------------------------------------ cinema: video e giochi a schermo grande
// La realtà mista si oscura (cupola scura attorno), gli altri pannelli spariscono, la finestra del PC (in diretta
// fluida) o il video vanno su uno schermo grande davanti a te. Esci con la barra sotto lo schermo o scacciando via.
class BarraCinema extends Pannello {
  constructor() { super("cinema", "CINEMA", 0.7, 0.13, { visibile: false, denso: true }); this.luce = 0; }
  contenuto(c, W, H) {
    const s = cinema.sorgente;
    this.titolo = "CINEMA" + (s && s.nome ? " · " + s.nome.toUpperCase().slice(0, 22) : "");
    const y = H - 92;
    this.bottone(c, 34, y, 220, 70, "✕ ESCI", () => cinema.esci(), { dim: 24, colore: "255,84,104" });
    this.bottone(c, 270, y, 220, 70, ["☾ BUIO", "◐ PENOMBRA", "☀ STANZA"][this.luce], () => {
      this.luce = (this.luce + 1) % 3; this.sporco = true;
    }, { dim: 22 });
    if (s instanceof FinestraPc) {
      this.bottone(c, 506, y, 220, 70, "⟳ DAVANTI", () => s.manda({ tipo: "davanti" }), { dim: 22 });
      c.font = "400 22px " + FONT; c.fillStyle = "rgba(223,246,255,0.6)";
      c.fillText(cinema.fps ? cinema.fps + " fps" : "collego…", 750, y + 42);
    } else if (s && s.video) {
      const el = s.video;
      this.bottone(c, 506, y, 150, 70, el.paused ? "▶" : "❚❚", () => { el.paused ? el.play() : el.pause(); this.sporco = true; }, { dim: 28 });
      this.bottone(c, 670, y, 120, 70, "−10", () => { el.currentTime = Math.max(0, el.currentTime - 10); }, { dim: 22 });
      this.bottone(c, 800, y, 120, 70, "+10", () => { el.currentTime += 10; }, { dim: 22 });
    }
  }
}
const cinema = (() => {
  const cupola = new THREE.Mesh(new THREE.SphereGeometry(24, 32, 16),
    new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.BackSide, transparent: true, opacity: 0, depthWrite: false }));
  cupola.renderOrder = -10; cupola.visible = false; scena.add(cupola);
  const tex = new THREE.Texture();
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.flipY = false;   // le ImageBitmap arrivano già girate (imageOrientation: "flipY")
  const schermo = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  schermo.userData.cinema = true; schermo.visible = false; scena.add(schermo);
  const cornice = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(1, 1)),
    new THREE.LineBasicMaterial({ color: 0x39d5ff, opacity: 0.5, ...ADD }));
  schermo.add(cornice);
  const barra = new BarraCinema();
  scena.add(barra.gruppo);
  const C = { attivo: false, sorgente: null, schermo, barra, fps: 0, nascosti: [], ctrl: null, bitmap: null,
    proporzione: 16 / 9, fotogrammi: 0, tFps: performance.now(), sferaPrima: null };
  const LUCI = [0.94, 0.7, 0];
  function misura(prop) {
    C.proporzione = prop;
    const w = 2.4;
    schermo.scale.set(w, w / prop, 1);
    barra.gruppo.position.copy(schermo.position).add(new THREE.Vector3(0, -(w / prop) / 2 - 0.16, 0.25).applyQuaternion(schermo.quaternion));
    barra.gruppo.quaternion.copy(schermo.quaternion);
  }
  C.entra = function (sorgente) {
    if (C.attivo) C.esci(true);
    C.attivo = true; C.sorgente = sorgente; C.fps = 0; C.fotogrammi = 0; C.tFps = performance.now();
    const testa = posizioneTesta(), avanti = new THREE.Vector3(0, 0, -1).applyQuaternion(orientamentoTesta());
    avanti.y = 0; avanti.normalize();
    schermo.position.copy(testa).addScaledVector(avanti, 2.1);
    schermo.position.y = testa.y + 0.05;
    schermo.lookAt(testa.x, schermo.position.y, testa.z);
    // conferme e avvisi restano: una richiesta di permesso deve vedersi anche nel cinema
    C.nascosti = pannelli.filter((p) => p !== barra && !p.sempreDavanti && p.gruppo.visible);
    C.nascosti.forEach((p) => { p.gruppo.visible = false; });
    barra.gruppo.visible = true; barra.comparsa = 0; barra.sporco = true;
    cupola.visible = true;
    C.sferaPrima = sfera.gruppo.position.clone();
    if (sorgente.video) {
      const vt = new THREE.VideoTexture(sorgente.video);
      vt.colorSpace = THREE.SRGBColorSpace;
      schermo.material.map = vt; schermo.material.needsUpdate = true;
      misura((sorgente.video.videoWidth || 16) / (sorgente.video.videoHeight || 9));
      sorgente.video.play().catch(() => {});
    } else {
      schermo.material.map = tex; schermo.material.needsUpdate = true;
      misura(16 / 9);
      avviaDiretta(sorgente);
    }
    schermo.visible = true;
    // la sfera (pizzica e tieni per parlare) non copre lo schermo: va accanto alla barra, a portata di mano
    const accanto = barra.gruppo.position.clone().add(new THREE.Vector3(-0.5, 0, 0).applyQuaternion(barra.gruppo.quaternion));
    sfera.gruppo.position.copy(ancora.worldToLocal(accanto));
    bip(440, 0.12, 0.05); setTimeout(() => bip(330, 0.2, 0.05), 120);
    console.info("[xr] cinema:", sorgente.nome || sorgente.id);
  };
  C.esci = function (subito) {
    if (!C.attivo) return;
    C.attivo = false;
    if (C.ctrl) { C.ctrl.abort(); C.ctrl = null; }
    if (schermo.material.map !== tex) { schermo.material.map.dispose(); schermo.material.map = tex; }
    schermo.visible = false; barra.gruppo.visible = false;
    C.nascosti.forEach((p) => { if (pannelli.includes(p)) p.gruppo.visible = true; });
    C.nascosti = []; C.sorgente = null;
    if (C.sferaPrima) { sfera.gruppo.position.copy(C.sferaPrima); C.sferaPrima = null; }
    if (subito) cupola.material.opacity = 0;
    bip(330, 0.1, 0.05);
  };
  C.tocca = function (u, v) {   // un tocco sullo schermo grande = clic nello stesso punto della finestra del PC
    const s = C.sorgente;
    if (s instanceof FinestraPc) { bip(1300, 0.04, 0.05); s.manda({ tipo: "clic", x: u, y: 1 - v }); }
    else if (s && s.video) { s.video.paused ? s.video.play() : s.video.pause(); barra.sporco = true; }
  };
  C.aggiorna = function (dt) {
    const voluta = C.attivo ? LUCI[barra.luce] : 0;
    cupola.material.opacity += (voluta - cupola.material.opacity) * Math.min(1, dt * 3);
    cupola.visible = cupola.material.opacity > 0.01;
    cupola.position.copy(posizioneTesta());
    if (C.attivo && C.sorgente && !pannelli.includes(C.sorgente)) C.esci();   // chiusa la sorgente
    if (C.attivo && C.sorgente && C.sorgente.video && !C.sorgente.video.paused) barra.sporco = barra.sporco || Math.random() < 0.03;
  };
  C.fotogramma = function (bmp) {
    if (!C.attivo) { bmp.close(); return; }
    const vecchio = C.bitmap;
    C.bitmap = bmp;
    tex.image = bmp; tex.needsUpdate = true;
    if (vecchio) setTimeout(() => vecchio.close(), 100);   // già caricato nella scheda video
    const p = bmp.width / bmp.height;
    if (Math.abs(p - C.proporzione) > 0.02) misura(p);
    C.fotogrammi++;
    const ora = performance.now();
    if (ora - C.tFps > 1000) { C.fps = Math.round(C.fotogrammi * 1000 / (ora - C.tFps || 1)); C.fotogrammi = 0; C.tFps = ora; barra.sporco = true; }
  };
  return C;
})();
// la diretta fluida della finestra o dello schermo: [4 byte di lunghezza][JPEG]... (vedi diretta() in visore.py)
async function avviaDiretta(f) {
  const ctrl = new AbortController();
  cinema.ctrl = ctrl;
  const parametri = f.schermo ? { numero: f.schermo } : { hwnd: f.hwnd };
  Object.assign(parametri, { lato: 1600, fps: 30 });
  try {
    const r = await fetch(urlJ("/jarvis/schermo/diretta", parametri), { headers: { "X-Token": TOKEN }, signal: ctrl.signal });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).errore || "errore " + r.status);
    const lettore = r.body.getReader();
    let buf = new Uint8Array(0), occupato = false;
    for (;;) {
      const { value, done } = await lettore.read();
      if (done) break;
      const unito = new Uint8Array(buf.length + value.length);
      unito.set(buf); unito.set(value, buf.length); buf = unito;
      let ultimo = null;
      while (buf.length >= 4) {
        const n = new DataView(buf.buffer, buf.byteOffset, 4).getUint32(0);
        if (buf.length < 4 + n) break;
        ultimo = buf.slice(4, 4 + n); buf = buf.slice(4 + n);
      }
      // si decodifica solo l'ultimo arrivato: se il visore resta indietro salta i fotogrammi, non accumula ritardo
      if (ultimo && !occupato) {
        occupato = true;
        // le ImageBitmap non si capovolgono da sole in WebGL: si chiede già girata
        createImageBitmap(new Blob([ultimo], { type: "image/jpeg" }), { imageOrientation: "flipY" })
          .then((b) => (cinema.ctrl === ctrl ? cinema.fotogramma(b) : b.close()))
          .catch(() => {}).finally(() => { occupato = false; });
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") { avviso("Diretta interrotta: " + e.message); console.warn("[xr] diretta:", e); }
  }
}

// ------------------------------------------------------------------ telecamera del visore (per Jarvis, col permesso)
// Il permesso del browser si chiede prima di entrare (dentro la sessione il Quest non mostra la richiesta); Jarvis
// chiede comunque il tuo sì ogni volta prima di scattare.
async function preparaTelecamera() {
  try {
    const st = await navigator.permissions.query({ name: "camera" }).catch(() => null);
    if (st && st.state !== "prompt") return;
    const f = await navigator.mediaDevices.getUserMedia({ video: true });
    f.getTracks().forEach((t) => t.stop());
  } catch (e) { console.warn("[xr] telecamera:", e.message); }
}
async function scattaFoto(id) {
  const manda = (corpo, errore) => fetch(urlJ("/jarvis/telecamera", errore ? { id, errore } : { id }),
    { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "image/jpeg" }, body: corpo || new Blob([]) });
  let flusso = null;
  try {
    avviso("📷 Jarvis guarda con la telecamera del visore");
    flusso = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 960 } } });
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.srcObject = flusso;
    await v.play();
    await new Promise((ok) => setTimeout(ok, 700));   // esposizione e messa a fuoco
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.9));
    bip(1600, 0.04, 0.07); setTimeout(() => bip(1200, 0.05, 0.06), 60);   // lo scatto
    await manda(blob);
    console.info("[xr] foto per Jarvis:", c.width + "x" + c.height, Math.round(blob.size / 1024) + " KB");
  } catch (e) {
    await manda(null, e.message || String(e)).catch(() => {});
    avviso("Telecamera non disponibile: " + (e.message || e));
  } finally {
    if (flusso) flusso.getTracks().forEach((t) => t.stop());
  }
}

// ------------------------------------------------------------------ anteprima sul PC (mouse)
const mouse = new THREE.Vector2();
let trascinando = null, yaw = 0, pitch = -0.08, colpoMouse = null, parlaMouse = false;
renderer.domElement.addEventListener("pointerdown", (e) => {
  if (renderer.xr.isPresenting) return;
  colpoMouse = colpisciMouse(e);
  if (colpoMouse && colpoMouse.object.userData.sfera) { parlaMouse = iniziaParlato(); return; }
  if (colpoMouse && colpoMouse.object.userData.cinema) { cinema.tocca(colpoMouse.uv.x, colpoMouse.uv.y); return; }
  if (colpoMouse && colpoMouse.object.userData.pannello) {
    colpoMouse.object.userData.pannello.premi(colpoMouse.uv.x, colpoMouse.uv.y);
    return;
  }
  trascinando = { x: e.clientX, y: e.clientY, yaw, pitch };
});
addEventListener("pointerup", () => { trascinando = null; if (parlaMouse) { parlaMouse = false; fineParlato(); } });
renderer.domElement.addEventListener("pointermove", (e) => {
  if (renderer.xr.isPresenting) return;
  if (trascinando) {
    yaw = trascinando.yaw - (e.clientX - trascinando.x) * 0.004;
    pitch = THREE.MathUtils.clamp(trascinando.pitch - (e.clientY - trascinando.y) * 0.004, -1.2, 1.2);
    return;
  }
  const h = colpisciMouse(e);
  pannelli.forEach((p) => { if (!h || h.object.userData.pannello !== p) p.sopra(null); });
  if (h && h.object.userData.pannello) h.object.userData.pannello.sopra(h.uv.x, h.uv.y);
  renderer.domElement.style.cursor = h ? "pointer" : "grab";
});
function colpisciMouse(e) {
  mouse.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  raggio.setFromCamera(mouse, camera);
  return raggio.intersectObjects(bersagli(), false)[0] || null;
}

// ------------------------------------------------------------------ dati da Jarvis e dal pannello
function eventoJarvis(d) {
  if (d.tutto) {
    const s = d.tutto;
    S.imp = s.impostazioni || {}; S.stato = s.stato || "riposo"; S.promemoria = s.promemoria || [];
    S.emergenza = !!(s.emergenza && s.emergenza.attiva); S.azione = s.azione || "";
    mostraConferma(s.conferma || null);
    sporca(); return;
  }
  if (d.stato) S.stato = d.stato;
  if (d.livello !== undefined) S.livello = d.livello;
  if (d.utente) { S.corrente = null; aggiungi("utente", d.utente); }
  if (d.jarvis_pezzo) {
    if (!S.corrente) { S.corrente = { tipo: "jarvis", testo: "" }; S.messaggi.push(S.corrente); }
    S.corrente.testo += d.jarvis_pezzo; sporca("conversazione");
  }
  if (d.jarvis) {
    if (S.corrente) S.corrente.testo = d.jarvis; else aggiungi("jarvis", d.jarvis);
    S.corrente = null; sporca("conversazione");
  }
  if (d.fine) S.corrente = null;
  if (d.azione) { S.corrente = null; aggiungi("azione", d.azione); }
  if (d.errore) aggiungi("errore", d.errore);
  if (d.conferma !== undefined) mostraConferma(d.conferma);
  if (d.impostazioni) { S.imp = d.impostazioni; sporca(); }
  if (d.emergenza) { S.emergenza = !!d.emergenza.attiva; sporca("conversazione"); }
  if (d.promemoria) { S.promemoria = d.promemoria; sporca("promemoria"); }
  if (d.avviso) avviso(d.avviso);
  if (d.dettatura !== undefined) { S.dettatura = !!d.dettatura; sporca("promemoria"); }
  if (d.nuova) { S.messaggi = []; sporca("conversazione"); }
  if (d.visore) eventoVisore(d.visore);
  if (d.telecamera && d.telecamera.id) scattaFoto(d.telecamera.id);
}
function aggiungi(tipo, testo) {
  S.messaggi.push({ tipo, testo });
  if (S.messaggi.length > 60) S.messaggi.splice(0, S.messaggi.length - 60);
  sporca("conversazione");
}
function eventoPannello(d) {
  S.pc = d;
  const metti = (k, v) => { S.storia[k].push(v == null ? null : v); if (S.storia[k].length > 90) S.storia[k].shift(); };
  metti("fps", d.fps && d.fps.ora); metti("gpu", d.gpu && d.gpu.carico); metti("cpu", d.cpu && d.cpu.carico);
  sporca("telemetria");
}
function caricaConfig() { apiP("/api/config").then((c) => { S.config = c; sporca("app"); }).catch(() => {}); }

// ------------------------------------------------------------------ avvio e ciclo
const orologio = new THREE.Clock();
let ultimoSecondo = 0, primoFotogramma = true;
// Dove metterti attorno sfera e pannelli. Al primo fotogramma il Quest può non tracciare ancora la testa (posa
// "emulata" o ferma nell'origine della stanza): la disposizione aspetta POSA_BUONA fotogrammi tracciati di fila e
// intanto la scena resta nascosta. Il "ricentra" del Quest (tasto Meta tenuto) e una scena rimasta lontana (oltre
// LONTANO metri per 2 s) la riportano attorno a te, tenendo la disposizione.
const POSA_BUONA = 12, LONTANO = 2.5;
let posaBuona = 0, lontanoDa = 0, daRicentrare = false;
// stato leggibile dagli strumenti di sviluppo del Quest (adb): dov'è la testa, dov'è la scena, se il visore traccia
window.__xrStato = {};
function posaTracciata() {
  const frame = renderer.xr.getFrame(), spazio = renderer.xr.getReferenceSpace();
  const posa = frame && spazio && frame.getViewerPose(spazio);
  const p = posa && posa.transform.position;
  Object.assign(window.__xrStato, { posa: p ? [p.x, p.y, p.z].map((v) => +v.toFixed(2)) : null,
    emulata: posa ? posa.emulatedPosition : null });
  return !!posa && !posa.emulatedPosition;
}
function ricentra(motivo) {
  centraAncora();
  if (!cinema.attivo) sfera.gruppo.position.set(0, altezzaOcchi + 0.02, -0.74);
  const t = posizioneTesta();
  console.info("[xr] ricentrato:", motivo, "testa", t.toArray().map((v) => v.toFixed(2)).join(" "));
}
function seguiTesta(dt) {
  if (primoFotogramma) {
    if (!posaTracciata()) { posaBuona = 0; ancora.visible = false; return; }
    if (++posaBuona < POSA_BUONA) { ancora.visible = false; return; }
    primoFotogramma = false;
    disposizioneIniziale(false);
    ancora.visible = true;
    const t = posizioneTesta();
    console.info("[xr] disposti attorno alla testa", t.toArray().map((v) => v.toFixed(2)).join(" "),
      "spazio", renderer.xr.getReferenceSpace() && "local-floor");
    return;
  }
  posaTracciata();
  Object.assign(window.__xrStato, { testa: posizioneTesta().toArray().map((v) => +v.toFixed(2)),
    ancora: ancora.position.toArray().map((v) => +v.toFixed(2)), disposti: !primoFotogramma });
  if (daRicentrare) { daRicentrare = false; ricentra("ricentra del visore"); avviso("Pannelli riportati attorno a te"); return; }
  const t = posizioneTesta();
  const lontananza = Math.hypot(t.x - ancora.position.x, t.z - ancora.position.z);
  lontanoDa = lontananza > LONTANO ? lontanoDa + dt : 0;
  if (lontanoDa > 2) {
    lontanoDa = 0;
    ricentra(`lontani ${lontananza.toFixed(1)} m`);
    avviso("Pannelli riportati da te");
  }
}
renderer.setAnimationLoop(() => {
  const dt = Math.min(0.05, orologio.getDelta()), t = orologio.elapsedTime;
  if (!renderer.xr.isPresenting) {
    camera.rotation.set(pitch, yaw, 0, "YXZ");
  } else {
    seguiTesta(dt);
  }
  const testa = posizioneTesta();
  aggiornaMani();
  aggiornaController();
  aggiornaLanci(dt);
  cinema.aggiorna(dt);
  sfera.aggiorna(dt, t, testa);
  pannelli.forEach((p) => p.aggiorna(dt, t));
  if (pAvviso.gruppo.visible && performance.now() > avvisoFino) pAvviso.gruppo.visible = false;
  anelloPedana.material.opacity = 0.3 + 0.15 * Math.sin(t * 1.5);
  tacche.rotation.z += dt * 0.05;
  pulviscolo.rotation.y += dt * 0.01;
  if (t - ultimoSecondo > 20) { ultimoSecondo = t; sporca("promemoria"); }   // l'orologio
  renderer.render(scena, camera);
});

const bottone = document.getElementById("entra"), nota = document.getElementById("nota");
// collegamento senza chiave: il visore mostra un codice, Jarvis lo chiede sul tablet (o lo consenti dal PC)
async function abbina() {
  bottone.disabled = true;
  bottone.textContent = "COLLEGO IL VISORE…";
  let r;
  try {
    r = await fetch(PANNELLO + "/api/xr/abbina", { method: "POST" }).then((x) => x.json().then((j) => {
      if (!x.ok) throw new Error(j.errore || "errore " + x.status); return j; }));
  } catch (e) {
    nota.innerHTML = "Non riesco a chiedere il collegamento: " + e.message;
    bottone.textContent = "RIPROVA"; bottone.disabled = false; bottone.onclick = () => location.reload();
    return false;
  }
  document.getElementById("codice").textContent = r.codice.split("").join(" ");
  document.getElementById("abbina").classList.add("visto");
  nota.innerHTML = "Jarvis ti chiede sul tablet se consentire il visore: rispondi <b>Consenti</b> (a voce o toccando).<br>" +
    "Oppure dalle impostazioni del PC, sezione Jarvis XR.";
  for (;;) {
    await new Promise((ok) => setTimeout(ok, 1500));
    const e = await fetch(PANNELLO + "/api/xr/abbina?id=" + encodeURIComponent(r.id)).then((x) => x.json()).catch(() => ({}));
    if (e.token) {
      scrivi("token", e.token);
      location.replace(location.pathname);   // ricarica, con la chiave salvata nel visore
      return false;
    }
    if (e.stato === "no" || e.stato === "scaduta" || e.stato === "sconosciuta") {
      document.getElementById("abbina").classList.remove("visto");
      nota.innerHTML = e.stato === "no" ? "Collegamento rifiutato." : "Tempo scaduto: nessuno ha confermato.";
      bottone.textContent = "RIPROVA"; bottone.disabled = false; bottone.onclick = () => location.reload();
      return false;
    }
  }
}

async function avvia() {
  // senza chiave, o con una chiave non più valida: si collega il visore col codice
  if (TOKEN) {
    const prova = await fetch(PANNELLO + "/api/config", { headers: { "X-Token": TOKEN } }).catch(() => null);
    if (prova && prova.status === 401) { scrivi("token", ""); TOKEN = ""; }
  }
  if (!TOKEN && !(await abbina())) return;
  flusso(JARVIS + "/jarvis/flusso?xr=1", eventoJarvis, (ok) => { S.jarvisOk = ok; sporca("conversazione"); });
  flusso(PANNELLO + "/api/flusso", eventoPannello, (ok) => { S.pannelloOk = ok; sporca("telemetria"); });
  caricaConfig();
  setInterval(caricaConfig, 60000);
  disposizioneIniziale(false);

  const ar = navigator.xr && await navigator.xr.isSessionSupported("immersive-ar").catch(() => false);
  const vr = !ar && navigator.xr && await navigator.xr.isSessionSupported("immersive-vr").catch(() => false);
  if (!ar && !vr) {
    bottone.textContent = "ANTEPRIMA SUL PC";
    nota.innerHTML = (window.isSecureContext ? "Questo browser non ha WebXR: apri la pagina nel browser del Quest 3."
      : "Per la realtà mista apri l'indirizzo <b>https://…:47443/xr</b> (QR nelle impostazioni del PC).") +
      "<br>Qui: clic per premere, trascina per guardarti intorno, tieni premuta la sfera per parlare.";
    bottone.onclick = () => preparaMicrofono();
    bottone.disabled = false;
    return;
  }
  bottone.textContent = ar ? "ENTRA IN REALTÀ MISTA" : "ENTRA IN VR";
  nota.innerHTML = "Tocca con l'indice · pizzica per spostare · due mani per ingrandire · pizzica e tieni la sfera per parlare";
  bottone.disabled = false;
  bottone.onclick = async () => {
    await preparaMicrofono();   // il permesso si chiede prima: dentro la sessione il visore non mostra la richiesta
    await preparaTelecamera();
    const sessione = await navigator.xr.requestSession(ar ? "immersive-ar" : "immersive-vr", {
      requiredFeatures: ["local-floor"], optionalFeatures: ["hand-tracking", "bounded-floor"] });
    scena.background = null; griglia.visible = false;
    primoFotogramma = true; posaBuona = 0; lontanoDa = 0; ancora.visible = false;
    await renderer.xr.setSession(sessione);
    // "ricentra" del Quest: l'origine dello spazio si sposta su di te, la scena va rimessa attorno a te
    const spazio = renderer.xr.getReferenceSpace();
    if (spazio && spazio.addEventListener) spazio.addEventListener("reset", () => { daRicentrare = true; });
    document.getElementById("avvio").classList.add("nascosto");
    // con il visore addosso si parla dal Quest: Jarvis spegne il microfono del tablet (si sovrapporrebbero)
    apiJ("/jarvis/visore/sessione", { immersivo: true }).catch(() => {});
    sessione.addEventListener("end", () => {
      apiJ("/jarvis/visore/sessione", { immersivo: false }).catch(() => {});
      cinema.esci(true);
      scena.background = sfondoAnteprima; griglia.visible = true;
      document.getElementById("avvio").classList.remove("nascosto");
      ancora.position.set(0, 0, 0); ancora.rotation.set(0, 0, 0); altezzaOcchi = 1.6; disposizioneIniziale(false);
      ancora.visible = true;
    });
  };
}
avvia();
// installabile come app dove il browser lo permette (Chrome sul PC; il Browser del Quest no: lì c'è l'APK di
// android/quest/): il pulsante compare solo se il browser lo offre e apre la sua finestra d'installazione
let richiestaInstalla = null;
const bottoneInstalla = document.getElementById("installa");
addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  richiestaInstalla = e;
  console.info("[xr] installabile come app");
  if (!matchMedia("(display-mode: standalone)").matches) bottoneInstalla.hidden = false;
});
bottoneInstalla.onclick = async () => {
  if (!richiestaInstalla) return;
  richiestaInstalla.prompt();
  const { outcome } = await richiestaInstalla.userChoice;
  console.info("[xr] installazione:", outcome);
  richiestaInstalla = null;
  bottoneInstalla.hidden = true;
};
addEventListener("appinstalled", () => { bottoneInstalla.hidden = true; console.info("[xr] installata"); });
if (q.get("prova") === "1") window.__xr = { mani, pannelli, THREE, sfera, S, ancora, cinema, FinestraPc, apriDinamico,
  cestino, butta, lancia, lanciati, FISSI, disposizioneIniziale };   // solo per le prove automatiche
