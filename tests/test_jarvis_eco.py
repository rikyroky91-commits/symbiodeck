"""Prove della cancellazione dell'eco sul PC (server/jarvis/eco.py) con segnali sintetici: python tests/test_jarvis_eco.py.

Il "tablet" è simulato: quello che Jarvis manda alle casse rientra nel microfono con un ritardo, attraverso una
piccola stanza, più un po' di rumore. Il tempo è virtuale (nessuna attesa vera)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis.eco import CancellaEco  # noqa: E402

FREQ = 16000
rng = np.random.default_rng(3)


def voce_finta(secondi, freq=24000):
    """Rumore modulato come una voce (sillabe di 100-350 ms, ogni tanto una pausa), 24 kHz int16."""
    n = int(secondi * freq)
    inviluppo = np.zeros(n)
    i = 0
    while i < n:
        lung = min(int(rng.uniform(0.1, 0.35) * freq), n - i)
        forza = 0.0 if rng.random() < 0.15 else rng.uniform(0.3, 1.0)
        inviluppo[i:i + lung] = forza * np.sin(np.linspace(0, np.pi, lung)) ** 0.5
        i += lung
    colore = np.convolve(rng.normal(0, 1, n), np.ones(6) / 6, "same")
    return np.clip(colore * inviluppo * 9000, -32767, 32767).astype(np.int16)


def stanza():
    ir = np.zeros(int(0.08 * FREQ))
    ir[0] = 1.0
    ir[1:] = rng.normal(0, 1, len(ir) - 1) * np.exp(-np.arange(1, len(ir)) / (0.015 * FREQ)) * 0.2
    return ir


def simula(uscita24, ritardo=0.45, guadagno=0.3, vicino=None, t_vicino=0.0, file_ritardo=None, salto=None,
           log=None, blocchi=None, distorsione=0.0):
    """Ritorna (aec, grezzo, pulito) per tutto il microfono, a blocchi da 1280 come in Jarvis.
    salto=(t, ms): da t secondi in poi l'eco arriva ms millisecondi più tardi (il tablet è ripartito)."""
    from scipy.signal import fftconvolve, resample_poly
    aec = CancellaEco(log=log or (lambda *a: None), file_ritardo=file_ritardo)
    orologio = [0.0]
    aec.ora = lambda: orologio[0]
    u16 = resample_poly(uscita24.astype(np.float64), 2, 3)
    durata = len(u16) / FREQ + 1.0
    mic = np.zeros(int(durata * FREQ))
    suono = u16 if not distorsione else np.tanh(u16 / 32768 * distorsione) / np.tanh(distorsione) * 32768
    eco = fftconvolve(suono, stanza())[:len(u16)] * guadagno
    a = int(ritardo * FREQ)
    mic[a:a + len(eco)] += eco[:len(mic) - a]
    if salto is not None:
        tj, s = int(salto[0] * FREQ), int(salto[1] / 1000 * FREQ)
        dopo = np.zeros(len(mic))
        dopo[a + s:a + s + len(eco)] += eco[:len(mic) - a - s]
        mic[tj:] += dopo[tj:] - np.concatenate([np.zeros(a), eco, np.zeros(len(mic))])[tj:len(mic)]
    if vicino is not None:
        b = int(t_vicino * FREQ)
        mic[b:b + len(vicino)] += vicino[:len(mic) - b]
    mic += rng.normal(0, 60, len(mic))
    grezzo, pulito, inviati = [], [], 0
    for k in range(0, len(mic) - 1280 + 1, 1280):
        t = k / FREQ
        orologio[0] = t
        while inviati < len(uscita24) and inviati / 24000 - 0.35 <= t:
            aec.riferimento(uscita24[inviati:inviati + 2400], inviati / 24000, 0.0)
            inviati += 2400
        blocco = np.clip(mic[k:k + 1280], -32768, 32767).astype(np.int16)
        grezzo.append(blocco)
        pulito.append(aec.pulisci(blocco, t + 0.08 + abs(rng.normal(0, 0.02))))
        if blocchi is not None:   # (t, rms grezzo, rms pulito, la cancellazione si dice affidabile)
            blocchi.append((t, float(np.sqrt(np.mean(grezzo[-1].astype(float) ** 2))),
                            float(np.sqrt(np.mean(pulito[-1].astype(float) ** 2))), aec.affidabile()))
    return aec, np.concatenate(grezzo).astype(np.float64), np.concatenate(pulito).astype(np.float64)


