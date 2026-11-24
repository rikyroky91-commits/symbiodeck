"""Prove del Cervello di Jarvis (senza processi): python tests/test_jarvis_cervello.py."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis.cervello import Cervello  # noqa: E402


# ── Oggetti finti ──────────────────────────────────────────────────────────

class FintoEsecutore:
    """Esecutore finto: cattura le richieste e simula disponibilità."""

    def __init__(self):
        self.ricevuti = []
        self.occupato = False
        self.sessione = None
        self.progressi = []
        self._chiamate = []

    def chiedi(self, testo):
        self.ricevuti.append(testo)

    def lavora_in_sottofondo(self):
        return False

    def interrompi(self):
        pass

    def ferma(self):
        pass

    def nuova_conversazione(self):
        pass

    def ascoltatori(self, *_a, **_k):
        pass

    def conversazioni(self):
        return []

    def apri_nell_app(self, sessione=None):
        return False

    def mostra_nell_app(self):
        return False

    def nell_app(self, sessione=None):
        return False


class FintoVoce:
    """Voce finta: cattura le chiamate a chiedi e simula strumenti."""

    def __init__(self):
        self.ricevuti = []
        self.occupato = False
        self._strumento_cb = None

    def chiedi(self, testo):
        self.ricevuti.append(testo)

    def interrompi(self):
        pass

    def ferma(self):
        pass

    def nuova_conversazione(self):
        pass

    def ascolta(self, *_a, **_k):
        pass

    def ascoltatori(self, *_a, **_k):
        pass

    def _invoca_strumento(self, nome, ingresso):
        if self._strumento_cb:
            self._strumento_cb(nome, ingresso)

    def _file_mcp_voce(self):
        return Path("/dev/null")


def _costruisci_cervello():
    """Crea un Cervello con voce ed esecutore sostituiti."""
    dati = Path(__file__).resolve().parent / "_tmp_cervello"
    imp = {}
    c = Cervello(dati, imp, 0, "chiave", None, log=lambda *a: None)
    f_voce = FintoVoce()
    f_esec = FintoEsecutore()
    c.voce = f_voce
    c.esec = f_esec
    return c, f_voce, f_esec


# ── Test ───────────────────────────────────────────────────────────────────

def test_v_strumento_esegui_passa_richiesta_ed_frase():
    c, f_voce, f_esec = _costruisci_cervello()
    c._ultima_frase = "quanti file?"
    c._v_strumento("mcp__jarvis__esegui", {"richiesta": "conta i file"})
    time.sleep(0.2)
    assert len(f_esec.ricevuti) == 1
    assert "conta i file" in f_esec.ricevuti[0]
    assert "quanti file?" in f_esec.ricevuti[0]


def test_e_fine_risultato_ok():
    c, f_voce, f_esec = _costruisci_cervello()
    c.richiesta_lavoro = "conta i file"
    c._e_fine("ci sono 3 file", None)
    assert len(f_voce.ricevuti) == 1
    assert "[Esito del lavoro" in f_voce.ricevuti[0]
    assert "ci sono 3 file" in f_voce.ricevuti[0]


def test_e_fine_errore():
    c, f_voce, f_esec = _costruisci_cervello()
    c.richiesta_lavoro = "conta i file"
    c._e_fine("", "boom")
    assert len(f_voce.ricevuti) == 1
    assert "Errore: boom" in f_voce.ricevuti[0]


def test_stato_lavoro_chiavi():
    c, f_voce, f_esec = _costruisci_cervello()
    stato = c.stato_lavoro()
    assert "occupato" in stato
    assert "richiesta" in stato
    assert "minuti" in stato
    assert "azioni" in stato


def test_v_strumento_stato_lavoro_non_manda_niente():
    c, f_voce, f_esec = _costruisci_cervello()
    prima = len(f_esec.ricevuti)
    c._v_strumento("mcp__jarvis__stato_lavoro", {})
    time.sleep(0.1)
    assert len(f_esec.ricevuti) == prima


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
