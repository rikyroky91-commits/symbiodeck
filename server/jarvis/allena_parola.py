"""Prove e allenamento di "Hey Jarvis" sulla voce vera. Con Jarvis acceso, dalla cartella server:

    python -m jarvis.allena_parola registra si 60    # 60 s dicendo "Hey Jarvis", con una pausa tra una e l'altra
    python -m jarvis.allena_parola registra no 120   # 120 s parlando d'altro (anche TV, bambini, musica)
    python -m jarvis.allena_parola prova             # quante volte si accende e quante conferma Whisper
    python -m jarvis.allena_parola allena            # modello sulla tua voce e confronto prima/dopo

Si registra dal microfono che Jarvis sta usando (tablet, PC o visore), solo per il tempo chiesto, e intanto Jarvis
non risponde. I file restano sul PC in jarvis-dati/parola/ (fuori da git): si cancellano a mano.
Il modello allenato si salva ma Jarvis non lo usa ancora."""
import json
import pickle
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

CARTELLA = Path(__file__).resolve().parent.parent / "jarvis-dati" / "parola"
FREQ, BLOCCO = 16000, 1280
ETICHETTE = ("si", "no")


class Registrazione:
    """Mette da parte i blocchi del microfono per `secondi`, poi li salva in un WAV. La usa Jarvis."""

    def __init__(self, etichetta, secondi, alla_fine, cartella=CARTELLA):
        if etichetta not in ETICHETTE:
            raise ValueError('etichetta: "si" oppure "no"')
        secondi = max(1, min(300, int(secondi)))
        self.percorso = Path(cartella) / etichetta / time.strftime("%Y%m%d-%H%M%S.wav")
        self.blocchi = []
        self.alla_fine = alla_fine
        self.orologio = threading.Timer(secondi, self.chiudi)
        self.orologio.daemon = True
        self.orologio.start()

    def aggiungi(self, blocco):
        self.blocchi.append(np.array(blocco, dtype=np.int16))

    def chiudi(self):
        self.orologio.cancel()
        secondi = len(self.blocchi) * BLOCCO / FREQ
        if self.blocchi:
            self.percorso.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.percorso), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(FREQ)
                w.writeframes(np.concatenate(self.blocchi).tobytes())
        self.alla_fine(self.percorso if self.blocchi else None, secondi)


