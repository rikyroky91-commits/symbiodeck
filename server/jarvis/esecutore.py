"""Claude Code come esecutore di Jarvis: un processo `claude -p` sempre aperto (stream-json in entrata e in
uscita), con tutti gli strumenti di Claude Code, lo schermo e Chrome. Fa i lavori che la voce gli affida (vedi
cervello.py); la sua sessione è quella che compare nell'app Claude sotto il progetto pc-panel. Ogni strumento
passa prima dalla guardia (guardia.py, hook PreToolUse)."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .memoria import componi
from .trame import descrivi_azione

QUI = Path(__file__).resolve().parent
PROGETTO = QUI.parent.parent   # la cartella pc-panel: le sessioni di Jarvis stanno sotto questo progetto nell'app
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
        self.sottofondo = {}   # lavori dell'esecutore in sottofondo: task_id -> ora di partenza
        self.progressi = []    # (ora, descrizione) da task_progress di Claude Code
        # conversazione in corso: sopravvive ai riavvii di Jarvis (e del PC)
        self.file_sessione = dati / "sessione.txt"
        self.file_storia = dati / "conversazioni.json"
        self.sessione = self._leggi_file(self.file_sessione).strip() or None
        self._domanda = ""
        self._domande_note = None  # domande nella sessione dopo l'ultima risposta di Jarvis (per accorgersi dell'app)
        if self.sessione and not self.registro().exists():
            self._cambia_sessione(None)   # era in un'altra cartella di lavoro: si riparte da una nuova
        self._blocco = threading.Lock()
        self._su_testo = self._su_azione = self._su_fine = self._su_strumento = lambda *a: None
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
        mcp = {"mcpServers": {"schermo": {"command": python, "args": [str(QUI / "schermo_mcp.py")]},
                              "visore": self._mcp_visore(python)}}
        f = self.dati / "mcp-jarvis.json"
        f.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
        return f

    def _mcp_visore(self, python):
        """Server MCP "visore" (visore_mcp.py): aprire cartelle, foto e finestre nel Quest 3 dell'utente."""
        return {"command": python, "args": [str(QUI / "visore_mcp.py")],
                "env": {"JARVIS_PORTA": str(self.porta), "JARVIS_INTERNO": self.chiave_interna}}

    def _comando(self, exe):
        """Claude Code completo, come nell'app: modello e ragionamento dei compiti, guardia, schermo, Chrome."""
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "bypassPermissions",
               "--append-system-prompt-file", str(componi(QUI / "esecutore.md", self.dati / "esecutore-con-memoria.md")),
               "--settings", str(self._file_impostazioni()), "--mcp-config", str(self._file_mcp()),
               "--model", self.imp.get("modello_compiti") or "opus", "--effort", self.imp.get("effort_compiti") or "high"]
        if self.imp.get("chrome", True):
            cmd.append("--chrome")
        if self.sessione:
            cmd += ["--resume", self.sessione]
        return cmd

    def _avvia(self):
        exe = trova_claude(self.imp.get("claude_exe", ""))
        if not exe:
            raise RuntimeError("Claude Code non trovato sul PC")
        cmd = self._comando(exe)
        env = dict(self._ambiente(), JARVIS_PORTA=str(self.porta), JARVIS_INTERNO=self.chiave_interna,
                   JARVIS_CONFIG=str(self.file_config))
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=str(self.cartella()), env=env, creationflags=SENZA_FINESTRA)
        self._modello_avviato = self._firma()
        threading.Thread(target=self._leggi, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._scarta_errori, args=(self.proc,), daemon=True).start()

    def _ambiente(self):
        return dict(os.environ)

    def _firma(self):
        return tuple(self.imp.get(k) for k in ("modello_compiti", "effort_compiti", "chrome"))

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
            self.sottofondo.clear()
            self._domande_note = None

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

    def cartella(self):
        """Dove lavora Claude Code: decide sotto quale progetto compaiono le sessioni nell'app Claude."""
        scelta = self.imp.get("cartella_lavoro") or ""
        return Path(scelta) if scelta and Path(scelta).is_dir() else PROGETTO

    def registro(self, sessione=None):
        """Il file .jsonl della sessione (~/.claude/projects/<cartella>/<id>.jsonl)."""
        return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.cartella())) / \
            f"{sessione or self.sessione}.jsonl"

    def _righe(self):
        try:
            return self.registro().read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def conta_domande(self):
        """Le domande nel registro della sessione (messaggi dell'utente), senza gli avvisi interni di fine lavoro.
        Si contano le domande e non le righe: quando l'app riprende una sessione riscrive anche le righe vecchie."""
        n = 0
        for riga in self._righe():
            try:
                d = json.loads(riga)
            except ValueError:
                continue
            c = (d.get("message") or {}).get("content") if d.get("type") == "user" else None
            if isinstance(c, str) and not c.lstrip().startswith("<task-notification>"):
                n += 1
        return n

    def continuata_nell_app(self):
        """True se nella sessione c'è una domanda che Jarvis non ha fatto (l'utente l'ha ripresa nell'app Claude):
        allora la conversazione è dell'app e Jarvis ne comincia una nuova."""
        if not self.sessione or self._domande_note is None:
            return False
        return self.conta_domande() > self._domande_note

    def mostra_nell_app(self):
        """Fa comparire la conversazione in corso nell'app Claude (sotto il progetto pc-panel) senza lasciarla:
        il comando rifiuta una sessione aperta, quindi si chiude un attimo il processo e la si riprende alla
        richiesta successiva (--resume). L'app legge anche quello che Jarvis aggiunge dopo."""
        with self._blocco:
            if not self.sessione or self.occupato or self.lavora_in_sottofondo():
                return None
            self.ferma()
            exe = trova_claude(self.imp.get("claude_exe", ""))
            r = subprocess.run([exe, "--desktop", "--resume", self.sessione], timeout=60, cwd=str(self.cartella()),
                               creationflags=SENZA_FINESTRA)
            if r.returncode != 0:
                raise RuntimeError(f"l'app Claude non ha preso la conversazione (codice {r.returncode})")
            self._segna_nell_app(self.sessione)
            return self.sessione

    def nell_app(self, sessione=None):
        sessione = sessione or self.sessione
        return any(c.get("id") == sessione and c.get("nell_app") for c in self.conversazioni())

    def _segna_nell_app(self, sessione):
        storia = self.conversazioni()
        for c in storia:
            if c.get("id") == sessione:
                c["nell_app"] = True
        try:
            self.file_storia.write_text(json.dumps(storia[:50], ensure_ascii=False, indent=1), encoding="utf-8")
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
            r = subprocess.run([exe, "--desktop", "--resume", sessione], timeout=60, cwd=str(self.cartella()),
                               creationflags=SENZA_FINESTRA)
            if r.returncode != 0:
                raise RuntimeError(f"l'app Claude non ha aperto la conversazione (codice {r.returncode})")
            return sessione

    def _scrivi(self, oggetto):
        self.proc.stdin.write((json.dumps(oggetto, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    # ------------------------------------------------------------ comandi
    def chiedi(self, testo):
        if self.continuata_nell_app():
            self.nuova_conversazione()   # l'utente l'ha ripresa nell'app: quella resta all'app
        if self.sessione and self._domande_note is None:
            self._domande_note = self.conta_domande()   # dopo un riavvio di Jarvis
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
            self._domande_note = self.conta_domande()
        if tipo == "stream_event":
            ev = d.get("event") or {}
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta" \
                    and not d.get("parent_tool_use_id"):
                self._su_testo(ev["delta"].get("text", ""))
        elif tipo == "assistant":
            # anche gli strumenti dell'esecutore (sotto-agente) compaiono sul pannello: si vede cosa sta facendo
            for blocco in (d.get("message") or {}).get("content") or []:
                if blocco.get("type") == "tool_use":
                    self._su_strumento(blocco.get("name", ""), blocco.get("input") or {})
                    descrizione = descrivi_azione(blocco.get("name", ""), blocco.get("input"))
                    if descrizione:
                        self._su_azione(descrizione)
        elif tipo == "system":
            sotto = d.get("subtype")
            if sotto == "background_tasks_changed":     # elenco completo dei lavori in sottofondo
                prima = self.sottofondo
                self.sottofondo = {t.get("task_id"): prima.get(t.get("task_id"), time.time())
                                   for t in d.get("tasks") or []}
            elif sotto == "task_notification":         # un lavoro è finito
                self.sottofondo.pop(d.get("task_id"), None)
            elif sotto == "task_progress" and d.get("description"):
                self.progressi = (self.progressi + [(time.time(), d["description"])])[-20:]
        elif tipo == "result":
            self.occupato = False
            errore = None
            if d.get("is_error"):
                errore = d.get("result") or d.get("subtype") or "errore di Claude Code"
            self._su_fine(d.get("result") or "", errore)


    def lavora_in_sottofondo(self):
        # un lavoro "dimenticato" (notifica persa) non deve tenere Jarvis occupato per sempre
        for k, t in list(self.sottofondo.items()):
            if time.time() - t > 3 * 3600:
                self.sottofondo.pop(k, None)
        return bool(self.sottofondo)

    def ultime_azioni_esecutore(self, dopo=0.0, quante=8):
        """Le ultime azioni dei sotto-agenti, lette dal loro registro (i lavori in sottofondo non passano
        dall'uscita della voce)."""
        if not self.sessione:
            return []
        cartella = self.registro().with_suffix("") / "subagents"
        azioni = []
        try:
            registri = sorted(cartella.glob("agent-*.jsonl"), key=lambda f: f.stat().st_mtime)[-2:]
        except OSError:
            return []
        for f in registri:
            if f.stat().st_mtime < dopo:
                continue
            try:
                righe = f.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
            except OSError:
                continue
            for riga in righe:
                try:
                    d = json.loads(riga)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                for b in (d.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        descrizione = descrivi_azione(b.get("name", ""), b.get("input"))
                        if descrizione:
                            azioni.append(descrizione)
        return azioni[-quante:]


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
