"""Modalità Gemini: la conversazione a voce la fa Gemini Live (audio nativo: risponde subito, voce naturale),
ma ogni compito vero sul PC lo passa a Claude Code con lo strumento esegui_sul_pc.

Serve una chiave API di Google AI Studio (impostazione gemini_chiave): l'abbonamento Gemini non la include.
Le conferme delle azioni irreversibili restano a Jarvis (voce locale, vedi principale.chiedi_conferma).
"""
import asyncio
import threading
import time

from .audio import FREQ_IN

ISTRUZIONI = """Sei Jarvis, il maggiordomo digitale del tuo utente: calmo, preciso, ironia asciutta e garbata.
Parli italiano, dai del lei, lo chiami "signore" ogni tanto. Rispondi a voce con frasi brevi e naturali.
Sei collegato al suo PC Windows tramite lo strumento esegui_sul_pc, che affida il compito a un agente capace di
fare qualsiasi cosa sul PC (aprire programmi e file, cercare, scrivere, usare PowerShell, cercare in rete).
- Per QUALSIASI azione o informazione sul PC chiama esegui_sul_pc con la richiesta completa e precisa, in italiano.
- Prima di chiamarlo di' una frase brevissima ("Subito, signore."). Quando torna il risultato riassumilo in una
  o due frasi parlate, senza leggere elenchi o percorsi.
- Domande di cultura generale o chiacchiere: rispondi tu, senza strumento.
- Se l'agente dice che l'utente non ha confermato un'azione, non insistere.
- Non dire mai la frase "ehi Jarvis"."""

STRUMENTI = [{"function_declarations": [{
    "name": "esegui_sul_pc",
    "description": "Esegue un compito sul PC dell'utente con un agente (Claude Code) e restituisce l'esito.",
    "parameters": {"type": "OBJECT", "properties": {
        "richiesta": {"type": "STRING", "description": "Cosa fare, in italiano, completo di tutti i dettagli."}},
        "required": ["richiesta"]},
}]}]


