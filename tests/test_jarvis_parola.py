"""Parola chiave verificata (2026-11-24): con un punteggio incerto Jarvis si accende solo se Whisper sente davvero
"Jarvis" negli ultimi 2 s; quello detto mentre verifica non si perde. Il ciclo di ascolto vero con parola chiave,
rilevatore di voce e Whisper finti. Senza audio né modelli: python tests/test_jarvis_parola.py"""
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
from jarvis import principale  # noqa: E402
from jarvis.principale import PREDEFINITE, Jarvis  # noqa: E402
from jarvis.voce_claude import NOME, Trascrittore  # noqa: E402

# i test non scrivono nel registro vero di Jarvis (server/jarvis.log)
principale.REGISTRO = Path(tempfile.gettempdir()) / "jarvis-prove.log"

VOCE, SILENZIO = 3000, 0     # valore dei campioni: il VAD finto riconosce la voce da qui


def _blocco(valore, punteggio=0.0):
    b = np.full(1280, valore, dtype=np.int16)
    b[0] = int(punteggio * 1000)          # il punteggio della parola chiave viaggia nel primo campione
    return b


class _ParolaFinta:
    def __init__(self, *a, **k):
        pass

    def predict(self, blocco):
        return {"hey_jarvis": blocco[0] / 1000}

    def reset(self):
        pass


class _VadFinto:
    def predict(self, blocco, frame_size=640):
        return 1.0 if abs(int(blocco[1])) > 1000 else 0.0

    def reset_states(self):
        pass


def _jarvis(dice_jarvis):
    j = Jarvis.__new__(Jarvis)
    j.imp = dict(PREDEFINITE, sensibilita=0.4, pausa=0.6)
    j.blocchi, j.richieste = queue.Queue(), queue.Queue()
    j.stato, j.conversazione, j.dettatura, j.domanda, j.conferma = "riposo", False, False, None, None
    j.livello, j.utente_parla, j.solo_remoto = 0.0, False, False
    j.uscita = types.SimpleNamespace(aec=None, parla=False, fine_audio=0, livello_uscita=lambda: 0.0,
                                     riprendi=lambda: None)
    j.tablet = types.SimpleNamespace(aec=False, comando=lambda **k: None)
    j.claude = types.SimpleNamespace(occupato=False, lavora_in_sottofondo=lambda: False)
    j.suoni, j.eventi = [], []
    j._suona = j.suoni.append
    j.evento = lambda **d: j.eventi.append(d)
    j._aggiorna_microfono = lambda: None
    j._coda_eco = lambda aec: False
    j.stt = types.SimpleNamespace(dice_jarvis=dice_jarvis)
    return j


def _avvia(j):
    oww = types.ModuleType("openwakeword")
    oww.utils = types.SimpleNamespace(download_models=lambda **k: None)
    finti = {"openwakeword": oww, "openwakeword.utils": oww.utils,
             "openwakeword.model": types.SimpleNamespace(Model=_ParolaFinta),
             "openwakeword.vad": types.SimpleNamespace(VAD=_VadFinto)}
    with mock.patch.dict(sys.modules, finti):
        threading.Thread(target=j.ciclo_ascolto, daemon=True).start()
        time.sleep(0.2)


def _manda(j, blocchi, pausa=0.0):
    for b in blocchi:
        j.blocchi.put(b)
        if pausa:
            time.sleep(pausa)


def test_nome_come_lo_scrive_whisper():
    for detto in ("e giarvis a bespotify", "e i GARVIS.", "Jarvis, che ore sono?", "Ehi Jarvis", "hey jervis",
                  "Ei Giarvis apri", "Hey Jarbis", "Ehi Tarvis", "Ehi Cervis", "e carvis", "E carvissi.",
                  "E' già visto."):
        assert NOME.search(detto), detto
    for frase in ("Dobbiamo festeggiare!", "La pipi delle 4 e mezza.", "Ma che dobbiamo fare stasera?",
                  "Hai visto la partita ieri sera?", "Davvero, non ci credo.", "Ho letto l'avviso", "Travis Scott",
                  "Gli avvisi del servizio", "Davis Cup", "e ciao come va?",
                  "Grazie a tutti.", "Ho previsto tutto", "Il servizio clienti", "Servirà una visita", "Arriba."):
        assert not NOME.search(frase), frase


def _trascrittore(senza, con):
    """Whisper finto: `senza` è il testo senza suggerimento, `con` quello suggerendo la parola "Jarvis"."""
    chiamate = []

    def transcribe(audio, **k):
        chiamate.append(k.get("hotwords"))
        testo = con if k.get("hotwords") else senza
        return [types.SimpleNamespace(text=testo, no_speech_prob=0.0, avg_logprob=0.0)], None
    t = Trascrittore({}, print)
    t.modello = types.SimpleNamespace(transcribe=transcribe)
    t.prepara = lambda: None
    return t, chiamate


