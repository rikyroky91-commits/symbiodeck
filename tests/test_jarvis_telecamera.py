"""Telecamere e visore (2026-11-24 sera): Jarvis guarda con la telecamera del visore o del tablet solo col permesso
dell'utente, ogni volta; con il visore addosso il microfono del tablet si spegne. Senza audio, rete né modelli:
python tests/test_jarvis_telecamera.py"""
import base64
import io
import sys
import tempfile
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import principale  # noqa: E402
from jarvis.audio import Tablet  # noqa: E402
from jarvis.principale import PREDEFINITE, Jarvis, _riduci_foto  # noqa: E402

# i test non scrivono nel registro vero di Jarvis (server/jarvis.log)
principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"


def _jpeg(w=2000, h=1500):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(out, "JPEG")
    return out.getvalue()


def _jarvis(permesso=True, visori=0, immersivo=False):
    j = Jarvis.__new__(Jarvis)
    j.imp = dict(PREDEFINITE, attivo=True, microfono="tablet")
    j.visori, j.visore_immersivo, j.foto_attese = visori, immersivo, {}
    j.eventi, j.conferme, j.comandi_tablet, j.microfono_voluto = [], [], [], []
    j.evento = lambda **d: j.eventi.append(d)

    def chiedi(descrizione, **k):
        j.conferme.append((descrizione, k))
        return permesso
    j.chiedi_conferma = chiedi
    j.tablet = types.SimpleNamespace(collegato=lambda: True, comando_una_volta=lambda **c: j.comandi_tablet.append(c),
                                     vuoi_microfono=j.microfono_voluto.append)
    return j


def _risponde(j, chiave, dati, errore=""):
    """Simula il dispositivo: appena arriva la richiesta di foto, la manda."""
    def lavora():
        for _ in range(200):
            ids = list(j.foto_attese)
            if ids:
                j.foto_arrivata(ids[0], dati, errore)
                return
            threading.Event().wait(0.01)
    threading.Thread(target=lavora, daemon=True).start()


def test_senza_permesso_niente_foto():
    j = _jarvis(permesso=False)
    r = j.guarda("riconoscere la scheda madre")
    assert not r["ok"] and "permesso" in r["messaggio"]
    assert not j.comandi_tablet and not any("telecamera" in e for e in j.eventi)
    descrizione, k = j.conferme[0]
    assert descrizione.startswith("usare la telecamera del tablet") and k.get("sempre_ok") is False


def test_foto_dal_tablet_ridotta_per_claude():
    j = _jarvis()
    _risponde(j, "tablet", _jpeg())
    r = j.guarda("riconoscere la scheda madre")
    assert r["ok"] and j.comandi_tablet and "foto" in j.comandi_tablet[0]
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(r["jpeg"])))
    assert max(im.size) == 1568 and im.format == "JPEG"          # lato lungo ridotto
    assert not j.foto_attese                                      # niente richieste rimaste aperte


def test_col_visore_addosso_usa_il_visore():
    j = _jarvis(visori=1, immersivo=True)
    _risponde(j, "visore", _jpeg(1280, 960))
    r = j.guarda("che pianta è")
    assert r["ok"] and not j.comandi_tablet
    assert any("telecamera" in e and e["telecamera"]["id"] for e in j.eventi)
    assert j.conferme[0][0].startswith("usare la telecamera del visore")


def test_errore_del_dispositivo_e_visore_assente():
    j = _jarvis(visori=1, immersivo=True)
    _risponde(j, "visore", b"", "NotAllowedError: permesso negato")
    r = j.guarda("etichetta")
    assert not r["ok"] and "NotAllowedError" in r["messaggio"]
    j = _jarvis(visori=0)
    r = j.guarda("etichetta", "visore")
    assert not r["ok"] and "non è collegato" in r["messaggio"] and not j.conferme   # non chiede se non può


def test_foto_con_id_sbagliato_rifiutata():
    j = _jarvis()
    assert not j.foto_arrivata("inventato", _jpeg())


def test_visore_addosso_spegne_il_microfono_del_tablet():
    j = _jarvis()
    j._aggiorna_microfono()
    j.imposta_visore(True)
    j.imposta_visore(True)                      # una volta sola
    j.imposta_visore(False)
    assert j.microfono_voluto == [True, False, True]
    assert [e["visore_immersivo"] for e in j.eventi if "visore_immersivo" in e] == [True, False]


def test_blocchi_del_tablet_ignorati_col_visore():
    j = _jarvis(immersivo=True)
    arrivati = []
    j.uscita = types.SimpleNamespace(aec=None)
    j.blocchi = types.SimpleNamespace(put_nowait=arrivati.append)
    j._blocco("tablet", b"x" * 2560)
    assert not arrivati
    j.visore_immersivo = False
    j._blocco("tablet", b"x" * 2560)
    assert arrivati


def test_comando_una_volta_non_si_ripete_al_ricollegamento():
    t = Tablet(lambda *a: None)
    t.comando(accendi_schermo=True)
    t.comando_una_volta(foto="abc")
    assert "foto" not in t.comandi and t.comandi["accendi_schermo"] is True


def test_riduci_foto_lascia_le_piccole():
    piccola = _jpeg(800, 600)
    assert _riduci_foto(piccola) is piccola


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
