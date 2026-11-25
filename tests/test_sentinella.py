"""Modalità sentinella (2026-11-25): archivio degli eventi sul PC, comandi e impostazioni verso il tablet nel flusso,
indirizzi HTTP del pannello, avvisi e comandi a voce di Jarvis. Senza tablet, rete esterna né modelli:
python tests/test_sentinella.py"""
import io
import json
import shutil
import struct
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import sentinella as modulo  # noqa: E402
from sentinella import Sentinella, correggi, descrivi, impostazioni  # noqa: E402

TMP = Path(__file__).resolve().parent / "_tmp_sentinella"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"


def _wav(secondi=0.5):
    pcm = b"\x00\x01" * int(16000 * secondi)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _id(tipo="movimento", coda="ab12", quando=None):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(quando or time.time())) + f"-{tipo}-{coda}"


def _sentinella(cfg=None, avvisi=None):
    shutil.rmtree(TMP, ignore_errors=True)
    salvate = []
    s = Sentinella(TMP / "dati", cfg if cfg is not None else {}, salva_config=lambda c: salvate.append(json.dumps(c)),
                   log=lambda *a: None, avvisa=(avvisi.append if avvisi is not None else None))
    s.salvate = salvate
    return s


def _aspetta(cond, secondi=2):
    fine = time.time() + secondi
    while time.time() < fine:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- impostazioni
def test_impostazioni_predefinite_e_limiti():
    assert impostazioni({}) == modulo.PREDEFINITE
    imp = correggi({"sens_movimento": 42, "armamento": "-5", "schermo": "spento", "rumore": 0, "sconosciuta": 1},
                   impostazioni({}))
    assert imp["sens_movimento"] == 10 and imp["armamento"] == 0 and imp["schermo"] == "spento"
    assert imp["rumore"] is False and "sconosciuta" not in imp
    for sbagliata in ({"schermo": "acceso"}, {"pausa": "tanto"}):
        try:
            correggi(sbagliata, impostazioni({}))
        except ValueError:
            continue
        raise AssertionError(f"accettata: {sbagliata}")


def test_impostazioni_salvate_nella_config():
    cfg = {"token": "x"}
    s = _sentinella(cfg)
    s.cambia_impostazioni({"sens_rumore": 8})
    assert cfg["sentinella"]["sens_rumore"] == 8 and s.salvate
    assert s.per_flusso()["imp"]["sens_rumore"] == 8


# ---------------------------------------------------------------- comandi verso il tablet
def test_comando_nel_flusso_con_numero_che_cresce_e_scade():
    s = _sentinella()
    assert "comando" not in s.per_flusso()
    a = s.comanda(True, "jarvis")
    b = s.comanda(False)
    c = s.per_flusso()["comando"]
    assert a["id"] != b["id"] and c == {"id": b["id"], "attiva": False}
    s.comando["t"] -= modulo.COMANDO_VALIDO + 1   # vecchio: un tablet appena riavviato non lo deve applicare
    assert "comando" not in s.per_flusso()


def test_stato_del_tablet_e_quando_diventa_vecchio():
    s = _sentinella()
    cambi = []
    s.cambiato = lambda: cambi.append(1)
    s.stato_dal_tablet({"attiva": True, "armata": False, "rumore": True, "inventato": 1}, "192.168.1.192")
    assert s.per_flusso()["tablet"]["attiva"] is True and "inventato" not in s.stato_tablet()
    s.stato_dal_tablet({"attiva": True, "armata": False, "rumore": True})
    assert len(cambi) == 1                        # lo stesso stato non riempie il flusso
    s.stato_dal_tablet({"attiva": True, "armata": True, "rumore": False, "perche_rumore": "visore"})
    assert len(cambi) == 2 and s.per_flusso()["tablet"]["perche_rumore"] == "visore"
    s.tablet["ricevuto"] -= modulo.STATO_VECCHIO + 1
    assert s.stato_tablet() is None and "tablet" not in s.per_flusso()


