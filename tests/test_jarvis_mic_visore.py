"""Microfono del Quest (2026-11-25, Jarvis XR nativo): "Hey Jarvis" detto nel visore arriva a Jarvis solo mentre il
visore è addosso; intanto il microfono del tablet (e quello del PC, se il Quest manda audio) non si mescola.
Senza audio: python tests/test_jarvis_mic_visore.py"""
import queue
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import principale  # noqa: E402
from jarvis.principale import PREDEFINITE, Jarvis  # noqa: E402

principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"


def _jarvis(microfono="tablet"):
    j = Jarvis.__new__(Jarvis)
    j.imp = dict(PREDEFINITE, attivo=True, microfono=microfono)
    j.blocchi = queue.Queue()
    j.visore_immersivo = False
    j.uscita = types.SimpleNamespace(aec=None, parla=False, fine_audio=0.0)
    j.mic_visore = types.SimpleNamespace(ultimo=0.0, microfono_attivo=lambda: time.time() - j.mic_visore.ultimo < 2)
    return j


def _da(j, chi):
    b = np.zeros(1280, dtype=np.int16)
    prima = j.blocchi.qsize()
    j._blocco(chi, b)
    return j.blocchi.qsize() > prima


def test_visore_solo_col_visore_addosso():
    j = _jarvis()
    assert not _da(j, "visore")            # visore tolto: il suo microfono non conta
    j.visore_immersivo = True
    assert _da(j, "visore")


def test_tablet_spento_col_visore():
    j = _jarvis()
    assert _da(j, "tablet")
    j.visore_immersivo = True
    assert not _da(j, "tablet")


def test_microfono_del_pc_tace_solo_se_il_quest_manda_audio():
    j = _jarvis("pc")
    j.visore_immersivo = True
    assert _da(j, "pc")                    # Quest senza microfono (Jarvis XR nel browser): resta il PC
    j.mic_visore.ultimo = time.time()
    assert not _da(j, "pc")                # il Quest manda audio: un microfono solo
    assert _da(j, "visore")


def test_visore_sordo_mentre_jarvis_parla():
    j = _jarvis()
    j.visore_immersivo = True
    j.uscita.parla = True
    assert not _da(j, "visore")
    j.uscita.parla = False
    j.uscita.fine_audio = time.monotonic()        # appena finito: ancora un attimo di silenzio
    assert not _da(j, "visore")
    j.uscita.fine_audio = time.monotonic() - 1
    assert _da(j, "visore")


def test_jarvis_spento_non_ascolta_nessuno():
    j = _jarvis()
    j.imp["attivo"] = False
    j.visore_immersivo = True
    assert not _da(j, "visore") and not _da(j, "tablet")


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
    print("ok")
