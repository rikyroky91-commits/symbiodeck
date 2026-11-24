# Lavori per il worker locale (Qwen su llama-server)

Compiti stretti e verificabili da affidare al worker (`C:\Users\<utente>\llm\worker.ps1`). Claude scrive il brief,
il worker esegue, Claude rivede il diff e rilancia il controllo. Un compito per volta, un file di test per compito.

Regole per tutti i brief:
- toccare solo il file indicato (nuovo file di test, salvo dove detto altro);
- stile di `tests/test_custode.py`: funzioni `test_*` con `assert`, niente librerie esterne, in fondo il blocco
  `if __name__ == "__main__":` che esegue tutte le `test_*` e stampa `ok <nome>`;
- import: `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))`;
- niente rete, niente audio, niente GPU, niente processi `claude`: si usano file temporanei e oggetti finti.

| # | Stato | Compito | File | Controllo |
|---|---|---|---|---|
| W1 | fatto (worker MoE, 14 min, rivisto) | Test di `Esecutore.conta_domande` e `continuata_nell_app` con un registro `.jsonl` finto | `tests/test_jarvis_sessioni.py` | `python tests/test_jarvis_sessioni.py` |
| W2 | fatto (worker MoE, 5,5 min, rivisto) | Test di `audio.Blocchi` (pezzi di lunghezza qualsiasi → blocchi da 1280 campioni) | `tests/test_jarvis_audio.py` | `python tests/test_jarvis_audio.py` |
| W3 | fatto (worker, rivisto) | Test di `cervello.Cervello` con esecutore e voce finti (affida, esito alla voce, stato_lavoro) | `tests/test_jarvis_cervello.py` | `python tests/test_jarvis_cervello.py` |
| W4 | fatto (worker MoE, 9 min, rivisto) | Test di `guardia.main` come processo (JSON su stdin, `JARVIS_DELEGA`, uscita deny) | `tests/test_jarvis_guardia_processo.py` | `python tests/test_jarvis_guardia_processo.py` |
| W5 | fatto (worker MoE, 1 min, rivisto) | `tests/tutti.py`: esegue tutti i `tests/test_*.py` e riassume ok/falliti | `tests/tutti.py` | `python tests/tutti.py` |

## Brief

### W1 – sessioni
Crea `tests/test_jarvis_sessioni.py`. Leggi prima `server/jarvis/esecutore.py` (metodi `registro`, `_righe`,
`conta_domande`, `continuata_nell_app`). Crea un `Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="",
file_config=tmp/"x.json")` con `tempfile.TemporaryDirectory()`, poi sostituisci il metodo `registro` dell'istanza
con `lambda sessione=None: percorso_jsonl` (un file nella cartella temporanea) e imposta `e.sessione = "s1"`.
Scrivi nel file righe JSON (una per riga). Casi:
1. due righe `{"type":"user","message":{"content":"ciao"}}` e una `{"type":"assistant",...}` → `conta_domande()==2`;
2. una riga user con content lista (risultato di uno strumento) non conta;
3. una riga user con content che inizia con `<task-notification>` non conta;
4. righe non JSON vengono ignorate;
5. `e._domande_note = 2` e il file ha 2 domande → `continuata_nell_app()` è False; aggiungi una domanda → True;
6. `e._domande_note = None` → False.

### W2 – blocchi audio
Crea `tests/test_jarvis_audio.py`. Leggi `server/jarvis/audio.py` (classe `Blocchi`, `BLOCCO_IN`). Raccogli i blocchi
in una lista passando `su_blocco=lista.append`. Casi: 1) 3000 byte → un blocco (2560 byte) e 440 in `buf`; 2) poi altri 2120 byte → un secondo
blocco e `buf` vuoto; 3) dati in 7 pezzi da 1000 byte → 2 blocchi;
4) i valori del blocco corrispondono ai byte inseriti (usa `numpy.arange(..., dtype=numpy.int16).tobytes()`).

### W3 – cervello
Crea `tests/test_jarvis_cervello.py`. Leggi `server/jarvis/cervello.py`. Non avviare processi: crea il Cervello e poi
sostituisci `c.voce` e `c.esec` con oggetti finti (classe con `chiedi(testo)` che salva i testi, `occupato=False`,
`lavora_in_sottofondo()` → False, `interrompi()`). Casi: 1) `c._v_strumento("mcp__jarvis__esegui", {"richiesta":
"conta i file"})` → dopo un breve `time.sleep(0.2)` l'esecutore finto ha ricevuto un testo che contiene "conta i file"
e la frase dell'utente (imposta prima `c._ultima_frase = "quanti file?"`); 2) `c._e_fine("ci sono 3 file", None)` →
la voce finta ha ricevuto un testo che contiene "[Esito del lavoro" e "ci sono 3 file"; 3) `c._e_fine("", "boom")` →
il testo contiene "Errore: boom"; 4) `stato_lavoro()` contiene le chiavi occupato, richiesta, minuti, azioni;
5) `c._v_strumento("mcp__jarvis__stato_lavoro", {})` non manda niente all'esecutore.

### W4 – guardia come processo
Crea `tests/test_jarvis_guardia_processo.py`. Esegui `server/jarvis/guardia.py` con `subprocess.run([sys.executable,
percorso], input=json.dumps(dati).encode(), capture_output=True, env=...)`. Casi: 1) con `JARVIS_DELEGA=1` e
`{"tool_name":"PowerShell","tool_input":{"command":"ls"}}` → stdout JSON con `permissionDecision == "deny"`;
2) stesso input con `"agent_type":"esecutore"` → stdout vuoto (ls non chiede conferma); 3) senza `JARVIS_DELEGA` e
`Remove-Item x` con `JARVIS_PORTA=1` (porta chiusa: Jarvis non risponde) → `deny`; 4) input non JSON → nessun errore,
stdout vuoto.

### W5 – tutti i test
Crea `tests/tutti.py`: trova `tests/test_*.py`, li esegue uno per uno con `subprocess.run([sys.executable, f])`,
stampa `OK nome` o `FALLITO nome` con le ultime 10 righe dell'errore, alla fine `N ok, M falliti` ed esce con codice 1
se qualcosa fallisce.
