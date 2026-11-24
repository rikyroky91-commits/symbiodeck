"""Cattura dalla scheda video (dxcam) in un processo a parte, per il cinema di Jarvis XR.

Dentro Jarvis dxcam ha fatto cadere tutto il processo con un errore nativo (0xC0000409, 2026-11-24): accanto alle
altre librerie native di Jarvis (CUDA, ONNX, COM). Qui, da solo, va (misurato: 50-58 fotogrammi/s). Jarvis lo
avvia per ogni diretta e legge da stdout [4 byte di lunghezza][JPEG]...; se questo processo cade, Jarvis continua
con la cattura GDI.

    python -m jarvis.cattura <numero> <hwnd> <lato> <fps>
"""
import os
import sys

os.environ["JARVIS_CATTURA_DXGI"] = "1"   # solo qui visore usa dxcam

from . import visore  # noqa: E402


def main():
    numero, hwnd, lato, fps = (int(x) for x in sys.argv[1:5])
    uscita = sys.stdout.buffer
    try:
        for jpeg in visore.diretta(numero, hwnd, lato, fps):
            uscita.write(len(jpeg).to_bytes(4, "big") + jpeg)
            uscita.flush()
    except (BrokenPipeError, OSError, LookupError):
        pass


if __name__ == "__main__":
    main()
