"""Prove della classe Blocchi (server/jarvis/audio.py): python tests/test_jarvis_audio.py oppure pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import numpy as np  # noqa: E402
from jarvis.audio import Blocchi, BLOCCO_IN  # noqa: E402


def test_3000_byte_un_blocco_e_resto():
    """3000 byte → 1 blocco di 1280 campioni int16 (2560 byte) e resto 440 byte in buf."""
    blocchi = []
    b = Blocchi(su_blocco=blocchi.append)
    dati = b"X" * 3000
    b.aggiungi(dati)
    assert len(blocchi) == 1
    assert len(blocchi[0]) == BLOCCO_IN  # 1280 campioni
    assert len(b.buf) == 440


def test_2120_byte_dopo_un_blocco():
    """Poi altri 2120 byte → un blocco di 1280 campioni (2560 byte) e resto 2560 byte in buf."""
    blocchi = []
    b = Blocchi(su_blocco=blocchi.append)
    # prima: 3000 byte → 1 blocco, 440 byte in buf
    b.aggiungi(b"X" * 3000)
    assert len(blocchi) == 1
    assert len(b.buf) == 440
    # poi: altri 2120 byte → 440 + 2120 = 2560 → 1 blocco, 0 in buf
    b.aggiungi(b"Y" * 2120)
    assert len(blocchi) == 2
    assert len(b.buf) == 0


def test_7_pezzi_da_1000_byte_due_blocchi():
    """Dati in 7 pezzi da 1000 byte → 2 blocchi."""
    blocchi = []
    b = Blocchi(su_blocco=blocchi.append)
    for _ in range(7):
        b.aggiungi(b"A" * 1000)
    # 7000 byte totali → 7000 // 2560 = 2 blocchi, resto 7000 - 5120 = 1880
    assert len(blocchi) == 2
    assert len(b.buf) == 1880


def test_valori_del_blocco_corrispondono_ai_byte_inseriti():
    """I valori del blocco corrispondono ai byte inseriti (usa numpy.arange(..., dtype=numpy.int16).tobytes())."""
    blocchi = []
    b = Blocchi(su_blocco=blocchi.append)
    # genera dati con valori prevedibili: arange int16
    # np.arange(0, 5120, dtype=np.int16).tobytes() → 10240 byte = 4 blocchi
    dati = np.arange(0, 5120, dtype=np.int16).tobytes()
    b.aggiungi(dati)
    assert len(blocchi) == 4
    # il primo blocco deve contenere i primi 1280 valori di arange
    atteso = np.arange(0, 1280, dtype=np.int16)
    np.testing.assert_array_equal(blocchi[0], atteso)
    # il secondo blocco deve contenere i valori da 1280 a 2559
    atteso2 = np.arange(1280, 2560, dtype=np.int16)
    np.testing.assert_array_equal(blocchi[1], atteso2)
    # il terzo blocco: 2560..3839
    atteso3 = np.arange(2560, 3840, dtype=np.int16)
    np.testing.assert_array_equal(blocchi[2], atteso3)
    # il quarto blocco: 3840..5119
    atteso4 = np.arange(3840, 5120, dtype=np.int16)
    np.testing.assert_array_equal(blocchi[3], atteso4)


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