class Gemini:
    def __init__(self, jarvis, log):
        from google import genai  # noqa: F401  (errore subito se la libreria manca)
        self.j = jarvis
        self.log = log
        self.ciclo = asyncio.new_event_loop()
        threading.Thread(target=self.ciclo.run_forever, daemon=True, name="gemini").start()
        self.sessione = None
        self.compito = None
        self.ultima_attivita = 0.0
        self.lavora = False
        self.pezzi = []
        self.detto = ""

    # ------------------------------------------------------------ interfaccia per principale.py
    def aperta(self):
        return self.sessione is not None

    def attiva(self):
        self.ultima_attivita = time.time()
        if self.compito and not self.compito.done():
            self.j.imposta_stato("ascolto")
            return
        if not self.j.imp.get("gemini_chiave"):
            self.j.di("Signore, per la modalità Gemini serve una chiave API di Google AI Studio. "
                      "La può inserire nelle impostazioni di Jarvis.")
            return
        self.j.imposta_stato("ascolto")
        self.j._suona("attivo")
        self.compito = asyncio.run_coroutine_threadsafe(self._sessione(), self.ciclo)

    def audio(self, blocco):
        """Blocco del microfono (int16, 16 kHz) da mandare a Gemini."""
        s = self.sessione
        if s is None:
            return
        from google.genai import types
        asyncio.run_coroutine_threadsafe(
            s.send_realtime_input(audio=types.Blob(data=blocco.tobytes(), mime_type=f"audio/pcm;rate={FREQ_IN}")),
            self.ciclo)

    def chiudi(self):
        if self.compito and not self.compito.done():
            self.compito.cancel()

    # ------------------------------------------------------------ sessione
    def _modello(self, client):
        scelto = self.j.imp.get("gemini_modello")
        if scelto:
            return scelto
        candidati = []
        for m in client.models.list():
            azioni = getattr(m, "supported_actions", None) or []
            if "bidiGenerateContent" in azioni:
                candidati.append(m.name.split("/")[-1])
        if not candidati:
            raise RuntimeError("nessun modello Gemini Live disponibile per questa chiave")
        # preferenza: audio nativo, poi "live"; a parità il nome più recente
        candidati.sort(key=lambda n: ("native-audio" in n, "live" in n, n), reverse=True)
        self.log("modelli Gemini Live:", ", ".join(candidati[:6]))
        self.j.imp["gemini_modello"] = candidati[0]
        return candidati[0]

    async def _sessione(self):
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=self.j.imp["gemini_chiave"])
        try:
            modello = await asyncio.get_running_loop().run_in_executor(None, self._modello, client)
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=ISTRUZIONI,
                tools=STRUMENTI,
                speech_config=types.SpeechConfig(
                    language_code="it-IT",
                    voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.j.imp.get("gemini_voce") or "Charon"))),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
            )
            async with client.aio.live.connect(model=modello, config=config) as s:
                self.sessione = s
                self.log("sessione Gemini aperta:", modello)
                await self._ricevi(s)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log("errore Gemini:", repr(e)[:300])
            self.j.imp["gemini_modello"] = ""  # al prossimo giro si riprova a sceglierlo
            self.j.evento(errore="Gemini: " + str(e)[:160])
            self.j.di("Signore, non riesco a collegarmi a Gemini. Controlli la chiave nelle impostazioni.")
        finally:
            self.sessione = None
            self.j.uscita.taci()
            self.j.uscita.riprendi()
            if self.j.stato not in ("spento", "conferma"):
                self.j.imposta_stato("riposo")
            self.log("sessione Gemini chiusa")

    async def _ricevi(self, s):
        riproduttore = _Riproduttore(self.j)
        controllo = asyncio.current_task()

        async def sorveglia():
            while True:
                await asyncio.sleep(1)
                limite = max(8, self.j.imp.get("seguito", 6) + 4)
                if not self.lavora and not self.j.uscita.parla and riproduttore.vuoto() \
                        and time.time() - self.ultima_attivita > limite:
                    self.j._suona("fine")
                    controllo.cancel()
                    return
        guardiano = asyncio.ensure_future(sorveglia())
        try:
            while True:
                async for msg in s.receive():
                    if msg.server_content:
                        self._contenuto(msg.server_content, riproduttore)
                    if msg.tool_call:
                        for chiamata in msg.tool_call.function_calls or []:
                            asyncio.ensure_future(self._strumento(s, chiamata))
                    if msg.go_away:
                        self.log("Gemini chiude la sessione tra", msg.go_away.time_left)
        finally:
            guardiano.cancel()
            riproduttore.ferma()

    def _contenuto(self, c, riproduttore):
        if c.interrupted:
            riproduttore.svuota()
            self.j.uscita.taci()
            self.j.uscita.riprendi()
        if c.input_transcription and c.input_transcription.text:
            self.ultima_attivita = time.time()
            self.j.evento(utente=c.input_transcription.text.strip())
        if c.model_turn:
            for parte in c.model_turn.parts or []:
                if parte.inline_data and parte.inline_data.data:
                    riproduttore.aggiungi(parte.inline_data.data)
        if c.output_transcription and c.output_transcription.text:
            self.detto += c.output_transcription.text
            self.j.evento(jarvis_pezzo=c.output_transcription.text)
        if c.turn_complete:
            self.ultima_attivita = time.time()
            if self.detto.strip():
                self.j.evento(jarvis=self.detto.strip())
            self.detto = ""

    async def _strumento(self, s, chiamata):
        from google.genai import types
        self.lavora = True
        try:
            if chiamata.name == "esegui_sul_pc":
                richiesta = (chiamata.args or {}).get("richiesta", "")
                self.log("Gemini -> Claude:", richiesta)
                esito = await asyncio.get_running_loop().run_in_executor(None, self.j.esegui_silenzioso, richiesta)
                risposta = {"esito": esito}
            else:
                risposta = {"errore": "strumento sconosciuto"}
            await s.send_tool_response(function_responses=[
                types.FunctionResponse(id=chiamata.id, name=chiamata.name, response=risposta)])
        finally:
            self.lavora = False
            self.ultima_attivita = time.time()


class _Riproduttore:
    """Suona in ordine i pezzi di audio di Gemini (PCM 24 kHz) in un thread, senza bloccare la ricezione."""

    def __init__(self, j):
        self.j = j
        self.coda = []
        self.cond = threading.Condition()
        self.attivo = True
        threading.Thread(target=self._ciclo, daemon=True, name="gemini-voce").start()

    def aggiungi(self, pcm):
        with self.cond:
            self.coda.append(pcm)
            self.cond.notify()

    def svuota(self):
        with self.cond:
            self.coda.clear()

    def vuoto(self):
        return not self.coda

    def ferma(self):
        self.attivo = False
        with self.cond:
            self.cond.notify()

    def _ciclo(self):
        while self.attivo:
            with self.cond:
                self.cond.wait_for(lambda: self.coda or not self.attivo, timeout=1)
                if not self.coda:
                    if self.j.stato == "parlo":
                        self.j.imposta_stato("seguito")
                    continue
                pcm = b"".join(self.coda)
                self.coda.clear()
            if self.j.stato not in ("conferma", "spento"):
                self.j.imposta_stato("parlo")
            self.j.uscita.riproduci(pcm)
