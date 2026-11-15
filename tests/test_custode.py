"""Prove della decisione del custode del gioco (senza Windows): python tests/test_custode.py oppure pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from pannello import DecisioneGioco  # noqa: E402

GIOCO, APP = 100, 200
VIRT, FUORI = True, False


def giro(d, eventi, pad_presente=True):
    """eventi: (ora, davanti, cursore sul virtuale, apertura, pad usato). Ritorna gli istanti in cui il gioco va
    ridato."""
    ridati = []
    for ora, davanti, virt, apertura, pad in eventi:
        r = d.passo(ora, davanti, davanti == GIOCO, True, virt, apertura, pad, pad_presente)
        if r:
            ridati.append((ora, r))
    return ridati


def test_tocco_poi_controller():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False, False), (1, APP, VIRT, False, False), (3, APP, VIRT, False, False),
                    (4, APP, VIRT, False, True)]) == [(4, GIOCO)]


def test_scrivere_sul_tablet_non_ridà_il_gioco():
    d = DecisioneGioco()
    ev = [(0, GIOCO, FUORI, False, False), (1, APP, VIRT, False, False)]
    ev += [(1 + i, APP, VIRT, False, False) for i in range(1, 120)]  # due minuti a scrivere, pad fermo
    assert giro(d, ev) == []
    assert d.gioco == GIOCO  # ma il gioco è ricordato: il primo tasto del pad lo riporta davanti
    assert giro(d, [(200, APP, VIRT, False, True)]) == [(200, GIOCO)]


def test_mouse_fuori_dal_tablet_con_il_pad_non_basta():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False, False), (1, APP, VIRT, False, False), (2, APP, FUORI, False, False)]) == []


def test_senza_controller_vale_il_mouse():
    d = DecisioneGioco()
    ev = [(0, GIOCO, FUORI, False, False), (1, APP, VIRT, False, False), (5, APP, VIRT, False, False),
          (6, APP, FUORI, False, False)]
    assert giro(d, ev, pad_presente=False) == [(6, GIOCO)]


def test_alt_tab_voluto_non_si_tocca():
    d = DecisioneGioco()
    assert giro(d, [(0, GIOCO, FUORI, False, False), (1, APP, FUORI, False, False), (5, APP, FUORI, False, True)]) == []
    assert d.gioco is None


def test_aspetta_l_apertura_dalla_dock():
    d = DecisioneGioco()
    ev = [(0, GIOCO, FUORI, False, False), (1, APP, VIRT, False, False), (2, APP, VIRT, True, True),
          (6, APP, VIRT, False, True)]
    assert giro(d, ev) == [(6, GIOCO)]


def test_gioco_chiuso():
    d = DecisioneGioco()
    d.passo(0, GIOCO, True, True, False, False, False, True)
    assert d.passo(1, APP, False, False, True, False, True, True) is None
    assert d.gioco is None


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
