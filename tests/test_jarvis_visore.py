"""Prove del PC dentro il visore (2026-11-24): cartelle, cartelle a parole, anteprime, file a pezzi, permessi,
richieste della voce. Le parti di Windows (finestre, schermo) si provano sul PC vero.
Senza rete: python -m pytest tests/test_jarvis_visore.py"""
import io
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import principale, visore  # noqa: E402


@pytest.fixture
def casa(monkeypatch):
    """Una cartella utente finta con Desktop, Pictures e qualche foto. Su Windows non nella cartella temporanea:
    sta in AppData, che il visore non mostra mai."""
    base = None
    if sys.platform == "win32":
        base = Path(__file__).resolve().parent / "_tmp_visore"
        base.mkdir(exist_ok=True)
    h = Path(tempfile.mkdtemp(dir=base))
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))       # Path.home() su Windows
    monkeypatch.setattr(visore, "WINDOWS", False)    # niente cartelle note vere (SHGetKnownFolderPath) né dischi
    monkeypatch.setattr(visore, "_cache_note", {})
    from PIL import Image
    foto = h / "Desktop" / "Foto"
    (foto / "Vacanze 2024").mkdir(parents=True)
    (h / "Pictures" / "Screenshots").mkdir(parents=True)
    (h / "Documents" / "Lavoro").mkdir(parents=True)
    (h / ".ssh").mkdir()
    (h / ".ssh" / "id_rsa").write_text("segreto")
    for i in range(3):
        Image.new("RGB", (1200, 800), (40 * i, 120, 200)).save(foto / f"IMG_{i}.jpg")
    (foto / "note.txt").write_text("ciao " * 100000)
    (foto / "video.mp4").write_bytes(bytes(range(256)) * 400)
    (foto / ".nascosto.jpg").write_bytes(b"x")
    (foto / "virus.exe").write_bytes(b"MZ")
    yield h
    shutil.rmtree(h, ignore_errors=True)


def test_elenco_e_permessi(casa):
    d = visore.elenco(str(casa / "Desktop" / "Foto"))
    nomi = [v["nome"] for v in d["voci"]]
    assert nomi[0] == "Vacanze 2024"                                 # prima le cartelle
    assert {"IMG_0.jpg", "note.txt", "video.mp4", "virus.exe"} <= set(nomi) and ".nascosto.jpg" not in nomi
    assert {v["nome"]: v["tipo"] for v in d["voci"]}["IMG_1.jpg"] == "immagine"
    assert d["su"] == str(casa / "Desktop")
    assert not visore.consentito(casa / ".ssh" / "id_rsa")
    assert not visore.consentito("/etc/passwd")
    with pytest.raises(PermissionError):
        visore.elenco("/etc")
    radici = {r["nome"]: r["percorso"] for r in visore.elenco("")["voci"]}
    assert radici["Desktop"] == str(casa / "Desktop") and "Cartella utente" in radici


def test_cartella_a_parole(casa):
    assert visore.risolvi_cartella("foto", "desktop") == str(casa / "Desktop" / "Foto")
    assert visore.risolvi_cartella("la cartella delle foto", "sul desktop") == str(casa / "Desktop" / "Foto")
    assert visore.risolvi_cartella("vacanze 2024") == str(casa / "Desktop" / "Foto" / "Vacanze 2024")
    assert visore.risolvi_cartella("screenshot") == str(casa / "Pictures" / "Screenshots")
    assert visore.risolvi_cartella("lavoro", "documenti") == str(casa / "Documents" / "Lavoro")
    assert visore.risolvi_cartella("immagini") == str(casa / "Pictures")          # la cartella di Windows
    assert visore.risolvi_cartella("", "desktop") == str(casa / "Desktop")
    assert visore.risolvi_cartella("ricette di nonna") is None
    assert visore.risolvi_cartella(str(casa / ".ssh")) is None                   # percorsi vietati: mai


