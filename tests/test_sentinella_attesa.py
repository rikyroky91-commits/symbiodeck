"""Attesa degli eventi per la chat delle notifiche (2026-11-25): solo gli eventi nuovi, foto e clip complete, raffica
in un solo giro, testo pronto. Senza tablet: python tests/test_sentinella_attesa.py"""
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import sentinella_attesa as sa  # noqa: E402


def _evento(radice, nome, tipo, file=("foto.jpg", "clip.mp4"), ora=None):
    c = radice / "2026-11-25" / nome
    c.mkdir(parents=True, exist_ok=True)
    for f in file:
        (c / f).write_bytes(b"x" * 100)
    (c / "evento.json").write_text(json.dumps({"tipo": tipo, "ora": ora or time.time(), "ritardo": 1}), encoding="utf-8")
    return c


def test_solo_nuovi_e_raffica():
    radice = Path(tempfile.mkdtemp())
    try:
        _evento(radice, "10.00.00 movimento (aaaa)", "movimento")          # vecchio: non va mandato
        risultato = []
        t = threading.Thread(target=lambda: risultato.extend(sa.aspetta(radice, 0, passo=0.1, raffica=0.5, attesa_clip=5)))
        t.start()
        time.sleep(0.4)
        _evento(radice, "19.26.02 movimento (9ab0)", "movimento", ora=1795631162.0)
        _evento(radice, "19.26.03 rumore (bbbb)", "rumore")
        t.join(15)
        assert not t.is_alive()
        assert [r["tipo"] for r in risultato] == ["movimento", "rumore"]
        assert risultato[0]["foto"].endswith("foto.jpg") and risultato[0]["clip"].endswith("clip.mp4")
        assert risultato[0]["testo"].startswith("🛡 Movimento davanti al tablet alle ")
    finally:
        shutil.rmtree(radice, ignore_errors=True)


def test_ferma_senza_file():
    radice = Path(tempfile.mkdtemp())
    try:
        risultato = []
        t = threading.Thread(target=lambda: risultato.extend(sa.aspetta(radice, 0, passo=0.1, raffica=0.2, attesa_clip=5)))
        t.start()
        time.sleep(0.3)
        _evento(radice, "20.00.00 ferma (cccc)", "ferma", file=())
        t.join(10)
        assert risultato and risultato[0]["tipo"] == "ferma" and risultato[0]["foto"] is None
        assert "si è fermata" in risultato[0]["testo"]
    finally:
        shutil.rmtree(radice, ignore_errors=True)


def test_clip_che_cresce_si_aspetta():
    radice = Path(tempfile.mkdtemp())
    try:
        c = _evento(radice, "21.00.00 movimento (dddd)", "movimento", file=("foto.jpg",))
        inizio = time.time()

        def clip_dopo():
            time.sleep(1.0)
            (c / "clip.mp4").write_bytes(b"y" * 10)
        threading.Thread(target=clip_dopo).start()
        sa.completa(c, time.time() + 10)
        assert (c / "clip.mp4").is_file() and time.time() - inizio >= 3.5   # arrivata e ferma da 3 s
    finally:
        shutil.rmtree(radice, ignore_errors=True)


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
    print("ok")
