"""La bacchetta magica del visore (2026-11-27): il disegno diventa un ritaglio salvato; va a Claude solo con un
pulsante o con la frase dopo. Senza visore, rete né modelli: python tests/test_jarvis_bacchetta.py"""
import io
import queue
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import bacchetta, principale  # noqa: E402
from jarvis.cervello import Cervello  # noqa: E402
from jarvis.principale import Jarvis  # noqa: E402

principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"


def _jpeg(w=1000, h=500):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (30, 30, 30))
    ImageDraw.Draw(im).rectangle((400, 200, 600, 300), fill=(220, 30, 30))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=95)
    return out.getvalue()


def _apri(dati):
    from PIL import Image
    return Image.open(io.BytesIO(dati)).convert("RGB")


CERCHIO = "0.38,0.5;0.5,0.34;0.62,0.5;0.5,0.66;0.38,0.5"


def test_cerchio_sottolineatura_e_tocco():
    c = bacchetta.riquadro(bacchetta.leggi_punti(CERCHIO), 1000, 500, margine=0)
    assert c == (380, 170, 620, 330), c
    l, t, r, b = bacchetta.riquadro([(0.2, 0.8), (0.6, 0.81)], 1000, 500, margine=0)
    assert t < 280 and b >= 405, (t, b)          # la riga prende quello che sta sopra
    l, t, r, b = bacchetta.riquadro([(0.5, 0.5), (0.501, 0.502)], 1000, 500, margine=0)
    assert r - l > 150 and abs((l + r) / 2 - 500) < 3          # il tocco prende una zona attorno


def test_fuori_dall_immagine_e_punti_sbagliati():
    for cattivo in ("", "a,b;c,d", "0.5,0.5"):
        try:
            bacchetta.leggi_punti(cattivo)
            raise AssertionError(cattivo)
        except ValueError:
            pass
    try:
        bacchetta.riquadro([(1.5, 1.5), (1.9, 1.9)], 1000, 500)
        raise AssertionError("fuori")
    except ValueError:
        pass


def test_salva_pulito_e_manda_segnato():
    with tempfile.TemporaryDirectory() as d:
        b = bacchetta.Bacchetta(d)
        s, anteprima = b.nuovo("stanza", bacchetta.leggi_punti(CERCHIO), {}, _jpeg())
        pulito = _apri(Path(s["percorso"]).read_bytes())
        assert max(pulito.size) >= 480
        r, g, _ = pulito.getpixel((pulito.width // 2, pulito.height // 2))
        assert r > 150 and g < 90, "al centro c'è l'oggetto cerchiato"
        arancio = lambda im: sum(1 for p in im.resize((200, 150)).getdata() if p[0] > 150 and 90 < p[1] < 220 and p[2] < 90)  # noqa: E731
        assert arancio(_apri(s["jpeg"])) > arancio(pulito)
        assert _apri(anteprima).width <= bacchetta.LATO_ANTEPRIMA
        assert b.prendi("altro") is None
        assert b.prendi()["id"] == s["id"]
        assert b.prendi() is None, "si attacca una volta sola"
        s2, _ = b.nuovo("stanza", bacchetta.leggi_punti(CERCHIO), {}, _jpeg())
        assert s2["percorso"] != s["percorso"]
        s2["quando"] -= bacchetta.SCADE + 1
        assert b.prendi() is None, "scaduto"


def test_quale_schermo_mostra_moonlight():
    mon = [{}, {"left": 0, "top": 0, "width": 3840, "height": 2160},
           {"left": 3840, "top": 0, "width": 2560, "height": 1440}]
    assert bacchetta.monitor_dello_stream(mon, 2560, 1440) == 2
    assert bacchetta.monitor_dello_stream(mon, 1920, 1080) == 1
    assert bacchetta.monitor_dello_stream(mon, 1920, 1080, {(3840, 0)}) == 2


def _jarvis(cartella, emergenza=False):
    j = Jarvis.__new__(Jarvis)
    j.bacchetta = bacchetta.Bacchetta(cartella)
    j.richieste = queue.Queue()
    j.eventi = []
    j.evento = lambda **d: j.eventi.append(d)
    j.uscita = types.SimpleNamespace(riprendi=lambda: None)
    j.emergenza = types.SimpleNamespace(attiva=emergenza)
    j.chiesto = []
    j.claude = types.SimpleNamespace(chiedi=lambda *a: j.chiesto.append(a))
    return j


def test_il_pulsante_mette_in_coda_e_la_voce_si_porta_il_ritaglio():
    with tempfile.TemporaryDirectory() as d:
        j = _jarvis(d)
        r = j.ritaglio_bacchetta({"fonte": ["stanza"], "punti": [CERCHIO]}, _jpeg())
        assert r["anteprima"] and Path(r["percorso"]).exists()
        assert not j.chiesto and j.richieste.empty(), "disegnare da solo non manda niente"
        assert j.azione_bacchetta({"id": r["id"], "azione": "cerca"}) == {"ok": True}
        tipo, (testo, s) = j.richieste.get_nowait()
        assert tipo == "bacchetta" and "Cerca" in testo
        j._chiedi(testo, s)
        messaggio, immagine, percorso = j.chiesto[-1]
        assert r["percorso"] in messaggio and immagine.startswith(b"\xff\xd8") and percorso == r["percorso"]
        assert {"presa": r["id"]} in [e.get("bacchetta") for e in j.eventi]
        try:
            j.azione_bacchetta({"id": r["id"], "azione": "cerca"})
            raise AssertionError("due volte")
        except ValueError:
            pass
        # senza ritaglio la domanda va com'è
        j._chiedi("che ore sono")
        assert j.chiesto[-1] == ("che ore sono",)


def test_in_emergenza_l_immagine_non_parte():
    with tempfile.TemporaryDirectory() as d:
        j = _jarvis(d, emergenza=True)
        r = j.ritaglio_bacchetta({"fonte": ["stanza"], "punti": [CERCHIO]}, _jpeg())
        j._chiedi("cos'è questo", j.bacchetta.prendi())
        assert len(j.chiesto[-1]) == 1 and r["percorso"] in j.chiesto[-1][0]


def test_la_voce_riceve_l_immagine_e_l_esecutore_il_percorso():
    c = Cervello.__new__(Cervello)
    mandati, esecutore = [], []
    c.contesto = lambda: ""
    c.voce = types.SimpleNamespace(chiedi=lambda *a: mandati.append(a))
    c.chiedi("cos'è questo", b"\xff\xd8x", r"C:\Immagini\Bacchetta\a.jpg")
    assert mandati[-1][1] == b"\xff\xd8x"
    c.esec = types.SimpleNamespace(occupato=False, chiedi=esecutore.append)
    c.azioni, c.richiesta_lavoro = [], ""
    c._azione = c.log = lambda *a: None
    c._chiedi_esecutore = esecutore.append
    c._affida("cerca il prezzo")
    for _ in range(100):
        if esecutore:
            break
        __import__("time").sleep(0.01)
    assert r"C:\Immagini\Bacchetta\a.jpg" in esecutore[0]
    c.chiedi("che ore sono")
    assert len(mandati[-1]) == 1 and c._immagine == ""


if __name__ == "__main__":
    for nome, prova in list(globals().items()):
        if nome.startswith("test_"):
            prova()
            print("ok", nome)
