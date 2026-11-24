"""Jarvis XR per la Libreria del Quest: un APK piccolissimo che apre https://<IP del PC>/ nel Browser del Quest.
Stessi attrezzi e stessa firma dell'app del tablet (../costruisci.py). L'IP del PC finisce nell'APK: se cambia,
si ricostruisce. Il Quest va collegato in adb (modalità sviluppatore).

    python android/quest/costruisci_quest.py [--ip 192.168.1.96] [--installa]
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parent))
from costruisci import FIRMA, PASSWORD, attrezzi, esegui, ip_del_pc  # noqa: E402

COSTRUZIONE = QUI / "build"
ICONA = QUI.parent.parent / "web" / "icone" / "jarvis-xr-192.png"
PACCHETTO = "it.rikyr.jarvisxr"
VERSIONE = (1, "1.0")


def quest(adb):
    """Il numero di serie del Quest collegato (il tablet può essere collegato anche lui)."""
    righe = esegui([adb, "devices", "-l"]).splitlines()[1:]
    return next((r.split()[0] for r in righe if "Quest" in r and " device " in r), None)


def main():
    t = attrezzi()
    ip = sys.argv[sys.argv.index("--ip") + 1] if "--ip" in sys.argv else ip_del_pc()
    shutil.rmtree(COSTRUZIONE, ignore_errors=True)
    gen = COSTRUZIONE / "gen" / "it" / "rikyr" / "jarvisxr"
    gen.mkdir(parents=True)
    (gen / "Config.java").write_text(
        "package it.rikyr.jarvisxr;\n\n/** Generato da costruisci_quest.py. */\nfinal class Config {\n"
        f'    static final String URL = "https://{ip}/";\n    private Config() {{}}\n}}\n', encoding="utf-8")
    res = COSTRUZIONE / "res" / "mipmap"
    res.mkdir(parents=True)
    shutil.copy(ICONA, res / "icona.png")
    esegui([t["aapt2"], "compile", "--dir", COSTRUZIONE / "res", "-o", COSTRUZIONE / "res.zip"])
    base = COSTRUZIONE / "base.apk"
    esegui([t["aapt2"], "link", "-I", t["jar"], "--manifest", QUI / "AndroidManifest.xml",
            "--min-sdk-version", 29, "--target-sdk-version", 32,
            "--version-code", VERSIONE[0], "--version-name", VERSIONE[1], "-o", base, COSTRUZIONE / "res.zip"])
    classi = COSTRUZIONE / "classi"
    sorgenti = [str(p) for p in (QUI / "src").rglob("*.java")] + [str(gen / "Config.java")]
    esegui(["javac", "-nowarn", "-Xlint:-options", "-encoding", "UTF-8", "-source", "8", "-target", "8",
            "-classpath", t["jar"], "-d", classi] + sorgenti)
    dex = COSTRUZIONE / "dex"
    dex.mkdir()
    esegui([t["d8"], "--release", "--lib", t["jar"], "--min-api", 29, "--output", dex]
           + [str(p) for p in classi.rglob("*.class")])
    with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dex / "classes.dex", "classes.dex")
    allineato = COSTRUZIONE / "allineato.apk"
    esegui([t["zipalign"], "-p", "-f", "4", base, allineato])
    if not FIRMA.exists():
        sys.exit(f"Manca la chiave di firma {FIRMA}: costruisci prima l'app del tablet (python android/costruisci.py)")
    finale = COSTRUZIONE / "JarvisXR-Quest.apk"
    esegui([t["apksigner"], "sign", "--ks", FIRMA, "--ks-pass", f"pass:{PASSWORD}", "--out", finale, allineato],
           shell=True)
    print(f"APK: {finale}  (apre https://{ip}/)")
    if "--installa" in sys.argv:
        adb = shutil.which("adb") or r"C:\platform-tools\adb.exe"
        seriale = quest(adb)
        if not seriale:
            sys.exit("Quest non collegato in adb: modalità sviluppatore, cavo USB e 'Consenti' nel visore")
        print(esegui([adb, "-s", seriale, "install", "-r", finale]).strip())
        print("Nel Quest: Libreria > Fonti sconosciute > Jarvis XR (si può fissare nella barra)")


if __name__ == "__main__":
    main()
