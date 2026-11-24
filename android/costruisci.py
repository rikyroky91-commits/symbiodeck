"""Costruisce PannelloPC.apk senza Gradle (aapt2, javac, d8, zipalign, apksigner dell'SDK).

L'indirizzo del PC e il token vengono da ../server/config.json e finiscono in Config.java,
così il tablet non va configurato a mano. Se l'IP del PC cambia, si ricostruisce e reinstalla.

    python costruisci.py [--ip 192.168.1.96] [--installa]
    python costruisci.py --generico      # APK per chiunque, in build-generico/: si abbina da solo al PC
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

QUI = Path(__file__).resolve().parent
COSTRUZIONE = QUI / "build"
CONFIG = QUI.parent / "server" / "config.json"
SDK = Path(os.environ.get("ANDROID_HOME") or os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"))
FIRMA = QUI / "firma" / "pannello.jks"
PASSWORD = "pannello-locale"   # chiave solo per installare sul proprio tablet, non per lo Store
MIN_SDK, TARGET_SDK = 26, 28  # 26: requestDismissKeyguard


def esegui(cmd, **kw):
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"Errore in {' '.join(cmd[:3])}…\n{r.stdout}\n{r.stderr}")
    return r.stdout


def attrezzi():
    bt = sorted((SDK / "build-tools").iterdir(), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")])[-1]
    jar = next(SDK / "platforms" / f"android-{v}" / "android.jar" for v in (34, 35, 36)
               if (SDK / "platforms" / f"android-{v}" / "android.jar").exists())
    return {"aapt2": bt / "aapt2.exe", "d8": bt / "d8.bat", "zipalign": bt / "zipalign.exe",
            "apksigner": bt / "apksigner.bat", "jar": jar}


def ip_del_pc():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 9))   # nessun pacchetto parte: serve solo a scegliere l'interfaccia
        return s.getsockname()[0]
    finally:
        s.close()


def icona(lato=192):
    """Icona disegnata a mano: quadrato scuro arrotondato con un anello azzurro e un arco verde."""
    import math
    dati = bytearray()
    c = (lato - 1) / 2
    for y in range(lato):
        dati.append(0)
        for x in range(lato):
            dx, dy = x - c, y - c
            r = math.hypot(dx, dy)
            # angoli arrotondati
            bordo = lato * 0.22
            qx, qy = max(abs(dx) - (c - bordo), 0), max(abs(dy) - (c - bordo), 0)
            dentro = math.hypot(qx, qy) <= bordo
            if not dentro:
                dati += b"\x00\x00\x00\x00"
                continue
            col = (16, 21, 29, 255)
            ang = (math.degrees(math.atan2(dy, dx)) + 90) % 360
            if lato * 0.27 <= r <= lato * 0.35:
                col = (53, 200, 255, 255) if ang <= 250 else (40, 52, 66, 255)
            elif r <= lato * 0.06:
                col = (118, 185, 0, 255)
            dati += bytes(col)
    def blocco(tipo, corpo):
        return struct.pack(">I", len(corpo)) + tipo + corpo + struct.pack(">I", zlib.crc32(tipo + corpo) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", lato, lato, 8, 6, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(bytes(dati), 9)) + blocco(b"IEND", b""))


def main():
    import json
    t = attrezzi()
    generico = "--generico" in sys.argv  # APK per chiunque: niente IP né chiave, il tablet si abbina da solo
    if generico:
        cfg, pc_nome = {"app_monitor": ""}, ""
    else:
        cfg, pc_nome = json.loads(CONFIG.read_text(encoding="utf-8")), os.environ.get("COMPUTERNAME", "")
        ip = sys.argv[sys.argv.index("--ip") + 1] if "--ip" in sys.argv else ip_del_pc()
        porta, token = cfg.get("porta", 47800), cfg["token"]
    url_usb = "" if generico else f"http://127.0.0.1:{porta}/?t={token}"
    url_rete = "" if generico else f"http://{ip}:{porta}/?t={token}"

    global COSTRUZIONE
    if generico:
        COSTRUZIONE = QUI / "build-generico"  # non tocca la build personale in build/
    shutil.rmtree(COSTRUZIONE, ignore_errors=True)
    gen = COSTRUZIONE / "gen" / "it" / "rikyr" / "pannello"
    gen.mkdir(parents=True)
    (gen / "Config.java").write_text(
        "package it.rikyr.pannello;\n\n/** Generato da costruisci.py: indirizzi del PC. */\nfinal class Config {\n"
        f'    static final String URL_USB = "{url_usb}";\n'
        f'    static final String URL_RETE = "{url_rete}";\n'
        # secondo monitor: PC (per nome) e app di Apollo (per UUID, così non serve l'elenco in cache di Artemis)
        f'    static final String PC_NOME = "{pc_nome}";\n'
        f'    static final String APP_MONITOR = "{cfg.get("app_monitor", "Virtual Display")}";\n'
        f'    static final String APP_MONITOR_UUID = "{cfg.get("app_monitor_uuid", "")}";\n'
        "    private Config() {}\n}\n", encoding="utf-8")

    shutil.copytree(QUI / "res", COSTRUZIONE / "res")
    (COSTRUZIONE / "res" / "mipmap").mkdir(exist_ok=True)
    (COSTRUZIONE / "res" / "mipmap" / "icona.png").write_bytes(icona())
    esegui([t["aapt2"], "compile", "--dir", COSTRUZIONE / "res", "-o", COSTRUZIONE / "res.zip"])
    base = COSTRUZIONE / "base.apk"
    esegui([t["aapt2"], "link", "-I", t["jar"], "--manifest", QUI / "AndroidManifest.xml",
            "--min-sdk-version", MIN_SDK, "--target-sdk-version", TARGET_SDK,
            "--version-code", 19, "--version-name", "1.10.0", "-A", QUI / "assets",
            "-o", base, COSTRUZIONE / "res.zip"])

    classi = COSTRUZIONE / "classi"
    sorgenti = [str(p) for p in (QUI / "src").rglob("*.java")] + [str(gen / "Config.java")]
    esegui(["javac", "-nowarn", "-Xlint:-options", "-encoding", "UTF-8", "-source", "8", "-target", "8",
            "-classpath", t["jar"], "-d", classi] + sorgenti)
    dex = COSTRUZIONE / "dex"
    dex.mkdir()
    esegui([t["d8"], "--release", "--lib", t["jar"], "--min-api", MIN_SDK, "--output", dex]
           + [str(p) for p in classi.rglob("*.class")])
    with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dex / "classes.dex", "classes.dex")

    allineato = COSTRUZIONE / "allineato.apk"
    esegui([t["zipalign"], "-p", "-f", "4", base, allineato])
    if not FIRMA.exists():
        FIRMA.parent.mkdir(exist_ok=True)
        esegui(["keytool", "-genkeypair", "-keystore", FIRMA, "-storepass", PASSWORD, "-keypass", PASSWORD,
                "-alias", "pannello", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                "-dname", "CN=Pannello PC"])
    finale = COSTRUZIONE / "PannelloPC.apk"
    esegui([t["apksigner"], "sign", "--ks", FIRMA, "--ks-pass", f"pass:{PASSWORD}", "--out", finale, allineato],
           shell=True)
    print(f"APK: {finale}  " + ("(generico: si abbina da solo)" if generico else f"(PC {ip}:{porta})"))

    if "--installa" in sys.argv:
        # L'app è "persistent" (dal 2026-11-14): Android rifiuta "adb install" ("Persistent apps are not
        # updateable"). Si sostituisce l'APK in /system/priv-app, si toglie l'eventuale aggiornamento in /data
        # (tenendo i dati: -k) e si riavvia il tablet. Serve "adb root" (LineageOS, root solo ADB).
        adb = shutil.which("adb") or r"C:\platform-tools\adb.exe"
        esegui([adb, "root"])
        esegui([adb, "wait-for-device"])
        esegui([adb, "remount"])
        print(esegui([adb, "push", finale, "/system/priv-app/PannelloPC/PannelloPC.apk"]).strip())
        esegui([adb, "shell", "chmod 644 /system/priv-app/PannelloPC/PannelloPC.apk; "
                              "chcon u:object_r:system_file:s0 /system/priv-app/PannelloPC/PannelloPC.apk; "
                              "rm -rf /system/priv-app/PannelloPC/oat; sync"])
        # permessi privilegiati e uscita dal risparmio energetico (Jarvis a schermo spento): prima del riavvio
        print(esegui([adb, "push", QUI / "privapp-permissions-pannello.xml",
                      "/system/etc/permissions/privapp-permissions-pannello.xml"]).strip())
        esegui([adb, "shell", "chmod 644 /system/etc/permissions/privapp-permissions-pannello.xml; "
                              "chcon u:object_r:system_file:s0 /system/etc/permissions/privapp-permissions-pannello.xml"])
        r = subprocess.run([adb, "shell", "pm", "uninstall", "-k", "it.rikyr.pannello"], capture_output=True, text=True)
        print("aggiornamento in /data:", (r.stdout + r.stderr).strip() or "nessuno")
        print("riavvio del tablet…")
        esegui([adb, "reboot"])
        esegui([adb, "wait-for-device"])
        esegui([adb, "shell", "while [ \"$(getprop sys.boot_completed)\" != 1 ]; do sleep 2; done"])
        # appena avviato il gestore dei pacchetti può rispondere "cannot be home": si riprova per un po'
        esegui([adb, "shell", "for i in 1 2 3 4 5 6 7 8 9 10; do cmd package set-home-activity "
                              "it.rikyr.pannello/.Pannello && break; sleep 3; done"])
        # Jarvis: microfono concesso senza toccare il tablet, servizio fuori da Doze anche se il file sopra manca
        subprocess.run([adb, "shell", "pm", "grant", "it.rikyr.pannello", "android.permission.RECORD_AUDIO"],
                       capture_output=True)
        # telecamera per Jarvis ("guarda questa scheda madre"): si usa solo dopo il sì dell'utente a ogni richiesta
        subprocess.run([adb, "shell", "pm", "grant", "it.rikyr.pannello", "android.permission.CAMERA"],
                       capture_output=True)
        subprocess.run([adb, "shell", "dumpsys", "deviceidle", "whitelist", "+it.rikyr.pannello"], capture_output=True)
        print(esegui([adb, "shell", "dumpsys package it.rikyr.pannello | grep -E 'versionName|codePath'"]).strip())


if __name__ == "__main__":
    main()
