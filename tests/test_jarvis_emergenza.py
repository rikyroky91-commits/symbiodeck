"""Prove della modalità emergenza senza rete né GPU: python tests/test_jarvis_emergenza.py oppure pytest."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import emergenza  # noqa: E402


class JarvisFinto:
    def __init__(self, scelta="auto"):
        self.imp = {"emergenza": scelta}
        self.detti, self.cervelli, self.eventi = [], [], []

    def evento(self, **d):
        self.eventi.append(d)

    def di_subito(self, testo):
        self.detti.append(testo)

    def cambia_cervello(self, locale):
        self.cervelli.append(locale)


def nuova(scelta="auto"):
    j = JarvisFinto(scelta)
    e = emergenza.Emergenza(j, lambda *a: None)
    e.avvia_server = lambda: None          # niente llama-server vero
    e.ferma_server = lambda: None
    return j, e


def test_senza_internet_entra_e_poi_esce():
    j, e = nuova()
    e.offline = 1
    e.valuta()
    assert not e.attiva                     # un solo controllo fallito non basta
    e.offline = 2
    e.valuta()
    assert e.attiva and j.cervelli == [True] and "Internet" in e.motivo
    e.offline = 0
    e.valuta()
    assert not e.attiva and j.cervelli == [True, False]
    assert len(j.detti) == 2                # lo dice a voce entrando e uscendo


def test_limite_di_claude():
    j, e = nuova()
    assert not e.errore_claude("file non trovato")
    assert e.errore_claude("Claude AI usage limit reached|1795470000")
    time.sleep(0.2)                         # errore_claude valuta in un thread
    assert e.attiva and "limiti" in e.motivo
    e.limite_fino = time.time() - 1         # passati i 30 minuti
    e.valuta()
    assert not e.attiva


def test_impostazioni_sempre_e_mai():
    j, e = nuova("sempre")
    e.valuta()
    assert e.attiva
    j2, e2 = nuova("mai")
    e2.offline = 5
    assert not e2.errore_claude("usage limit reached")
    e2.valuta()
    assert not e2.attiva


def test_server_che_non_parte():
    j, e = nuova()

    def guasto():
        raise RuntimeError("niente modello")
    e.avvia_server = guasto
    e.offline = 2
    e.valuta()
    assert j.cervelli == []                 # resta sul cervello di prima
    assert any("Non riesco" in d for d in j.detti)


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