def db(a, b):
    return 10 * np.log10(np.mean(a ** 2) / np.mean(b ** 2))


def test_toglie_l_eco():
    aec, grezzo, pulito = simula(voce_finta(10))
    s = slice(6 * FREQ, 10 * FREQ)
    assert db(grezzo[s], pulito[s]) > 15, db(grezzo[s], pulito[s])
    assert abs(aec.ritardo - 0.45) <= 0.03, aec.ritardo
    assert aec.affidabile()


def test_senza_jarvis_non_tocca_niente():
    aec = CancellaEco(log=lambda *a: None)
    blocco = rng.normal(0, 2000, 1280).astype(np.int16)
    assert np.array_equal(aec.pulisci(blocco, 1.0), blocco)
    assert not aec.affidabile()      # niente misure: non si interrompe a voce


def test_lascia_la_voce_di_chi_parla_sopra():
    """Doppio parlato: dopo la cancellazione la voce vicina deve restare quasi intera (non schiacciata)."""
    vicino = rng.normal(0, 1, 2 * FREQ) * 3000 * (0.5 + 0.5 * np.sin(np.arange(2 * FREQ) / FREQ * 2 * np.pi * 3) ** 2)
    t_vicino = 7.0
    _aec, _g, pulito = simula(voce_finta(10), vicino=vicino, t_vicino=t_vicino)
    s = slice(int(t_vicino * FREQ), int((t_vicino + 2) * FREQ))
    # quello che resta nel tratto con la voce vicina è almeno la voce vicina meno 2 dB
    assert db(pulito[s], vicino) > -2, db(pulito[s], vicino)
    # e non molto di più (l'eco è stata tolta anche lì)
    assert db(pulito[s], vicino) < 3, db(pulito[s], vicino)


