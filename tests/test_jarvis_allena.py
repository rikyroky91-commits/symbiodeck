"""Registrazioni per allenare "Hey Jarvis" (allena_parola.py): il microfono finisce nel file e non nell'ascolto, e i
conteggi di frasi e accensioni. Senza audio né modelli: python tests/test_jarvis_allena.py"""
import queue
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import allena_parola, principale  # noqa: E402

# i test non scrivono nel registro vero di Jarvis (server/jarvis.log)
principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"


def test_registrazione_salva_il_wav_e_jarvis_non_ascolta():
    with tempfile.TemporaryDirectory() as tmp:
        finite = []
        j = principale.Jarvis.__new__(principale.Jarvis)
        j.imp, j.visore_immersivo, j.blocchi = {"attivo": True, "microfono": "tablet"}, False, queue.Queue()
        j.uscita = types.SimpleNamespace(aec=None)
        j.registrazione = allena_parola.Registrazione("si", 60, lambda *a: finite.append(a), cartella=tmp)
        for valore in (1, 2, 3):
            j._blocco("tablet", np.full(1280, valore, dtype=np.int16))
        assert j.blocchi.empty()
        j.registrazione.chiudi()
        percorso, secondi = finite[0]
        assert percorso.parent == Path(tmp) / "si" and abs(secondi - 0.24) < 1e-6
        pcm = allena_parola.leggi(percorso)
        assert len(pcm) == 3 * 1280 and pcm[0] == 1 and pcm[-1] == 3


def test_senza_microfono_niente_file():
    with tempfile.TemporaryDirectory() as tmp:
        finite = []
        allena_parola.Registrazione("no", 60, lambda *a: finite.append(a), cartella=tmp).chiudi()
        assert finite == [(None, 0.0)] and not list(Path(tmp).iterdir())


def test_etichetta_sbagliata():
    try:
        allena_parola.Registrazione("../x", 5, print)
    except ValueError:
        return
    raise AssertionError("doveva rifiutarla")


def test_frasi_e_accensioni():
    parlato = [0] * 5 + [1] * 8 + [0] * 3 + [1] * 4 + [0] * 20 + [1] * 6 + [0] * 20 + [1]
    assert allena_parola.frasi(parlato) == [(5, 19), (40, 45)]          # pausa breve unita, colpo isolato ignorato
    punteggi = [0, 0.5, 0.8, 0.1] + [0] * 30 + [0.45]
    assert allena_parola.accensioni(punteggi, 0.4) == [1, 34]


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
