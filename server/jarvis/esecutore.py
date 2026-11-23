"""Claude Code come esecutore di Jarvis: un processo `claude -p` sempre aperto (stream-json in entrata e in
uscita), così la conversazione continua da un comando vocale all'altro e la risposta arriva a pezzi, da dire
frase per frase. Ogni strumento passa prima dalla guardia (guardia.py, hook PreToolUse)."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .trame import descrivi_azione

QUI = Path(__file__).resolve().parent
SENZA_FINESTRA = 0x08000000  # CREATE_NO_WINDOW


def trova_claude(scelto=""):
    candidati = [scelto] if scelto else []
    candidati += [shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe"),
                  os.path.expandvars(r"%APPDATA%\npm\claude.cmd")]
    return next((c for c in candidati if c and os.path.exists(c)), None)


def python_senza_finestra():
    """pythonw.exe accanto all'interprete in uso: gli hook non aprono finestre nere."""
    eseguibile = Path(sys.executable)
    w = eseguibile.with_name("pythonw.exe")
    return str(w if w.exists() else eseguibile)


class Esecutore:
    """Uso: e = Esecutore(...); e.ascoltatori(...); e.chiedi("apri il blocco note").
    Gli ascoltatori sono chiamati dal thread di lettura:
      testo(pezzo)                 - testo della risposta, a pezzi
      azione(descrizione)          - uno strumento sta per partire
      fine(risultato, errore)      - il turno è finito (errore: testo o None)"""

    def __init__(self, dati: Path, impostazioni, porta, chiave_interna, file_config):
        self.dati = dati
        self.imp = impostazioni          # dict condiviso con Jarvis (modello, claude_exe)
        self.porta = porta
        self.chiave_interna = chiave_interna
        self.file_config = file_config
        self.proc = None
        self.occupato = False
        # conversazione in corso: sopravvive ai riavvii di Jarvis (e del PC)
        self.file_sessione = dati / "sessione.txt"
        self.file_storia = dati / "conversazioni.json"
        self.sessione = self._leggi_file(self.file_sessione).strip() or None
        self._domanda = ""
        self._blocco = threading.Lock()
        self._su_testo = self._su_azione = self._su_fine = lambda *a: None
        self._modello_avviato = None

    def ascoltatori(self, testo, azione, fine):
        self._su_testo, self._su_azione, self._su_fine = testo, azione, fine

    # ------------------------------------------------------------ processo
    def _file_impostazioni(self):
        guardia = f'"{python_senza_finestra()}" "{QUI / "guardia.py"}"'.replace("\\", "/")
        imp = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": guardia, "timeout": 90}]}]}}
        f = self.dati / "impostazioni-claude.json"
        f.write_text(json.dumps(imp, indent=2), encoding="utf-8")
        return f

    def _file_mcp(self):
        """Server MCP in più rispetto al Claude Code normale: "schermo" (vedere lo schermo, mouse e tastiera), che
        la riga di comando su Windows non ha e l'app Claude sì."""
        python = str(Path(sys.executable).with_name("python.exe"))  # stdio: serve la console, non pythonw
        mcp = {"mcpServers": {"schermo": {"command": python, "args": [str(QUI / "schermo_mcp.py")]}}}
        f = self.dati / "mcp-jarvis.json"
        f.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
        return f

    def _avvia(self):
        exe = trova_claude(self.imp.get("claude_exe", ""))
        if not exe:
            raise RuntimeError("Claude Code non trovato sul PC")
        # due modelli: la voce (sessione principale, risponde subito) e l'esecutore (sotto-agente per i compiti)
        esecutore = {"esecutore": {
            "description": "Esegue sul PC qualsiasi compito vero (file, programmi, configurazioni, codice, ricerche, "
                           "schermo, browser) e riporta l'esito. Usalo per tutto ciò che va oltre un comando singolo.",
            "prompt": (QUI / "esecutore.md").read_text(encoding="utf-8"),
            "model": self.imp.get("modello_compiti") or "opus",
            "effort": self.imp.get("effort_compiti") or "high",
        }}
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions",
               "--append-system-prompt-file", str(QUI / "persona.md"),
               "--settings", str(self._file_impostazioni()), "--mcp-config", str(self._file_mcp()),
               "--agents", json.dumps(esecutore, ensure_ascii=False),
               "--model", self.imp.get("modello_voce") or "sonnet", "--effort", self.imp.get("effort_voce") or "low"]
        if self.imp.get("chrome", True):
            cmd.append("--chrome")
        if self.sessione:
            cmd += ["--resume", self.sessione]
        env = dict(os.environ, JARVIS_PORTA=str(self.porta), JARVIS_INTERNO=self.chiave_interna,
                   JARVIS_CONFIG=str(self.file_config), JARVIS_DELEGA="1")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=str(Path.home()), env=env, creationflags=SENZA_FINESTRA)
        self._modello_avviato = self._firma()
        threading.Thread(target=self._leggi, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._scarta_errori, args=(self.proc,), daemon=True).start()

    def _firma(self):
        return tuple(self.imp.get(k) for k in ("modello_voce", "effort_voce", "modello_compiti", "effort_compiti",
                                                "chrome"))

    def _vivo(self):
        return self.proc is not None and self.proc.poll() is None

    def ferma(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()

    def nuova_conversazione(self):
        with self._blocco:
            self.ferma()
            self._cambia_sessione(None)
            self.occupato = False

    # ------------------------------------------------------------ conversazioni salvate
    @staticmethod
    def _leggi_file(f):
        try:
            return f.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _cambia_sessione(self, nuova):
        self.sessione = nuova
        try:
            if nuova:
                self.file_sessione.write_text(nuova, encoding="utf-8")
            elif self.file_sessione.exists():
                self.file_sessione.unlink()
        except OSError:
            pass

    def conversazioni(self):
        """Le ultime conversazioni di Jarvis, la più recente per prima: [{id, titolo, inizio, ultimo}]."""
        try:
            storia = json.loads(self._leggi_file(self.file_storia) or "[]")
        except ValueError:
            storia = []
        return sorted(storia, key=lambda c: c.get("ultimo", 0), reverse=True)

    def _ricorda(self, sessione):
        storia = [c for c in self.conversazioni() if c.get("id") != sessione]
        vecchia = next((c for c in self.conversazioni() if c.get("id") == sessione), None)
        ora = time.time()
        voce = vecchia or {"id": sessione, "titolo": self._domanda[:80], "inizio": ora}
        if not voce.get("titolo"):
            voce["titolo"] = self._domanda[:80]
        voce["ultimo"] = ora
        try:
            self.file_storia.write_text(json.dumps(([voce] + storia)[:50], ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        except OSError:
            pass

    def apri_nell_app(self, sessione=None):
        """Apre una conversazione nell'app Claude per Windows (claude --desktop --resume <id>). Jarvis la lascia
        all'app: due programmi che scrivono sulla stessa conversazione perderebbero messaggi, quindi se era quella
        in corso Jarvis ne comincia una nuova alla prossima richiesta."""
        with self._blocco:
            sessione = sessione or self.sessione
            if not sessione:
                raise ValueError("nessuna conversazione da aprire")
            if sessione == self.sessione:
                self.ferma()
                self._cambia_sessione(None)
                self.occupato = False
            exe = trova_claude(self.imp.get("claude_exe", ""))
            if not exe:
                raise RuntimeError("Claude Code non trovato sul PC")
            # --desktop rifiuta l'uscita rediretta: niente capture_output, una console nascosta tutta sua
            r = subprocess.run([exe, "--desktop", "--resume", sessione], timeout=60, cwd=str(Path.home()),
                               creationflags=SENZA_FINESTRA)
            if r.returncode != 0:
                raise RuntimeError(f"l'app Claude non ha aperto la conversazione (codice {r.returncode})")
            return sessione

    def _scrivi(self, oggetto):
        self.proc.stdin.write((json.dumps(oggetto, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    # ------------------------------------------------------------ comandi
    def chiedi(self, testo):
        with self._blocco:
            if self._vivo() and self._modello_avviato != self._firma():
                self.ferma()  # modello o impostazioni cambiate: si riparte dalla stessa conversazione
            if not self._vivo():
                self._avvia()
            self.occupato = True
            self._domanda = self._domanda or testo
            self._scrivi({"type": "user", "message": {"role": "user", "content": testo}})

    def interrompi(self):
        if self._vivo() and self.occupato:
            try:
                self._scrivi({"type": "control_request", "request_id": uuid.uuid4().hex,
                              "request": {"subtype": "interrupt"}})
            except OSError:
                pass

    # ------------------------------------------------------------ uscita di claude
    def _leggi(self, proc):
        for riga in proc.stdout:
            try:
                d = json.loads(riga)
            except ValueError:
                continue
            try:
                self._evento(d)
            except Exception as e:  # un ascoltatore che sbaglia non deve fermare la lettura
                print("jarvis: errore evento", repr(e), file=sys.stderr)
        if proc is self.proc or self.proc is None:
            if self.occupato:
                self.occupato = False
                self._su_fine("", "Claude Code si è chiuso")

    def _scarta_errori(self, proc):
        ultime = []
        for riga in proc.stderr:
            ultime = (ultime + [riga.decode("utf-8", "replace").rstrip()])[-20:]
        self.ultimi_errori = ultime

    def _evento(self, d):
        tipo = d.get("type")
        if d.get("session_id") and d["session_id"] != self.sessione:
            self._cambia_sessione(d["session_id"])
        if tipo == "result" and self.sessione:
            self._ricorda(self.sessione)  # la conversazione esiste davvero (c'è stato un turno)
            self._domanda = ""
        if tipo == "stream_event":
            ev = d.get("event") or {}
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta" \
                    and not d.get("parent_tool_use_id"):
                self._su_testo(ev["delta"].get("text", ""))
        elif tipo == "assistant":
            # anche gli strumenti dell'esecutore (sotto-agente) compaiono sul pannello: si vede cosa sta facendo
            for blocco in (d.get("message") or {}).get("content") or []:
                if blocco.get("type") == "tool_use":
                    descrizione = descrivi_azione(blocco.get("name", ""), blocco.get("input"))
                    if descrizione:
                        self._su_azione(descrizione)
        elif tipo == "result":
            self.occupato = False
            errore = None
            if d.get("is_error"):
                errore = d.get("result") or d.get("subtype") or "errore di Claude Code"
            self._su_fine(d.get("result") or "", errore)


def prova(testo):
    """python -m jarvis.esecutore "domanda": prova a riga di comando senza voce (stampa la risposta)."""
    fatto = threading.Event()
    dati = QUI.parent / "jarvis-dati" / "prova"  # le prove non toccano la conversazione di Jarvis
    dati.mkdir(parents=True, exist_ok=True)
    e = Esecutore(dati, {"modello_voce": "sonnet", "effort_voce": "low", "modello_compiti": "sonnet",
                         "effort_compiti": "low"}, 47802, "", QUI.parent / "jarvis.json")
    t0 = time.time()
    e.ascoltatori(lambda p: print(p, end="", flush=True),
                  lambda a: print(f"\n[{time.time() - t0:.1f}s] {a}", flush=True),
                  lambda r, err: (print(f"\n[{time.time() - t0:.1f}s] fine", err or ""), fatto.set()))
    e.chiedi(testo)
    fatto.wait(300)
    e.ferma()


if __name__ == "__main__":
    prova(" ".join(sys.argv[1:]) or "Che ore sono?")
