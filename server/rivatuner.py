"""FPS letti direttamente dalla memoria condivisa di RivaTuner (RTSSSharedMemoryV2), senza MSI Afterburner.

Fino al 2026-11-28 gli FPS arrivavano solo da Afterburner (MAHMSharedMemory 0x50/0x53/0x55): se Afterburner era
chiuso (o perdeva l'avvio con Windows) il tablet restava senza FPS anche con RivaTuner acceso e agganciato al gioco.
Qui si legge RivaTuner da solo: basta che RTSS.exe giri con il rilevamento attivo (EnableHooking=1).

Il gioco è il programma della finestra in primo piano, se RivaTuner lo ha agganciato e sta disegnando: così sul
desktop non compaiono gli FPS di iCUE o di altri programmi che RivaTuner aggancia ma che non sono giochi.
- ora: fotogrammi dell'ultimo intervallo di RivaTuner (circa un secondo)
- media: dal primo fotogramma visto di quel gioco
- low1: media dei tempi del fotogramma peggiori (1%) negli ultimi 5 minuti, campionati 20 volte al secondo

Formato (RTSSSharedMemory.h dell'SDK di RivaTuner): intestazione con firma "RTSS", versione, dimensione, posizione e
numero delle voci delle app; ogni voce comincia con dwProcessID, szName[260], dwFlags, dwTime0, dwTime1, dwFrames,
dwFrameTime (microsecondi). dwTime0/dwTime1 sono millisecondi di GetTickCount.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

NOME = "RTSSSharedMemoryV2"
ATTIVITA_RTSS = "RTSS"      # attività pianificata che avvia RTSS.exe /s all'accesso (la crea RivaTuner)
PROFILO_GLOBALE = Path(r"C:\Program Files (x86)\RivaTuner Statistics Server\Profiles\Global")
SENZA_FINESTRA = 0x08000000
PASSO = 0.05                # 20 campioni al secondo del tempo del fotogramma
FRESCO_MS = 2500            # un intervallo più vecchio di così: il programma non disegna più
TENUTA = 2.0                # secondi in cui il gioco resta valido dopo aver perso il primo piano (tocco sul tablet)
CAMPIONI_LOW = int(300 / PASSO)

_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.GetTickCount.restype = wt.DWORD


class Statistiche:
    """Conti di un gioco (un processo), senza chiamate a Windows: si prova con tests/test_rivatuner.py."""

    def __init__(self):
        self.ultimo = None          # (t0, t1) dell'ultimo intervallo contato
        self.ora = None
        self.fotogrammi = 0
        self.millisecondi = 0
        self.tempi = deque(maxlen=CAMPIONI_LOW)
        self.visto = time.time()

    def intervallo(self, t0, t1, fotogrammi):
        """Un intervallo di RivaTuner (lo stesso si ripete a ogni campione finché non ne arriva uno nuovo)."""
        durata = (t1 - t0) & 0xFFFFFFFF
        if not durata or not fotogrammi or durata > 60000:
            return
        self.ora = 1000.0 * fotogrammi / durata
        if (t0, t1) != self.ultimo:
            self.ultimo = (t0, t1)
            self.fotogrammi += fotogrammi
            self.millisecondi += durata

    def tempo(self, microsecondi):
        if 0 < microsecondi < 5_000_000:   # sopra i 5 s è un caricamento o una pausa, non un fotogramma
            self.tempi.append(microsecondi)

    def risultato(self):
        media = 1000.0 * self.fotogrammi / self.millisecondi if self.millisecondi else None
        low1 = None
        if len(self.tempi) >= 100:
            peggiori = sorted(self.tempi, reverse=True)[:max(1, len(self.tempi) // 100)]
            low1 = 1e6 / (sum(peggiori) / len(peggiori))
            if media is not None:
                low1 = min(low1, media)
        return {"ora": self.ora, "media": media, "low1": low1}


def processo_davanti():
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wt.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def rilevamento_spento():
    """True se nel profilo globale di RivaTuner il rilevamento delle applicazioni è spento (niente FPS)."""
    try:
        testo = PROFILO_GLOBALE.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    return "\nEnableHooking=0" in "\n" + testo


class RivaTuner(threading.Thread):
    """Legge RivaTuner 20 volte al secondo; lettura() dà gli FPS del gioco in primo piano (o None)."""

    def __init__(self, log=print):
        super().__init__(daemon=True, name="rivatuner")
        self.log = log
        self.blocco = threading.Lock()
        self.giochi = {}            # pid -> Statistiche
        self.gioco = None           # pid del gioco attuale
        self.gioco_fino = 0         # time.time() fino a cui vale anche senza primo piano
        self.stato = "chiuso"       # "ok", "chiuso" (RTSS non gira), "spento" (rilevamento disattivato)
        self._h = None
        self._vista = None
        self._indici = {}           # pid -> indice della voce, riletto ogni secondo
        self._indici_letti = 0
        self._chiuso_da = time.time()
        self._rilancio = 0
        self._spento_letto = 0
        self._spento = False
        self._aperta_alle = 0

    # ------------------------------------------------ memoria condivisa

    def _apri(self):
        # OpenFileMapping e non mmap: mmap creerebbe la memoria vuota se RivaTuner è chiuso
        h = _k32.OpenFileMappingW(0x0004, False, NOME)   # FILE_MAP_READ
        if not h:
            return False
        vista = _k32.MapViewOfFile(h, 0x0004, 0, 0, 0)
        if not vista:
            _k32.CloseHandle(h)
            return False
        self._h, self._vista = h, vista
        self._indici_letti = 0
        self._aperta_alle = time.time()
        return True

    def _chiudi(self):
        if self._vista:
            _k32.UnmapViewOfFile(ctypes.c_void_p(self._vista))
        if self._h:
            _k32.CloseHandle(self._h)
        self._h = self._vista = None

    def _intestazione(self):
        firma, ver, dim, pos, num = struct.unpack("<4sIIII", ctypes.string_at(self._vista, 20))
        if firma != b"SSTR" or ver < 0x00020000 or dim < 284 or not 0 < num <= 4096:   # "RTSS" come DWORD
            return None
        return dim, pos, num

    def _voce(self, dim, pos, i):
        """(pid, t0, t1, fotogrammi, tempo_us) della voce i."""
        base = self._vista + pos + i * dim
        pid, = struct.unpack("<I", ctypes.string_at(base, 4))
        _fl, t0, t1, fotogrammi, tempo_us = struct.unpack("<5I", ctypes.string_at(base + 264, 20))
        return pid, t0, t1, fotogrammi, tempo_us

    def _indice(self, dim, pos, num, pid):
        if time.time() - self._indici_letti > 1:
            self._indici = {}
            for i in range(num):
                p, = struct.unpack("<I", ctypes.string_at(self._vista + pos + i * dim, 4))
                if p:
                    self._indici.setdefault(p, i)
            self._indici_letti = time.time()
        return self._indici.get(pid)

    # ------------------------------------------------ giro

    def run(self):
        while True:
            try:
                self.passo()
            except Exception as e:   # RivaTuner che si chiude a metà lettura: si riapre al giro dopo
                self.log("rivatuner:", repr(e))
                self._chiudi()
                time.sleep(1)
            time.sleep(PASSO)

    def passo(self):
        ora = time.time()
        if self._vista and ora - self._aperta_alle > 10:
            # la si riapre ogni tanto: tenuta aperta da noi sopravvive a RTSS chiuso, ferma ma con la firma giusta
            self._chiudi()
        if not self._vista and not self._apri():
            self._non_aperto(ora)
            time.sleep(1)
            return
        testa = self._intestazione()
        if not testa:
            self._chiudi()
            self._non_aperto(ora)
            time.sleep(1)
            return
        self._chiuso_da = None
        if ora - self._spento_letto > 30:
            spento = rilevamento_spento()
            if spento and not self._spento:
                self.log("rivatuner: rilevamento delle applicazioni spento (EnableHooking=0): niente FPS."
                         " Rimedio: server\\rtss-rilevamento.ps1 da amministratore")
            self._spento, self._spento_letto = spento, ora
        dim, pos, num = testa
        tick = _k32.GetTickCount()
        davanti = processo_davanti()
        trovato = bool(davanti) and self._leggi(dim, pos, num, davanti, tick)
        # il gioco ha perso il primo piano da poco (un tocco sul tablet): resta lui per TENUTA secondi
        tenuto = (not trovato and self.gioco is not None and self.gioco != davanti and ora <= self.gioco_fino
                  and self._leggi(dim, pos, num, self.gioco, tick))
        with self.blocco:
            self.stato = "spento" if self._spento else "ok"
            if trovato:
                self.gioco, self.gioco_fino = davanti, ora + TENUTA
            elif not tenuto:
                self.gioco = None   # niente davanti che disegna: desktop, gioco chiuso o fermo
            for pid in [p for p, s in self.giochi.items() if ora - s.visto > 600]:
                del self.giochi[pid]

    def _leggi(self, dim, pos, num, pid, tick):
        """Aggiorna le statistiche di pid; True se RivaTuner lo vede disegnare adesso."""
        i = self._indice(dim, pos, num, pid)
        if i is None:
            return False
        p, t0, t1, fotogrammi, tempo_us = self._voce(dim, pos, i)
        if p != pid:
            self._indici_letti = 0
            return False
        if not t1 or ((tick - t1) & 0xFFFFFFFF) > FRESCO_MS or not fotogrammi:
            return False
        with self.blocco:
            s = self.giochi.get(pid)
            if s is None:
                s = self.giochi[pid] = Statistiche()
            s.visto = time.time()
            s.intervallo(t0, t1, fotogrammi)
            s.tempo(tempo_us)
        return True

    def _non_aperto(self, ora):
        with self.blocco:
            self.stato, self.gioco = "chiuso", None
        if self._chiuso_da is None:
            self._chiuso_da = ora
            self.log("rivatuner: memoria condivisa assente (RTSS chiuso?)")
        # RivaTuner chiuso da 15 s: lo si riapre con la sua attività pianificata, al massimo ogni 2 minuti
        if ora - self._chiuso_da > 15 and ora - self._rilancio > 120:
            self._rilancio = ora
            esito = subprocess.run(["schtasks", "/run", "/tn", ATTIVITA_RTSS], capture_output=True,
                                   creationflags=SENZA_FINESTRA)
            self.log("rivatuner: rilancio l'attività", ATTIVITA_RTSS, "esito", esito.returncode)

    def lettura(self):
        """{"ora", "media", "low1"} del gioco attuale (valori None senza gioco) e lo stato di RivaTuner."""
        with self.blocco:
            s = self.giochi.get(self.gioco) if self.gioco else None
            fps = s.risultato() if s else {"ora": None, "media": None, "low1": None}
            return fps, self.stato