def test_ritardo_ricordato(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "eco-ritardo.json"
        aec, _g, _p = simula(voce_finta(6), ritardo=0.8, file_ritardo=f)
        assert f.exists()
        nuovo = CancellaEco(log=lambda *a: None, file_ritardo=f)
        assert nuovo.ritardo_noto and abs(nuovo.ritardo - aec.ritardo) < 1e-3


def test_casse_sature():
    """Eco così forte da saturare il microfono: niente interruzione a voce finché dura."""
    aec, grezzo, _p = simula(voce_finta(6), guadagno=6.0)
    assert np.mean(np.abs(grezzo) >= 32000) > 0.01
    assert aec.ora() < aec.saturo_fino or not aec.affidabile()


def test_blocchi_di_misura_strana():
    aec = CancellaEco(log=lambda *a: None)
    blocco = rng.normal(0, 2000, 1000).astype(np.int16)
    assert np.array_equal(aec.pulisci(blocco, 1.0), blocco)


def test_ritmo_regolare_non_fa_ripartire():
    """Sillabe a ritmo fisso (5 al secondo): la correlazione ha picchi quasi uguali ogni 200 ms. Il ritardo,
    trovato una volta, non deve saltare su un altro picco facendo ripartire il filtro."""
    n = 10 * 24000
    inviluppo = 0.5 + 0.5 * np.sin(np.arange(n) / 24000 * 2 * np.pi * 2.5) ** 2
    pcm = np.clip(np.convolve(rng.normal(0, 1, n), np.ones(6) / 6, "same") * inviluppo * 9000,
                  -32767, 32767).astype(np.int16)
    aec, grezzo, pulito = simula(pcm)
    assert aec.epoca <= 1, aec.epoca                 # al massimo il primo aggancio
    s = slice(6 * FREQ, 10 * FREQ)
    assert db(grezzo[s], pulito[s]) > 15, db(grezzo[s], pulito[s])


def test_interrotto_niente_eco_fantasma():
    """Dopo taci() il riferimento già mandato ma non suonato non va sottratto: il microfono (che non ha più eco)
    deve uscire com'è."""
    aec = CancellaEco(log=lambda *a: None)
    orologio = [10.0]
    aec.ora = lambda: orologio[0]
    pcm = voce_finta(1.0)
    for i in range(0, len(pcm), 2400):                 # 1 s mandato in anticipo, a partire da adesso
        aec.riferimento(pcm[i:i + 2400], 10.0 + i / 24000, 0.0)
    aec.ritardo = 0.0
    aec.taci()
    assert not aec._rif_per(10.0, 16000).any()


def test_eco_che_si_sposta():
    """A metà risposta l'eco arriva 22 ms più tardi (il tablet è ripartito da buffer vuoto): il filtro se ne
    accorge, intanto non si decidono interruzioni, si riallinea al campione senza ripartire da zero e torna a
    togliere l'eco in meno di un secondo."""
    for salto in (22, -15, 6):
        righe, blocchi = [], []
        aec, grezzo, pulito = simula(voce_finta(12), salto=(6.0, salto), blocchi=blocchi,
                                     log=lambda *a: righe.append(" ".join(map(str, a))))
        # nessun blocco con l'eco rimasta (meno di 10 dB tolti) mentre si dice affidabile: niente falsi allarmi
        scoperti = [round(t, 2) for t, g, p, aff in blocchi if t > 5.9 and g > 1500 and aff and g / max(p, 1) < 3.16]
        assert not scoperti, (salto, scoperti)
        assert aec.epoca <= 1, aec.epoca             # riallineato o riadattato, mai ripartito da zero
        s = slice(8 * FREQ, 12 * FREQ)
        assert db(grezzo[s], pulito[s]) > 15, (salto, db(grezzo[s], pulito[s]))
    s = slice(7 * FREQ, 12 * FREQ)
    assert db(grezzo[s], pulito[s]) > 15, db(grezzo[s], pulito[s])


def test_risposta_nuova_non_fa_ripartire():
    """Due risposte separate da 3 s di silenzio; nella seconda il tablet suona 18 ms più tardi (è ripartito da
    buffer vuoto). Il filtro non deve ripartire da zero (prima la stima grossolana lo faceva a ogni risposta) e
    nessun blocco con l'eco rimasta deve contare per un'interruzione."""
    pcm = np.concatenate([voce_finta(6), np.zeros(3 * 24000, dtype=np.int16), voce_finta(6)])
    blocchi = []
    aec, grezzo, pulito = simula(pcm, salto=(7.5, 18), blocchi=blocchi)
    assert aec.epoca <= 1, aec.epoca
    scoperti = [round(t, 2) for t, g, p, aff in blocchi if t > 7 and g > 1500 and aff and g / max(p, 1) < 3.16]
    assert not scoperti, scoperti
    s = slice(11 * FREQ, 15 * FREQ)
    assert db(grezzo[s], pulito[s]) > 15, db(grezzo[s], pulito[s])


def test_casse_che_distorcono():
    """Casse piccole che distorcono (come quelle del tablet): il residuo somiglia un po' all'eco, ma è debole. La
    cancellazione deve restare "affidabile" (prima il controllo dell'allineamento la spegneva di continuo e
    l'interruzione a voce non scattava più)."""
    blocchi = []
    _aec, grezzo, pulito = simula(voce_finta(12), distorsione=1.5, blocchi=blocchi)
    voce = [aff for t, g, p, aff in blocchi if 7 < t < 12 and g > 1500]
    assert sum(voce) >= 0.8 * len(voce), (sum(voce), len(voce))
    s = slice(7 * FREQ, 12 * FREQ)
    assert db(grezzo[s], pulito[s]) > 12, db(grezzo[s], pulito[s])


def test_stato():
    aec, _g, _p = simula(voce_finta(8))
    st = aec.stato()
    assert set(st) == {"ritardo_ms", "eco_tolta_db", "affidabile"}
    assert st["eco_tolta_db"] is not None and st["eco_tolta_db"] > 10


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