# ---------------------------------------------------------------- eventi
def test_evento_con_foto_clip_e_audio_annunciato_una_volta():
    avvisi = []
    s = _sentinella(avvisi=avvisi)
    id_ = _id()
    assert s.ricevi_file(id_, "foto.jpg", JPEG, valore=0.12, ora=time.time() * 1000)["nuovo"]
    assert not s.ricevi_file(id_, "foto.jpg", JPEG)["nuovo"]          # ripetuto dal tablet: niente secondo avviso
    for i in range(1, 4):
        s.ricevi_file(id_, f"clip-{i:02d}.jpg", JPEG)
    s.ricevi_file(id_, "audio.wav", _wav())
    assert _aspetta(lambda: len(avvisi) == 1)
    time.sleep(0.05)
    assert len(avvisi) == 1 and avvisi[0]["tipo"] == "movimento" and avvisi[0]["valore"] == 0.12
    ev = s.elenco()[0]
    assert ev["id"] == id_ and ev["foto"] and ev["audio"] and ev["clip"] == ["clip-01.jpg", "clip-02.jpg", "clip-03.jpg"]
    assert s.non_visti == 1 and s.per_flusso()["ultimo"]["id"] == id_
    assert s.file(id_, "audio.wav").read_bytes()[:4] == b"RIFF"


def test_evento_senza_foto_solo_rumore():
    s = _sentinella()
    id_ = _id("rumore")
    s.ricevi_file(id_, "evento", b"", valore=-21.5)
    ev = s.evento(id_)
    assert ev["tipo"] == "rumore" and not ev["foto"] and not ev["clip"]


def test_dati_non_validi_rifiutati():
    s = _sentinella()
    casi = [("../../config", "foto.jpg", JPEG), (_id(), "..\\config.json", JPEG), (_id(), "foto.jpg", b"GIF89a.."),
            (_id(), "audio.wav", JPEG), (_id(), "clip-1.jpg", JPEG), (_id(), "foto.png", JPEG),
            (_id("intruso"), "foto.jpg", JPEG), (_id(), "foto.jpg", b"\xff\xd8\xff" + b"0" * (4 * 2**20))]
    for id_, nome, dati in casi:
        try:
            s.ricevi_file(id_, nome, dati)
        except ValueError:
            continue
        raise AssertionError(f"accettato: {id_} {nome}")
    assert s.file("../x", "foto.jpg") is None and s.file(_id(), "evento.json") is None
    assert not (TMP / "dati").exists() or not any((TMP / "dati").iterdir())


def test_orologio_del_tablet_sbagliato_vale_l_ora_del_pc():
    s = _sentinella()
    id_ = _id()
    s.ricevi_file(id_, "foto.jpg", JPEG, ora=0)     # 1970: tablet senza ora
    assert abs(s.evento(id_)["ora"] - time.time()) < 5


def test_evento_arrivato_tardi_si_segna():
    s = _sentinella()
    prima = time.time() - 600
    id_ = _id(quando=prima)
    s.ricevi_file(id_, "foto.jpg", JPEG, ora=prima * 1000)
    assert 590 < s.evento(id_)["ritardo"] < 610


def test_visti_ricordati_dopo_un_riavvio_del_server():
    s = _sentinella()
    s.ricevi_file(_id(coda="0001"), "foto.jpg", JPEG)
    s2 = Sentinella(TMP / "dati", {}, log=lambda *a: None)
    assert s2.non_visti == 1
    s2.segna_visti()
    assert Sentinella(TMP / "dati", {}, log=lambda *a: None).non_visti == 0


def test_pulizia_e_cancella():
    s = _sentinella({"sentinella": {"conserva_giorni": 2}})
    vecchio, nuovo = _id(coda="0001", quando=time.time() - 3 * 86400), _id(coda="0002")
    s.ricevi_file(vecchio, "foto.jpg", JPEG, ora=(time.time() - 3 * 86400) * 1000)
    s.ricevi_file(nuovo, "foto.jpg", JPEG)
    assert s.pulisci() == 1 and [e["id"] for e in s.elenco()] == [nuovo]
    assert s.cancella(nuovo) and s.elenco() == []


def test_descrizione_e_riassunto():
    s = _sentinella()
    ora = time.mktime((2026, 11, 25, 14, 32, 0, 0, 0, -1))
    assert descrivi({"tipo": "movimento", "ora": ora}) == "un movimento davanti al tablet alle 14:32"
    assert descrivi({"tipo": "rumore", "ora": ora}) == "un rumore vicino al tablet alle 14:32"
    s.ricevi_file(_id("rumore"), "evento", b"")
    r = s.riassunto()
    assert r["oggi"] == 1 and r["ultimo"]["tipo"] == "rumore" and r["tablet"] is None


