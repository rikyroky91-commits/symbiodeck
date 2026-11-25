"""Prove di dettatura, annulla, briefing, domande dalle altre sessioni di Claude Code e voce dal visore (2026-11-24).
Senza audio né rete: python -m pytest tests/test_jarvis_extra.py"""
import queue
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis import annulla, principale, tastiera  # noqa: E402
from jarvis.guardia import copia_per_annullare  # noqa: E402
from jarvis.principale import Jarvis  # noqa: E402
from jarvis.promemoria import Promemoria  # noqa: E402

# i test non scrivono nel registro vero di Jarvis (server/jarvis.log)
principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"

if not hasattr(tastiera, "PREMUTI"):
    # su Windows la tastiera premerebbe davvero i tasti nella finestra in primo piano: nei test si registra soltanto
    tastiera.PREMUTI = []
    tastiera._invia = lambda vk=0, scan=0, flag=0: tastiera.PREMUTI.append((vk, scan, flag))


def _tmp():
    return Path(tempfile.mkdtemp())


def _jarvis(**imp):
    j = types.SimpleNamespace(imp=dict(principale.PREDEFINITE, **imp), detti=[], eventi=[], stato="riposo",
                              in_gioco=False, conferma=None, da_dire=queue.Queue(), dettatura=False,
                              conversazione=False, richieste=queue.Queue(), blocco_domande=threading.Lock())
    j.di = j.di_subito = lambda t: j.detti.append(t)
    j.evento = lambda **d: j.eventi.append(d)
    j.imposta_stato = lambda s: setattr(j, "stato", s)
    j.claude = types.SimpleNamespace(occupato=False, lavora_in_sottofondo=lambda: False, locale=False)
    j.promemoria = Promemoria(_tmp() / "p.json", log=lambda *a: None)
    j.cambia = lambda d: j.imp.update(d)
    j._torna_libero = lambda: None
    j.online_permesso = lambda: Jarvis.online_permesso(j)
    j._detta = lambda o, t: Jarvis._detta(j, o, t)
    j.uscita = types.SimpleNamespace(riprendi=lambda: None, parla=False)
    j._ascolta_conferma_dopo_domanda = lambda c: None
    j._risposta_conferma = lambda *a, **k: Jarvis._risposta_conferma(j, *a, **k)
    j._fine_conferma = lambda: None
    j.meteo = {"t": 0, "testo": ""}
    j.leggi_meteo = lambda: Jarvis.leggi_meteo(j)
    return j


# ------------------------------------------------------------ dettatura
def test_dettatura_scrive_senza_modelli():
    j = _jarvis()
    loc = lambda t: Jarvis._comando_locale(j, t)  # noqa: E731
    tastiera.PREMUTI.clear()
    assert loc("Jarvis, dettatura") and j.dettatura and "Detta pure" in j.detti[-1]
    assert loc("Caro Marco, ti scrivo per la riunione.")          # va nella finestra, non a Claude
    scritti = "".join(chr(s) for vk, s, f in tastiera.PREMUTI if f == 0x0004)
    assert scritti == "Caro Marco, ti scrivo per la riunione. "
    tastiera.PREMUTI.clear()
    assert loc("a capo") and tastiera.PREMUTI[0][0] == tastiera.INVIO
    tastiera.PREMUTI.clear()
    assert loc("cancella l'ultima parola") and [p[0] for p in tastiera.PREMUTI[:2]] == [tastiera.CTRL, tastiera.BACKSPACE]
    assert loc("fine dettatura") and not j.dettatura and j.detti[-1] == "Fine dettatura."
    assert not loc("apri spotify")                                  # fuori dalla dettatura: torna a Claude
    loc("detta")
    assert loc("stop") and not j.dettatura


# ------------------------------------------------------------ annulla le modifiche ai file
def test_annulla_rimette_com_era():
    d = _tmp()
    cartella = d / "annulla"
    f = d / "config.ini"
    f.write_text("versione buona", encoding="utf-8")
    annulla.registra(cartella, f, "s1")
    f.write_text("modifica 1", encoding="utf-8")
    annulla.registra(cartella, f, "s1")
    f.write_text("modifica 2", encoding="utf-8")
    nuovo = d / "nuovo.txt"
    annulla.registra(cartella, nuovo, "s1")
    nuovo.write_text("creato da Jarvis", encoding="utf-8")
    gruppo = annulla.ultimo_gruppo(cartella)
    assert len(gruppo) == 3
    rimessi, tolti, falliti = annulla.ripristina(cartella, gruppo)
    assert f.read_text(encoding="utf-8") == "versione buona"          # com'era prima di tutto il lavoro
    assert not nuovo.exists() and any((cartella / "tolti").iterdir())  # spostato, non cancellato
    assert rimessi == ["config.ini"] and tolti == ["nuovo.txt"] and not falliti
    assert annulla.ultimo_gruppo(cartella) == []                      # già annullato


def test_annulla_separa_i_lavori():
    d = _tmp()
    cartella = d / "annulla"
    a, b = d / "a.txt", d / "b.txt"
    a.write_text("a", encoding="utf-8"); b.write_text("b", encoding="utf-8")
    annulla.registra(cartella, a, "vecchia")
    annulla.registra(cartella, b, "nuova")
    assert [Path(v["percorso"]).name for v in annulla.ultimo_gruppo(cartella)] == ["b.txt"]


