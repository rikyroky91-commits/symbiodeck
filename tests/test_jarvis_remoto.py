"""Prove delle novità per iPhone, Apple Watch e Comandi Rapidi (/jarvis/eventi): python tests/test_jarvis_remoto.py"""
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import jarvis.principale as p  # noqa: E402


def finto():
    f = types.SimpleNamespace(cond=threading.Condition(), eventi=[], n_evento=0, conferma=None, stato="riposo",
                              claude=types.SimpleNamespace(lavora_in_sottofondo=lambda: False),
                              emergenza=types.SimpleNamespace(attiva=False))

    def ev(**d):
        f.n_evento += 1
        f.eventi.append((f.n_evento, d))
    return f, ev


def test_risposta_a_meta_arriva_intera_dopo():
    f, ev = finto()
    ev(utente="quanto spazio?")
    ev(jarvis_pezzo="Sul disco C ")
    ev(jarvis_pezzo="restano 94 giga")
    a = p.Jarvis.novita(f, 0)
    assert a["messaggi"] == [{"chi": "utente", "testo": "quanto spazio?"}]
    ev(jarvis_pezzo=", un po' tirato.")
    ev(fine=True)
    b = p.Jarvis.novita(f, a["ultimo"])
    assert b["messaggi"] == [{"chi": "jarvis", "testo": "Sul disco C restano 94 giga, un po' tirato."}]
    assert p.Jarvis.novita(f, b["ultimo"])["messaggi"] == []


def test_azioni_e_frasi_intere():
    f, ev = finto()
    ev(azione="Eseguo: Get-PSDrive")
    ev(jarvis="Ci guardo.")
    n = p.Jarvis.novita(f, 0)
    assert [m["chi"] for m in n["messaggi"]] == ["azione", "jarvis"]
    assert n["ultimo"] == 2


def test_conferma_in_attesa_visibile():
    f, _ev = finto()
    f.conferma = {"id": "ab12", "descrizione": "cancellare dei file: Remove-Item x", "esito": None, "ok": False}
    n = p.Jarvis.novita(f, 0)
    assert n["conferma"] == {"id": "ab12", "descrizione": "cancellare dei file: Remove-Item x"}


def test_indirizzi_tailscale_accettati():
    assert p.rete_locale("100.101.5.7") and p.rete_locale("192.168.1.20") and p.rete_locale("127.0.0.1")
    assert not p.rete_locale("8.8.8.8")


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