def test_anteprime_ridotte_e_in_cache(casa):
    from PIL import Image
    f = casa / "Desktop" / "Foto" / "IMG_1.jpg"
    dati = visore.anteprima(str(f), 256)
    im = Image.open(io.BytesIO(dati))
    assert im.format == "JPEG" and max(im.size) == 256
    assert visore.anteprima(str(f), 256) is dati                                 # dalla cache
    with pytest.raises(PermissionError):
        visore.anteprima(str(casa / "Desktop" / "Foto" / "note.txt"))


def test_apri_sul_pc_mai_programmi(casa):
    with pytest.raises(PermissionError):
        visore.apri_sul_pc(str(casa / "Desktop" / "Foto" / "virus.exe"))
    with mock.patch.object(visore.os, "startfile", create=True) as apri:
        visore.apri_sul_pc(str(casa / "Desktop" / "Foto" / "IMG_0.jpg"))
        apri.assert_called_once()


def _gestore(headers):
    classe = next(c for c in vars(principale).values() if isinstance(c, type) and hasattr(c, "manda_file"))
    h = classe.__new__(classe)
    h.headers = headers
    h.uscita = io.BytesIO()
    h.stato = []
    h.send_response = lambda c: h.stato.append(c)
    h.intestazioni = {}
    h.send_header = lambda k, v: h.intestazioni.__setitem__(k, v)
    h.end_headers = lambda: None
    h._cors = lambda: None
    h.wfile = h.uscita
    return classe, h


def test_file_a_pezzi_per_i_video(casa):
    video = casa / "Desktop" / "Foto" / "video.mp4"
    classe, h = _gestore({"Range": "bytes=100-199"})
    classe.manda_file(h, str(video))
    assert h.stato == [206] and h.intestazioni["Content-Range"] == f"bytes 100-199/{video.stat().st_size}"
    assert h.uscita.getvalue() == video.read_bytes()[100:200] and h.intestazioni["Content-Type"] == "video/mp4"
    classe, h = _gestore({})
    classe.manda_file(h, str(casa / "Desktop" / "Foto" / "note.txt"))
    assert h.stato == [200] and len(h.uscita.getvalue()) == 400 * 1024        # testi lunghi: solo l'inizio
    with pytest.raises(PermissionError):
        classe.manda_file(h, str(casa / ".ssh" / "id_rsa"))


def test_voce_apre_nel_visore(casa):
    j = types.SimpleNamespace(visori=0, eventi=[])
    j.evento = lambda **d: j.eventi.append(d)
    m = lambda d: principale.Jarvis.mostra_nel_visore(j, d)  # noqa: E731
    assert not m({"cosa": "cartella", "nome": "foto", "dove": "desktop"})["ok"]   # visore spento
    j.visori = 1
    r = m({"cosa": "cartella", "nome": "foto", "dove": "desktop"})
    assert r["ok"] and "7 elementi" in r["messaggio"]
    assert j.eventi[-1] == {"visore": {"cosa": "cartella", "percorso": str(casa / "Desktop" / "Foto")}}
    assert not m({"cosa": "cartella", "nome": "ricette di nonna"})["ok"]
    r = m({"cosa": "file", "percorso": str(casa / "Desktop" / "Foto" / "IMG_2.jpg")})
    assert r["ok"] and j.eventi[-1]["visore"]["tipo"] == "immagine"
    assert not m({"cosa": "file", "percorso": str(casa / ".ssh" / "id_rsa")})["ok"]
    assert m({"cosa": "chiudi"})["ok"] and j.eventi[-1]["visore"]["cosa"] == "chiudi"


def test_strumenti_mcp_del_visore():
    from jarvis import visore_mcp
    mandati = []
    with mock.patch.object(visore_mcp, "_visore", lambda corpo: mandati.append(corpo) or "ok"):
        visore_mcp.visore_cartella("foto", "desktop")
        visore_mcp.visore_finestra("Chrome")
        visore_mcp.visore_schermo()
        visore_mcp.visore_chiudi()
    assert mandati == [{"cosa": "cartella", "nome": "foto", "dove": "desktop"}, {"cosa": "finestra", "titolo": "Chrome"},
                       {"cosa": "schermo", "numero": 1}, {"cosa": "chiudi"}]
