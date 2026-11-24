"""Decisioni ricordate per le conferme della guardia: "sì, sempre" e "no, mai".

Idea da OpenJarvis (tools/approval_store.py, Apache 2.0): ogni azione ha un'impronta e la risposta dell'utente può
valere per sempre. Qui l'impronta è stretta apposta: tipo di azione + comando esatto (o percorso esatto), così "sì,
sempre" vale solo per lo stesso identico comando (es. la pulizia di %TEMP% che fai ogni settimana), mai per una
famiglia di azioni. Alcune azioni non accettano mai "sì, sempre" (vedi guardia.SEMPRE_VIETATO): dischi, registro,
sicurezza, spegnimento, cancellazioni definitive, disinstallazioni, messaggi, app collegate, file di sistema.
"No, mai" invece vale per tutto. File: server/jarvis-dati/permessi.json (fuori da git).
"""
import json
import os
import threading
import time
from pathlib import Path

SI, NO = "sempre_si", "sempre_no"


class Permessi:
    def __init__(self, file: Path, log=print):
        self.file = Path(file)
        self.log = log
        self._blocco = threading.Lock()
        try:
            self.voci = json.loads(self.file.read_text(encoding="utf-8")).get("permessi", {})
        except (OSError, ValueError, AttributeError):
            self.voci = {}

    def _salva(self):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"permessi": self.voci}, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError as e:
            self.log("permessi non salvati:", repr(e))

    def decisione(self, impronta, sempre_ok):
        """True / False se c'è una decisione ricordata valida, None se bisogna chiedere."""
        if not impronta:
            return None
        v = self.voci.get(impronta)
        if not v:
            return None
        if v["decisione"] == NO:
            return False
        return True if sempre_ok else None   # un vecchio "sì" su un'azione ora vietata non vale

    def ricorda(self, impronta, ok, descrizione, sempre_ok):
        if not impronta or (ok and not sempre_ok):
            return False
        with self._blocco:
            self.voci[impronta] = {"decisione": SI if ok else NO, "descrizione": descrizione[:300],
                                   "quando": time.strftime("%Y-%m-%d %H:%M")}
            self._salva()
        self.log("permesso ricordato:", SI if ok else NO, "-", descrizione[:160])
        return True

    def elenco(self):
        return [{"impronta": k, **v} for k, v in sorted(self.voci.items(), key=lambda kv: kv[1].get("quando", ""))]

    def togli(self, impronta):
        with self._blocco:
            if self.voci.pop(impronta, None) is None:
                return False
            self._salva()
        return True

    def azzera(self):
        with self._blocco:
            n, self.voci = len(self.voci), {}
            self._salva()
        return n
