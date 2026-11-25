"""Domande e conferme che finiscono senza risposta (2026-11-25): Jarvis non deve restare nello stato "conferma"
(il tablet mostrava lo schermo rosso per ore dopo "Un visore vuole collegarsi… Lo consento?" consentito dal PC).
Senza audio, rete né modelli: python tests/test_jarvis_domande.py"""
import queue
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import principale  # noqa: E402
from jarvis.principale import Jarvis  # noqa: E402

# i test non scrivono nel registro vero di Jarvis (server/jarvis.log)
principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"


class _Detto(queue.Queue):
    """La coda delle frasi da dire, ma già "detta": nei test non c'è nessuno che parla."""
    def put(self, item, block=True, timeout=None):
        pass


def _jarvis(occupato=False):
    j = Jarvis.__new__(Jarvis)
    j.stato, j.conferma, j.eventi = "riposo", None, []
    j.imp = {"seguito": 0}
    j.blocco_domande = threading.Lock()
    j.da_dire = _Detto()
    j.uscita = types.SimpleNamespace(riprendi=lambda: None, taci=lambda: None, parla=False)
    j.claude = types.SimpleNamespace(occupato=occupato, lavora_in_sottofondo=lambda: False)
    j.evento = lambda **d: j.eventi.append(d)
    j._aggiorna_microfono = lambda: None
    return j


def test_domanda_scaduta_torna_a_riposo():
    j = _jarvis()
    r = Jarvis.chiedi_risposta(j, "Un visore vuole collegarsi. Lo consento?", ["Consenti", "Rifiuta"], attesa=0.6)
    assert r == "" and j.conferma is None and j.stato == "riposo"


def test_domanda_chiusa_da_fuori():
    j = _jarvis()
    esito = {}
    t = threading.Thread(target=lambda: esito.setdefault("r", Jarvis.chiedi_risposta(j, "Lo consento?", [], attesa=30)))
    t.start()
    for _ in range(100):
        if j.conferma:
            break
        time.sleep(0.02)
    time.sleep(0.5)                                  # intanto Jarvis è passato allo stato "conferma"
    assert j.stato == "conferma"
    assert Jarvis.chiudi_domanda(j)
    t.join(5)
    assert not t.is_alive() and esito["r"] == "" and j.stato == "riposo"
    assert not Jarvis.chiudi_domanda(j)              # nessuna domanda aperta


def test_conferma_scaduta_con_un_lavoro_in_corso():
    j = _jarvis(occupato=True)
    j.permessi = types.SimpleNamespace(decisione=lambda *a: None)
    j.solo_remoto = False
    assert Jarvis._chiedi_conferma(j, "cancellare dei file: prova", attesa=0.6) is False
    assert j.stato == "lavoro"                       # sta ancora lavorando: non "riposo", ma mai "conferma"


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
