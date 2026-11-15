"""Prove della decisione del custode del gioco (senza Windows): python tests/test_custode.py oppure pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from pannello import DecisioneGioco  # noqa: E402

GIOCO, APP = 100, 200
VIRT, FUORI = (5000, 500), (1900, 1000)


def giro(d, eventi):
    """eventi: (ora, davanti, cursore, apertura). Ritorna gli istanti in cui il gioco va ridato."""
    ridati = []
    for ora, davanti, cursore, apertura in eventi:
        r = d.passo(ora, davanti, davanti == GIOCO, True, cursore, cursore[0] >= 3840, apertura)
        if r:
            ridati.append((ora, r))
    return ridati


def test_tocco_sul_secondo_monitor():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False), (1, APP, VIRT, False), (1.5, APP, VIRT, False),
                    (2.3, APP, VIRT, False)]) == [(2.3, GIOCO)]


def test_trascinamento_aspetta_che_finisca():
    d = DecisioneGioco()
    ev = [(0, GIOCO, FUORI, False), (1, APP, VIRT, False)]
    ev += [(1 + i * 0.3, APP, (5000 + i, 500), False) for i in range(1, 10)]  # dito che scorre fino a 3,7 s
    ev += [(4.0, APP, (5009, 500), False), (5.0, APP, (5009, 500), False)]
    assert giro(d, ev) == [(5.0, GIOCO)]


def test_mouse_vero_che_torna_sul_gioco():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False), (1, APP, VIRT, False), (1.3, APP, FUORI, False)]) == [(1.3, GIOCO)]


def test_alt_tab_voluto_non_si_tocca():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False), (1, APP, FUORI, False), (5, APP, FUORI, False)]) == []
    assert d.gioco is None


def test_aspetta_l_apertura_dalla_dock():
    d = DecisioneGioco()
    ev = [(0, GIOCO, FUORI, False), (1, APP, VIRT, False), (2, APP, VIRT, True), (6, APP, VIRT, True),
          (6.5, APP, VIRT, False), (7.8, APP, VIRT, False)]
    assert giro(d, ev) == [(7.8, GIOCO)]


def test_gioco_chiuso():
    d = DecisioneGioco()
    d.passo(0, GIOCO, True, True, FUORI, False, False)
    assert d.passo(1, APP, False, False, VIRT, True, False) is None
    assert d.gioco is None


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
