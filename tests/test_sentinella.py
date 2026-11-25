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
from sentinella import Sentinella, correggi, descrivi, id_da, impostazioni, sottocartella  # noqa: E402

TMP = Path(__file__).resolve().parent / "_tmp_sentinella"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"


def _wav(secondi=0.5):
    pcm = b"\x00\x01" * int(16000 * secondi)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300


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
    imp = correggi({"sens_movimento": 42, "armamento": "-5", "rumore": 0, "sconosciuta": 1}, impostazioni({}))
    assert imp["sens_movimento"] == 10 and imp["armamento"] == 0 and imp["schermo"] == "attenuato"
    assert imp["rumore"] is False and "sconosciuta" not in imp
    # lo schermo non si spegne mai: "spento" non si può più scegliere e una config vecchia diventa "attenuato"
    assert impostazioni({"sentinella": {"schermo": "spento"}})["schermo"] == "attenuato"
    for sbagliata in ({"schermo": "acceso"}, {"schermo": "spento"}, {"pausa": "tanto"}):
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
    notifiche = []
    with mock.patch("urllib.request.urlopen", apri), \
            mock.patch.object(pannello, "notifica_windows", lambda *a: notifiche.append(a) or True):
        pannello.avvisa_sentinella({"token": "k"}, ev)
        pannello.avvisa_sentinella({"token": "k"}, dict(ev, ritardo=900))   # arrivato tardi: niente voce
        pannello.avvisa_sentinella({"token": "k", "jarvis": False}, ev)      # Jarvis spento: nessuna chiamata
    assert len(mandate) == 2
    url, corpo, _h = mandate[0]
    assert url.endswith("/jarvis/sentinella") and corpo["voce"] is True and corpo["foto"].endswith("foto.jpg")
    assert "davanti al tablet" in corpo["testo"] and mandate[1][1]["voce"] is False
    # la notifica di Windows c'è sempre (anche con Jarvis spento), con la foto e la cartella dell'evento
    assert len(notifiche) == 3 and notifiche[0][0].endswith("movimento") and notifiche[0][2].endswith("foto.jpg")
    assert "min dopo" in notifiche[1][1] and Path(notifiche[0][3]).is_dir()


def test_notifica_di_windows_senza_modello():
    import xml.dom.minidom
    import pannello
    x = pannello.xml_notifica("🛡 Sentinella: movimento", 'Un <movimento> & "prova" àè', r"C:\x y\foto.jpg", r"C:\x y")
    xml.dom.minidom.parseString(x.encode("utf-8"))            # XML valido anche con caratteri speciali
    assert "&lt;movimento&gt; &amp;" in x and 'src="file:///C:/x%20y/foto.jpg"' in x and 'launch="file:///C:/x%20y"' in x
    s = _sentinella({"sentinella": {"notifica_pc": False}})
    pannello.SENTINELLA = s
    viste = []
    with mock.patch.object(pannello, "notifica_windows", lambda *a: viste.append(a)), \
            mock.patch.object(pannello, "a_jarvis", lambda *a: None):
        pannello.avvisa_sentinella({"token": "k"}, {"id": _id(), "tipo": "movimento", "ora": time.time()})
        assert viste == []                                     # spenta nelle impostazioni
        s.cambia_impostazioni({"notifica_pc": True})
        pannello.avvisa_sentinella({"token": "k"}, {"id": _id("ferma"), "tipo": "ferma", "ora": time.time(), "valore": 200})
    assert viste[0][0] == "⚠ Sentinella ferma" and "si è fermata" in viste[0][1]


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


# ---------------------------------------------------------------- 1.12: Desktop, clip video, allarme di Jarvis
def test_cartella_sul_desktop_per_giorno_e_ora():
    assert str(sottocartella("20261125-082115-rumore-73b9")).replace("\\", "/") == "2026-11-25/08.21.15 rumore (73b9)"
    assert id_da("2026-11-25", "08.21.15 rumore (73b9)") == "20261125-082115-rumore-73b9"
    assert id_da("2026-11-25", "foto delle vacanze") is None and id_da("vacanze", "08.21.15 rumore (73b9)") is None
    s = _sentinella()
    id_ = "20261125-082115-movimento-0a0b"
    s.ricevi_file(id_, "foto.jpg", JPEG)
    assert (TMP / "dati" / "2026-11-25" / "08.21.15 movimento (0a0b)" / "foto.jpg").is_file()
    # cartelle e file dell'utente nella cartella Sentinella non danno fastidio
    (TMP / "dati" / "2026-11-25" / "appunti").mkdir()
    (TMP / "dati" / "leggimi.txt").write_text("x")
    assert [e["id"] for e in s.elenco()] == [id_]
    assert s.cancella(id_) and not (TMP / "dati" / "2026-11-25" / "08.21.15 movimento (0a0b)").exists()


def test_clip_video_mp4():
    s = _sentinella()
    id_ = _id()
    s.ricevi_file(id_, "foto.jpg", JPEG)
    s.ricevi_file(id_, "clip.mp4", MP4)
    ev = s.evento(id_)
    assert ev["video"] and ev["foto"] and not ev["clip"] and Path(ev["cartella"]).is_dir()
    for nome, dati in (("clip.mp4", JPEG), ("clip.avi", MP4), ("clip-01.mp4", MP4)):
        try:
            s.ricevi_file(id_, nome, dati)
        except ValueError:
            continue
        raise AssertionError(f"accettato: {nome}")


