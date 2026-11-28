"""Prove dei conti degli FPS letti da RivaTuner (senza Windows): python tests/test_rivatuner.py oppure pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from rivatuner import Statistiche  # noqa: E402


def test_ora_e_media():
    s = Statistiche()
    s.intervallo(1000, 2000, 120)
    s.intervallo(1000, 2000, 120)   # lo stesso intervallo riletto al campione dopo: non si conta due volte
    s.intervallo(2000, 3000, 60)
    r = s.risultato()
    assert r["ora"] == 60
    assert r["media"] == 90
    assert r["low1"] is None        # meno di 100 tempi: troppo pochi


def test_low1_dai_fotogrammi_peggiori():
    s = Statistiche()
    s.intervallo(0, 1000, 100)
    for _ in range(198):
        s.tempo(10_000)             # 100 FPS
    s.tempo(50_000)                 # due scatti da 20 FPS
    s.tempo(50_000)
    assert round(s.risultato()["low1"]) == 20


def test_valori_assurdi_scartati():
    s = Statistiche()
    s.intervallo(5000, 5000, 10)    # durata zero
    s.intervallo(0, 100000, 10)     # oltre un minuto: non è un intervallo di RivaTuner
    s.tempo(0)
    s.tempo(9_000_000)              # un caricamento
    assert s.risultato() == {"ora": None, "media": None, "low1": None}


def test_contatore_che_riparte():
    s = Statistiche()
    s.intervallo(0xFFFFFF00, 0x00000100, 64)   # GetTickCount ripassa per lo zero: 512 ms
    assert s.risultato()["ora"] == 125


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
    print("ok")