# ---------------------------------------------------------------- indirizzi HTTP del pannello
def _server():
    import pannello
    pannello.REGISTRO = Path(tempfile.gettempdir()) / "pannello-prove.log"
    cfg = {"token": "tablet-prova", "token_pc": "pc-prova", "app": [], "scorciatoie": [], "abbinato": True}
    s = _sentinella(cfg)
    camp = pannello.Campionatore()
    camp.sentinella = s
    s.cambiato = camp.sentinella_cambiata
    srv = pannello.ThreadingHTTPServer(("127.0.0.1", 0), pannello.Gestore)
    srv.daemon_threads = True
    srv.cfg, srv.campionatore, srv.sentinella, srv.tablet, srv.jarvis = cfg, camp, s, {}, False
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _chiama(base, percorso, corpo=None, grezzo=None, token="tablet-prova"):
    dati = grezzo if grezzo is not None else (json.dumps(corpo).encode() if corpo is not None else None)
    r = urllib.request.Request(base + percorso, data=dati, method="POST" if dati is not None else "GET",
                               headers={"X-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as risposta:
            corpo = risposta.read()
            return risposta.status, (json.loads(corpo) if risposta.headers.get("Content-Type") == "application/json"
                                     else corpo)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http_dal_tablet_al_pc_e_ritorno():
    srv, base = _server()
    try:
        id_ = _id()
        assert _chiama(base, f"/api/sentinella/file?id={id_}&nome=foto.jpg&valore=0.08&ora={int(time.time() * 1000)}",
                       grezzo=JPEG)[0] == 200
        assert _chiama(base, f"/api/sentinella/file?id={id_}&nome=audio.wav", grezzo=_wav())[0] == 200
        assert _chiama(base, f"/api/sentinella/file?id={id_}&nome=../x.jpg", grezzo=JPEG)[0] == 400
        assert _chiama(base, f"/api/sentinella/file?id={id_}&nome=foto.jpg", grezzo=JPEG, token="sbagliato")[0] == 401
        codice, d = _chiama(base, "/api/sentinella")
        assert codice == 200 and d["eventi"][0]["id"] == id_ and d["eventi"][0]["audio"] and d["non_visti"] == 1
        codice, foto = _chiama(base, f"/api/sentinella/file?id={id_}&nome=foto.jpg")
        assert codice == 200 and foto == JPEG
        assert _chiama(base, f"/api/sentinella/file?id={id_}&nome=evento.json")[0] == 404
        # stato del tablet, comando e impostazioni finiscono nel flusso del pannello (che il tablet legge)
        assert _chiama(base, "/api/sentinella/stato", {"attiva": True, "armata": True, "rumore": True})[0] == 200
        codice, c = _chiama(base, "/api/sentinella/comando", {"attiva": False, "da": "prova"})
        assert codice == 200 and c["comando"]["attiva"] is False and c["tablet_collegato"] is False
        assert _chiama(base, "/api/sentinella/impostazioni", {"sens_movimento": 9})[0] == 200
        assert _chiama(base, "/api/sentinella/impostazioni", {"schermo": "boh"})[0] == 400
        flusso = srv.campionatore.ultima["sentinella"]
        assert flusso["comando"]["attiva"] is False and flusso["imp"]["sens_movimento"] == 9
        assert flusso["tablet"]["armata"] is True and flusso["ultimo"]["id"] == id_
        assert _chiama(base, "/api/sentinella/visti", {})[0] == 200 and srv.sentinella.non_visti == 0
        assert _chiama(base, "/api/sentinella/cancella", {"id": id_})[1]["ok"] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_avviso_a_jarvis_dal_pannello():
    import pannello
    s = _sentinella()
    pannello.SENTINELLA = s
    mandate = []

    class Risposta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def apri(r, timeout=0):
        mandate.append((r.full_url, json.loads(r.data), r.headers))
        return Risposta(b"{}")
    id_ = _id()
    s.ricevi_file(id_, "foto.jpg", JPEG)
    ev = s.evento(id_)
    with mock.patch.object(pannello, "SENTINELLA_DATI", TMP / "dati"), mock.patch("urllib.request.urlopen", apri):
        pannello.avvisa_sentinella({"token": "k"}, ev)
        pannello.avvisa_sentinella({"token": "k"}, dict(ev, ritardo=900))   # arrivato tardi: niente voce
        pannello.avvisa_sentinella({"token": "k", "jarvis": False}, ev)      # Jarvis spento: nessuna chiamata
    assert len(mandate) == 2
    url, corpo, _h = mandate[0]
    assert url.endswith("/jarvis/sentinella") and corpo["voce"] is True and corpo["foto"].endswith("foto.jpg")
    assert "davanti al tablet" in corpo["testo"] and mandate[1][1]["voce"] is False


# ---------------------------------------------------------------- Jarvis
def _jarvis():
    from jarvis import principale
    from jarvis.principale import PREDEFINITE, Jarvis
    principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"
    j = Jarvis.__new__(Jarvis)
    j.imp = dict(PREDEFINITE, attivo=True)
    j.eventi, j.detti, j.visore_immersivo, j.visori = [], [], False, 0
    j.evento = lambda **d: j.eventi.append(d)
    j.di = j.di_subito = j.detti.append
    j._torna_libero = lambda: None
    j.mostrati = []
    j.mostra_nel_visore = j.mostrati.append
    return j


def test_jarvis_frasi_della_sentinella():
    from jarvis import principale as p
    si = ["attiva la sentinella", "attivami la modalità sentinella", "modalità sentinella",
          "accendi la sentinella per favore", "metti la sentinella"]
    no = ["spegni la sentinella", "disattiva la modalità sentinella", "disarma la sentinella grazie"]
    stato = ["com'è la sentinella", "la sentinella ha visto qualcosa", "cosa ha visto la sentinella oggi",
             "è attiva la sentinella"]
    altro = ["apri spotify", "fammi vedere le foto della sentinella nel visore", "attiva la modalità privata"]
    for t in si:
        assert p._SENTINELLA_SI.match(t) and not p._SENTINELLA_NO.match(t), t
    for t in no:
        assert p._SENTINELLA_NO.match(t), t
    for t in stato:
        assert p._SENTINELLA_STATO.match(t) and not p._SENTINELLA_SI.match(t), t
    for t in altro:
        assert not (p._SENTINELLA_SI.match(t) or p._SENTINELLA_NO.match(t) or p._SENTINELLA_STATO.match(t)), t


def test_jarvis_comanda_la_sentinella_attraverso_il_pannello():
    from jarvis import principale as p
    j = _jarvis()
    chiamate = []

    def api(percorso, corpo):
        chiamate.append((percorso, corpo))
        return {"ok": True, "tablet_collegato": True}
    with mock.patch.object(p, "api_pannello", api), \
            mock.patch.object(p, "stato_sentinella", lambda: {"imp": {"armamento": 30}}):
        j.comanda_sentinella(True)
        j.visore_immersivo = True
        j.comanda_sentinella(True)
        j.comanda_sentinella(False)
    assert chiamate[0] == ("/api/sentinella/comando", {"attiva": True, "da": "jarvis"})
    assert "fra 30 secondi" in j.detti[0] and "visore" not in j.detti[0]
    assert "solo con la telecamera" in j.detti[1] and j.detti[2] == "Sentinella spenta."
    with mock.patch.object(p, "api_pannello", lambda *a: {"ok": True, "tablet_collegato": False}):
        j.comanda_sentinella(True)
    with mock.patch.object(p, "api_pannello", lambda *a: None):
        j.comanda_sentinella(True)
    assert "non è collegato" in j.detti[3] and "non risponde" in j.detti[4]


def test_jarvis_racconta_la_sentinella():
    from jarvis import principale as p
    j = _jarvis()
    ora = time.mktime((2026, 11, 25, 9, 5, 0, 0, 0, -1))
    r = {"tablet": {"attiva": True, "armata": True, "rumore": False}, "oggi": 2,
         "ultimo": {"tipo": "movimento", "ora": ora}}
    with mock.patch.object(p, "api_pannello", lambda *a: r):
        j.racconta_sentinella()
    assert j.detti[0] == ("La sentinella sta sorvegliando, solo con la telecamera. Oggi ha registrato 2 eventi, "
                          "l'ultimo un movimento alle 09:05.")


def test_jarvis_avvisa_a_voce_al_massimo_ogni_45_secondi_e_mostra_la_foto_nel_visore():
    j = _jarvis()
    d = {"id": _id(), "tipo": "movimento", "testo": "un movimento davanti al tablet alle 14:32", "voce": True,
         "foto": "C:/x/foto.jpg"}
    j.sentinella_evento(d)
    j.sentinella_evento(d)
    assert len(j.detti) == 1 and "Attenzione" in j.detti[0]
    assert [e["avviso"] for e in j.eventi].count("🛡 Sentinella: un movimento davanti al tablet alle 14:32") == 2
    assert j.mostrati == []                                  # visore non indossato: niente foto davanti agli occhi
    j.visore_immersivo = True
    j._voce_sentinella -= 60
    j.sentinella_evento(dict(d, voce=False))                 # arrivato tardi: solo l'avviso
    assert len(j.detti) == 1 and j.mostrati == [{"cosa": "file", "percorso": "C:/x/foto.jpg"}]


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("test_"):
                f()
                print("ok", nome)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