def test_eventi_vecchi_spostati_nella_cartella_nuova():
    shutil.rmtree(TMP, ignore_errors=True)
    vecchia = TMP / "sentinella-dati"
    id_ = "20261125-082115-rumore-73b9"
    (vecchia / id_).mkdir(parents=True)
    (vecchia / id_ / "evento.json").write_text(json.dumps({"id": id_, "tipo": "rumore", "ora": time.time(),
                                                           "ricevuto": time.time()}))
    (vecchia / id_ / "foto.jpg").write_bytes(JPEG)
    (vecchia / "visti.json").write_text(json.dumps({"fino": 0}))
    s = Sentinella(TMP / "Desktop" / "Sentinella", {}, log=lambda *a: None)
    assert s.migra(vecchia) == 1 and not vecchia.exists()
    assert s.elenco()[0]["foto"] and s.non_visti == 1
    assert s.migra(vecchia) == 0


def test_stato_passato_a_jarvis_quando_cambia():
    s = _sentinella()
    passati = []
    s.su_stato = passati.append
    s.stato_dal_tablet({"attiva": True, "armata": False})
    s.stato_dal_tablet({"attiva": True, "armata": False, "in_coda": 2})
    s.stato_dal_tablet({"attiva": True, "armata": True, "notte": True})
    s.stato_dal_tablet({"attiva": False})
    assert _aspetta(lambda: len(passati) == 3)
    assert [(p["attiva"], p.get("armata")) for p in passati] == [(True, False), (True, True), (False, None)]
    assert s.per_flusso()["tablet"]["attiva"] is False


def test_jarvis_diventa_un_allarme():
    j = _jarvis()
    j.stato_sentinella({"attiva": True, "armata": False})
    j.stato_sentinella({"attiva": True, "armata": False})
    j.stato_sentinella({"attiva": True, "armata": True})
    j.stato_sentinella({"attiva": False})
    assert [e["allarme"] for e in j.eventi if "allarme" in e] == [
        {"attiva": True, "armata": False}, {"attiva": True, "armata": True}, {"attiva": False, "armata": False}]


def test_clip_servita_a_pezzi_per_i_lettori_video():
    srv, base = _server()
    try:
        id_ = _id()
        _chiama(base, f"/api/sentinella/file?id={id_}&nome=foto.jpg", grezzo=JPEG)
        _chiama(base, f"/api/sentinella/file?id={id_}&nome=clip.mp4", grezzo=MP4)
        url = base + f"/api/sentinella/file?id={id_}&nome=clip.mp4&t=tablet-prova"
        for intervallo, atteso in (("bytes=0-9", MP4[:10]), ("bytes=10-", MP4[10:]), ("bytes=-8", MP4[-8:])):
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": intervallo}), timeout=5) as r:
                assert r.status == 206 and r.read() == atteso and r.headers["Content-Type"] == "video/mp4"
                assert r.headers["Content-Range"].endswith(f"/{len(MP4)}")
        with urllib.request.urlopen(url, timeout=5) as r:
            assert r.status == 200 and r.read() == MP4 and r.headers["Accept-Ranges"] == "bytes"
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=999999-"}), timeout=5)
            raise AssertionError("intervallo fuori dal file accettato")
        except urllib.error.HTTPError as e:
            assert e.code == 416
    finally:
        srv.shutdown()
        srv.server_close()


def test_sentinella_ferma_quando_il_tablet_tace():
    avvisi = []
    s = _sentinella(avvisi=avvisi)
    assert s.controlla_silenzio() is None                       # tablet mai sentito
    s.stato_dal_tablet({"attiva": True, "armata": True})
    assert s.controlla_silenzio(time.time() + 60) is None        # 1 minuto: normale (manda lo stato ogni 30 s)
    id_ = s.controlla_silenzio(time.time() + modulo.SILENZIO_FERMA + 5)
    assert id_ and "-ferma-" in id_
    assert s.controlla_silenzio(time.time() + 1000) is None      # una volta sola
    ev = s.evento(id_)
    assert ev["tipo"] == "ferma" and "non manda più lo stato" in ev["nota"]
    assert (TMP / "dati" / sottocartella(id_) / "evento.json").is_file()
    assert _aspetta(lambda: len(avvisi) == 1) and "si è fermata" in descrivi(avvisi[0])
    # si rifà sentire: di nuovo in guardia
    s.stato_dal_tablet({"attiva": True, "armata": True})
    assert s.controlla_silenzio(time.time() + modulo.SILENZIO_FERMA + 5)


def test_spenta_di_proposito_non_e_ferma():
    s = _sentinella()
    s.stato_dal_tablet({"attiva": False, "da": "tablet: pressione lunga"})
    assert s.controlla_silenzio(time.time() + 3600) is None
    assert s.stato_tablet() is None or s.tablet["da"] == "tablet: pressione lunga"


def test_pannello_passa_lo_stato_a_jarvis():
    import pannello
    mandate = []
    with mock.patch.object(pannello, "a_jarvis", lambda cfg, percorso, corpo: mandate.append((percorso, corpo))):
        pannello.stato_sentinella_a_jarvis({"token": "k"}, {"attiva": True, "armata": True, "notte": True})
        pannello.stato_sentinella_a_jarvis({"token": "k", "jarvis": False}, {"attiva": True})
    assert mandate == [("/jarvis/sentinella/stato", {"attiva": True, "armata": True})]


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("test_"):
                f()
                print("ok", nome)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