def test_verifica_riprova_suggerendo_il_nome():
    pcm = np.zeros(16000, dtype=np.int16)
    t, chiamate = _trascrittore("Ehi Yardis", "Hey Jarvis")
    assert t.dice_jarvis(pcm) == (True, "Hey Jarvis") and chiamate == [None, "Jarvis"]
    t, chiamate = _trascrittore("Ehi Jarvis", "non serve")
    assert t.dice_jarvis(pcm) == (True, "Ehi Jarvis") and chiamate == [None]      # capito subito: niente riprova
    t, chiamate = _trascrittore("Grazie.", "Grazie.")
    assert t.dice_jarvis(pcm) == (False, "Grazie.")                               # resta il testo senza suggerimento


def test_parola_incerta_scartata_se_whisper_non_sente_jarvis():
    sentiti = []
    j = _jarvis(lambda pcm: (sentiti.append(len(pcm)) or (False, "dobbiamo festeggiare")))
    _avvia(j)
    _manda(j, [_blocco(VOCE) for _ in range(10)] + [_blocco(VOCE, 0.47)] + [_blocco(VOCE) for _ in range(20)]
           + [_blocco(SILENZIO) for _ in range(30)], pausa=0.002)
    time.sleep(0.5)
    assert sentiti and sentiti[0] <= 25 * 1280                 # gli ultimi 2 s al massimo
    assert j.stato == "riposo" and not j.suoni and j.richieste.empty()


def test_parola_incerta_confermata_tiene_la_richiesta_detta_di_fila():
    def lenta(pcm):
        time.sleep(0.3)                                         # Whisper sulla CPU: intanto l'utente parla
        return True, "e i jarvis"
    j = _jarvis(lenta)
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.47)])
    _manda(j, [_blocco(VOCE) for _ in range(12)], pausa=0.01)   # "apri Spotify" mentre verifica
    time.sleep(0.4)
    _manda(j, [_blocco(SILENZIO) for _ in range(15)], pausa=0.002)
    tipo, pcm = j.richieste.get(timeout=2)
    assert tipo == "voce" and len(pcm) >= 12 * 1280             # la richiesta intera, non persa nella verifica
    assert "attivo" not in j.suoni                              # parlava già: niente suono sopra le sue parole
    assert "capito" in j.suoni


def test_punteggio_alto_e_whisper_senza_parole_accende():
    # registro del 2026-11-26: "Hey Jarvis" a 0,83 trascritto "Grazie." (invenzione di Whisper sull'audio corto)
    j = _jarvis(lambda pcm: (False, "Grazie."))
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.83)] + [_blocco(SILENZIO) for _ in range(10)], pausa=0.002)
    time.sleep(0.4)
    assert j.stato == "ascolto" and "attivo" in j.suoni


def test_punteggio_alto_ma_frase_vera_non_accende():
    j = _jarvis(lambda pcm: (False, "no per un giallo"))
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.83)] + [_blocco(SILENZIO) for _ in range(10)], pausa=0.002)
    time.sleep(0.4)
    assert j.stato == "riposo" and not j.suoni


def test_punteggio_basso_e_whisper_senza_parole_non_accende():
    j = _jarvis(lambda pcm: (False, "Grazie a tutti."))
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.45)] + [_blocco(SILENZIO) for _ in range(10)], pausa=0.002)
    time.sleep(0.4)
    assert j.stato == "riposo" and not j.suoni


def test_seguito_conta_da_quando_jarvis_ha_finito_di_parlare():
    # 2026-11-26: "quando Jarvis mi fa una domanda deve rimanere in ascolto": il tempo partiva da quando pensava, così
    # dopo una risposta lunga il seguito era già finito
    j = _jarvis(lambda pcm: (False, ""))
    j.imp["seguito"] = 1
    j.conversazione, j.stato = True, "penso"
    _avvia(j)
    _manda(j, [_blocco(SILENZIO) for _ in range(5)], pausa=0.1)       # pensa 0,5 s
    j.stato, j.uscita.parla = "parlo", True
    _manda(j, [_blocco(SILENZIO) for _ in range(15)], pausa=0.1)      # risponde per 1,5 s
    j.stato, j.uscita.parla = "seguito", False
    _manda(j, [_blocco(SILENZIO) for _ in range(6)], pausa=0.1)       # 0,6 s dopo la fine: ascolta ancora
    time.sleep(0.1)
    assert j.stato == "seguito"
    _manda(j, [_blocco(SILENZIO) for _ in range(10)], pausa=0.1)      # 1,6 s dopo: scaduto
    time.sleep(0.2)
    assert j.stato == "riposo"


def test_parola_sicura_basta_da_sola():
    chiamate = []
    j = _jarvis(lambda pcm: chiamate.append(1) or (False, ""))
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.95)])
    time.sleep(0.3)
    assert j.stato == "ascolto" and "attivo" in j.suoni and not chiamate


def test_whisper_guasto_non_accende():
    def guasto(pcm):
        raise RuntimeError("cublas64_12.dll")
    j = _jarvis(guasto)
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.5)] + [_blocco(SILENZIO) for _ in range(10)], pausa=0.002)
    time.sleep(0.4)
    assert j.stato == "riposo" and not j.suoni


def test_verifica_spenta_come_prima():
    j = _jarvis(lambda pcm: (False, ""))
    j.imp["verifica_parola"] = False
    _avvia(j)
    _manda(j, [_blocco(VOCE, 0.45)])
    time.sleep(0.3)
    assert j.stato == "ascolto"


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