def leggi(percorso):
    with wave.open(str(percorso), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def frasi(parlato, pausa=10):
    """Da una lista di sì/no (un valore per blocco di 80 ms) alle frasi: (inizio, fine), separate da `pausa` blocchi."""
    trovate = []
    for i, p in enumerate(parlato):
        if not p:
            continue
        if trovate and i - trovate[-1][1] <= pausa:
            trovate[-1][1] = i
        else:
            trovate.append([i, i])
    return [(a, b) for a, b in trovate if b - a >= 3]      # sotto 0,3 s: colpi e fruscii


def accensioni(punteggi, soglia, distanza=19):
    """I blocchi in cui Jarvis si accenderebbe (come nel ciclo di ascolto: non due volte entro 1,5 s)."""
    trovate = []
    for i, p in enumerate(punteggi):
        if p >= soglia and (not trovate or i - trovate[-1] > distanza):
            trovate.append(i)
    return trovate


def _a_blocchi(pcm):
    return [pcm[i:i + BLOCCO] for i in range(0, len(pcm) - BLOCCO + 1, BLOCCO)]


def prova(file, soglia, verificatore=None):
    """Per ogni etichetta: frasi dette, accensioni di openWakeWord e quante Whisper conferma."""
    from openwakeword.model import Model
    from openwakeword.vad import VAD
    from .voce_claude import Trascrittore
    extra = {"custom_verifier_models": {"hey_jarvis": str(verificatore)}} if verificatore else {}
    modello = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx", **extra)
    vad, stt = VAD(), Trascrittore({"stt": "auto"}, lambda *a: None)
    for etichetta in ETICHETTE:
        dette = accese = confermate = 0
        durata = 0.0
        for f in file[etichetta]:
            blocchi = _a_blocchi(leggi(f))
            modello.reset()
            vad.reset_states()
            punteggi = [modello.predict(b)["hey_jarvis"] for b in blocchi]
            dette += len(frasi([vad.predict(b, frame_size=640) > 0.5 for b in blocchi]))
            durata += len(blocchi) * BLOCCO / FREQ
            for i in accensioni(punteggi, soglia):
                accese += 1
                # da 0,9 in su Jarvis si accende senza chiedere a Whisper (parola_sicura)
                confermate += punteggi[i] >= 0.9 or stt.dice_jarvis(np.concatenate(blocchi[max(0, i - 24):i + 1]))[0]
        nome = '"Hey Jarvis"' if etichetta == "si" else "altro parlato"
        print(f"  {nome}: {durata:.0f} s, {dette} frasi, si è acceso {accese} volte, passate {confermate}")


def allena(file, uscita):
    from openwakeword.custom_verifier_model import get_reference_clip_features, train_verifier_model
    from openwakeword.model import Model
    modello = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    def caratteristiche(percorsi, soglia, giri):
        tutte = []
        for f in percorsi:
            modello.reset()
            tutte.append(get_reference_clip_features(leggi(f), modello, "hey_jarvis", threshold=soglia, N=giri))
        return np.vstack(tutte)

    # soglia 0.1 e non 0.5 (quella di openWakeWord): servono anche i "Hey Jarvis" che oggi sente poco
    si, no = caratteristiche(file["si"], 0.1, 5), caratteristiche(file["no"], 0.0, 1)
    if not len(si):
        raise SystemExit('Nelle registrazioni "si" la parola chiave non si sente mai: registrane altre, più vicino.')
    lr = train_verifier_model(np.vstack((si, no)), np.array([1] * len(si) + [0] * len(no)))
    with open(uscita, "wb") as f:
        pickle.dump(lr, f)


def registra(etichetta, secondi):
    from .principale import PORTA, token_pannello
    tablet, pc = token_pannello()
    richiesta = urllib.request.Request(
        f"http://127.0.0.1:{PORTA}/jarvis/registra-parola", method="POST", headers={"X-Token": pc or tablet},
        data=json.dumps({"etichetta": etichetta, "secondi": secondi}).encode())
    try:
        urllib.request.urlopen(richiesta, timeout=10).read()
    except urllib.error.URLError as e:
        raise SystemExit(f"Jarvis non risponde ({e}): è acceso e aggiornato?")
    cosa = 'di\' "Hey Jarvis", con 2 secondi di pausa tra una e l\'altra' if etichetta == "si" else \
        'parla d\'altro, senza mai dire "Jarvis"'
    print(f"Registro per {secondi} s: {cosa}. Il secondo suono è la fine.")
    time.sleep(secondi + 1)
    print("Finito: l'esito è in jarvis.log.")


def main(argomenti):
    if argomenti[:1] == ["registra"] and len(argomenti) == 3 and argomenti[1] in ETICHETTE:
        return registra(argomenti[1], int(argomenti[2]))
    if argomenti not in (["prova"], ["allena"]):
        raise SystemExit(__doc__)
    from .principale import CONFIG
    try:
        soglia = float(json.loads(CONFIG.read_text(encoding="utf-8"))["sensibilita"])
    except (OSError, ValueError, KeyError):
        soglia = 0.5
    file = {e: sorted((CARTELLA / e).glob("*.wav")) for e in ETICHETTE}
    if not file["si"] or not file["no"]:
        raise SystemExit('Servono registrazioni "si" e "no": python -m jarvis.allena_parola registra si 60')
    if argomenti == ["prova"]:
        print(f"Come adesso (soglia {soglia}):")
        return prova(file, soglia)
    if len(file["si"]) < 2 or len(file["no"]) < 2:
        raise SystemExit("Servono almeno 2 registrazioni per tipo: l'ultima di ognuna resta fuori per il controllo.")
    studio = {e: file[e][:-1] for e in ETICHETTE}
    controllo = {e: file[e][-1:] for e in ETICHETTE}
    uscita = CARTELLA / "verificatore.pkl"
    allena(studio, uscita)
    print("Sulle registrazioni non usate per allenare, prima:")
    prova(controllo, soglia)
    print("con il modello sulla tua voce:")
    prova(controllo, soglia, uscita)
    print(f"Modello salvato in {uscita} (Jarvis non lo usa ancora).")


if __name__ == "__main__":
    main(sys.argv[1:])