def test_guardia_copia_prima_di_scrivere(monkeypatch):
    d = _tmp()
    (d / "jarvis-dati").mkdir()
    f = d / "x.txt"
    f.write_text("prima", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(d / "jarvis.json"))
    copia_per_annullare({"tool_name": "Edit", "tool_input": {"file_path": str(f)}, "session_id": "s"})
    copia_per_annullare({"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s"})
    voci = annulla.ultimo_gruppo(d / "jarvis-dati" / "annulla")
    assert len(voci) == 1 and Path(voci[0]["copia"]).read_text(encoding="utf-8") == "prima"


def test_annulla_a_voce_chiede_conferma():
    j = _jarvis()
    with mock.patch.object(principale, "DATI", _tmp()):
        cartella = principale.DATI / "annulla"
        f = principale.DATI / "y.txt"
        f.write_text("prima", encoding="utf-8")
        annulla.registra(cartella, f, "s")
        f.write_text("dopo", encoding="utf-8")
        chiesto = []
        j.chiedi_conferma = lambda d: chiesto.append(d) or True
        Jarvis.annulla_modifiche(j)
        assert "rimettere com'erano 1 file: y.txt" in chiesto[0]
        assert f.read_text(encoding="utf-8") == "prima" and "y.txt" in j.detti[-1]


# ------------------------------------------------------------ briefing
def test_briefing_con_i_dati_del_pc():
    j = _jarvis()
    from datetime import datetime, timedelta
    # oggi anche dopo le 23:30 (prima "tra 30 minuti" finiva a domani e il test falliva di sera)
    quando = min(datetime.now() + timedelta(minutes=30), datetime.now().replace(hour=23, minute=59, second=0))
    j.promemoria.aggiungi(quando, "chiamare il dentista")
    with mock.patch.object(principale, "stato_pannello", return_value={"cpu": {"temp": 55}, "gpu": {"temp": 61}}):
        testo = Jarvis.briefing(j)
    assert testo.startswith("[Briefing del mattino") and "chiamare il dentista" in testo
    assert "processore 55 °C" in testo and "calendario e posta" in testo
    assert "Meteo" not in testo                       # nessuna città nelle impostazioni
    assert principale._BRIEFING.match("buongiorno") and not principale._BRIEFING.match("buongiorno a tutti voi")


def test_meteo_mai_in_privata():
    j = _jarvis(citta="Catania", privata=True)
    with mock.patch("urllib.request.urlopen") as rete:
        assert Jarvis.leggi_meteo(j) == ""
        rete.assert_not_called()


# ------------------------------------------------------------ domande dalle altre sessioni di Claude Code
def test_domanda_con_risposta_a_voce_e_dal_tablet():
    j = _jarvis()
    esito = {}

    def chiedi():
        esito["r"] = Jarvis.chiedi_risposta(j, "Pubblico la versione 2.1?", ["sì", "aspetta"], 5)
    t = threading.Thread(target=chiedi)
    t.start()
    while not j.conferma:
        time.sleep(0.01)
    assert j.conferma["pubblico"]["domanda"] and j.conferma["pubblico"]["opzioni"] == ["sì", "aspetta"]
    assert "Puoi dire: sì, aspetta." in j.da_dire.get()
    j._risposta_conferma(None, "")                       # silenzio: aspetta ancora
    assert t.is_alive()
    j._risposta_conferma(True, "sì, vai pure")
    t.join(2)
    assert esito["r"] == "sì, vai pure"
    t = threading.Thread(target=chiedi)
    t.start()
    while not j.conferma:
        time.sleep(0.01)
    Jarvis.conferma_dal_pannello(j, j.conferma["id"], True, testo="aspetta")
    t.join(2)
    assert esito["r"] == "aspetta"


def test_jarvis_mcp_strumenti():
    from jarvis import jarvis_mcp
    chiamate = []

    def finto(percorso, corpo, attesa=15):
        chiamate.append((percorso, corpo))
        return {"risposta": "sì"} if percorso == "/jarvis/domanda" else {"ok": True}
    with mock.patch.object(jarvis_mcp, "_jarvis", finto):
        assert jarvis_mcp.jarvis_parla("Build finita") == "Detto."
        assert jarvis_mcp.jarvis_chiedi("Pubblico?", ["sì", "no"]) == "L'utente ha risposto: sì"
        assert "Promemoria salvato" in jarvis_mcp.jarvis_promemoria("2026-11-25 09:00", "riunione")
    assert [c[0] for c in chiamate] == ["/jarvis/annuncia", "/jarvis/domanda", "/jarvis/promemoria-esterno"]


# ------------------------------------------------------------ voce dal visore (tieni e parla)
def test_voce_intera_dal_quest():
    j = _jarvis()
    pcm = (np.sin(np.arange(16000) / 5) * 8000).astype(np.int16).tobytes()
    classe = next(c for c in vars(principale).values()
                  if isinstance(c, type) and hasattr(c, "voce_intera"))
    h = classe.__new__(classe)
    h.headers = {"Content-Length": str(len(pcm))}
    h.rfile = types.SimpleNamespace(read=lambda n: pcm[:n])
    risposte = []
    h.manda = lambda codice, corpo: risposte.append((codice, corpo))
    type(h).j = property(lambda self: j)
    classe.voce_intera(h)
    assert risposte[-1][0] == 200
    tipo, audio = j.richieste.get_nowait()
    assert tipo == "voce" and len(audio) == 16000 and j.stato == "penso" and j.conversazione
    h.headers = {"Content-Length": "10"}
    classe.voce_intera(h)
    assert risposte[-1][0] == 400


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_") and "monkeypatch" not in f.__code__.co_varnames:
            f()
            print("ok", nome)
