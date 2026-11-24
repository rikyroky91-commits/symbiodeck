"""Esegue tutti i test tests/test_*.py uno per uno e stampa il riepilogo: python tests/tutti.py (da qualsiasi cartella)."""
import subprocess
import sys
from pathlib import Path

CARTELLA = Path(__file__).resolve().parent
ok = falliti = 0

for f in sorted(CARTELLA.glob("test_*.py")):
    risultato = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=str(CARTELLA.parent))
    if risultato.returncode == 0:
        print(f"OK {f.stem}")
        ok += 1
    else:
        print(f"FALLITO {f.stem}")
        uscita = (risultato.stderr or risultato.stdout).strip().splitlines()
        for riga in uscita[-10:]:
            print(f"  {riga}")
        falliti += 1

print(f"{ok} ok, {falliti} falliti")
sys.exit(1 if falliti else 0)
