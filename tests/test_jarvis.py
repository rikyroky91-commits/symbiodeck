"""Prove delle parti di Jarvis senza audio né rete: python tests/test_jarvis.py oppure pytest."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis.guardia import valuta  # noqa: E402
from jarvis.trame import (TRAMA_AUDIO, TRAMA_COMANDO, Frasi, Interruzione, descrivi_azione,  # noqa: E402
                          leggi_trame, pulisci_per_voce, si_o_no, trama)


# ------------------------------------------------------------ trame audio
def test_trame_intere_e_spezzate():
    a, b = trama(TRAMA_AUDIO, b"abc"), trama(TRAMA_COMANDO, b'{"microfono":true}')
    assert leggi_trame(a) == ([(1, b"abc")], b"")
    assert leggi_trame(a + b)[0] == [(1, b"abc"), (2, b'{"microfono":true}')]
    trame, resto = leggi_trame(a[:4])
    assert trame == [] and resto == a[:4]
    assert leggi_trame(resto + a[4:]) == ([(1, b"abc")], b"")


# ------------------------------------------------------------ testo da dire
def test_frasi_a_pezzi():
    f = Frasi(minimo=10)
    assert f.aggiungi("Ciao signore. Come") == ["Ciao signore."]
    assert f.aggiungi(" sta oggi?") == ["Come sta oggi?"]
    assert f.fine() == []


def test_frasi_corte_si_uniscono():
    f = Frasi(minimo=12)
    assert f.aggiungi("Sì. Fatto. Ho aperto il blocco note. Altro") == ["Sì. Fatto. Ho aperto il blocco note."]
    assert f.fine() == ["Altro."]


def test_codice_non_si_legge():
    f = Frasi(minimo=5)
    assert f.aggiungi("Ecco:\n```\nGet-Date\n") == ["Ecco:"]
    assert f.aggiungi("```\nFatto.") == ["Fatto."]
    assert f.fine() == []


def test_elenchi_diventano_frasi():
    f = Frasi()
    pezzi = []
    for p in ["Io toglierei:\n- **Samsung", " Magician**: serve solo\n- Adobe", " Acrobat\n\nFatto."]:
        pezzi += f.aggiungi(p)
    assert pezzi + f.fine() == ["Io toglierei:", "Samsung Magician: serve solo.", "Adobe Acrobat.", "Fatto."]


def test_pulisci_per_voce():
    assert pulisci_per_voce("**ciao** [link](http://x.it)") == "ciao link."
    assert pulisci_per_voce("# Titolo") == "Titolo."
    assert pulisci_per_voce("- voce") == "voce."
    assert pulisci_per_voce("vedi https://example.com ora") == "vedi il link ora."
    # nessun simbolo letto alla lettera
    assert pulisci_per_voce("#cosa ti propongo#") == "cosa ti propongo."
    for simbolo in "#*_|`<>":
        assert simbolo not in pulisci_per_voce("a " + simbolo * 2 + " b " + simbolo + "c" + simbolo)
    assert pulisci_per_voce("| Disco | Liberi |\n|---|---|\n| C: | 105 GB |") == "Disco, Liberi. C:, 105 giga."
    assert pulisci_per_voce("CPU a 72°C ~ 120 W") == "CPU a 72 gradi circa 120 watt."
    assert pulisci_per_voce("Adobe → aggiornalo a mano") == "Adobe, poi aggiornalo a mano."
    assert pulisci_per_voce("3840x2160 in C# e/o Python 😀") == "3840 per 2160 in C sharp e o Python."


def test_descrivi_azione():
    assert descrivi_azione("Read", {"file_path": "C:\\a\\b\\nota.txt"}) == "Leggo nota.txt"
    assert descrivi_azione("Bash", {"command": "ls", "description": "Elenco file"}) == "Eseguo: Elenco file"
    assert descrivi_azione("mcp__gmail__send_email", {}).startswith("Uso gmail")
    assert descrivi_azione("Agent", {"description": "x"}).startswith("Passo")
    assert descrivi_azione("ToolSearch", {"query": "x"}) is None


def test_si_o_no():
    assert si_o_no("sì") is True
    assert si_o_no("Sì, confermo.") is True
    assert si_o_no("vai pure") is True
    assert si_o_no("no") is False
    assert si_o_no("annulla") is False
    assert si_o_no("sì ma no") is False   # il no vince
    assert si_o_no("boh") is None


# ------------------------------------------------------------ guardia
CONFERMA = [
    ("Bash", "rm -rf build"), ("Bash", "del C:\\x\\a.txt"), ("Bash", "Remove-Item foo.txt"),
    ("Bash", "shutdown /s /t 0"), ("Bash", "git push origin main"), ("Bash", "git reset --hard HEAD"),
    ("Bash", "reg delete HKCU\\Software\\X /f"), ("Bash", "winget uninstall Spotify"),
    ("Bash", "Stop-Process -Name chrome -Force"), ("Bash", "cd x; rd /s /q cartella"),
    ("Bash", "Format-Volume -DriveLetter D"), ("PowerShell", "Remove-Item C:\\temp\\x"),
    ("PowerShell", "Get-ChildItem *.tmp | Remove-Item"), ("Bash", "python -c \"import shutil; shutil.rmtree('x')\""),
]
LIBERI = [
    "ls", "Get-ChildItem", "Start-Process notepad", "Get-Date -Format HH:mm",
    "Get-ChildItem 'C:\\Users\\x\\Immagini del mare'", "git status", "git log --oneline",
    "Get-Process | Format-Table", "echo 'la cartella del progetto'",
]


def test_guardia_chiede_conferma():
    for nome, comando in CONFERMA:
        assert valuta(nome, {"command": comando}) is not None, comando
    assert valuta("Write", {"file_path": "C:\\Windows\\system32\\drivers\\etc\\hosts", "content": ""})
    assert valuta("mcp__gmail__send_email", {})
    assert valuta("mcp__slack__post_message", {})


def test_guardia_lascia_passare():
    for comando in LIBERI:
        assert valuta("Bash", {"command": comando}) is None, comando
    assert valuta("Read", {"file_path": "C:\\Windows\\win.ini"}) is None
    assert valuta("Glob", {"pattern": "*"}) is None
    assert valuta("Edit", {"file_path": "C:\\Users\\x\\nota.txt"}) is None
    assert valuta("mcp__gmail__search_emails", {}) is None


def test_guardia_livelli():
    assert valuta("Bash", {"command": "rm -rf build"}, "mai") is None
    assert valuta("Bash", {"command": "ls"}, "sempre") is not None
    assert valuta("Read", {"file_path": "x"}, "sempre") is None


def test_guardia_sovrascrittura():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    try:
        assert valuta("Write", {"file_path": f.name, "content": "x"}) is not None
        assert valuta("Write", {"file_path": f.name + ".nuovo", "content": "x"}) is None
    finally:
        os.remove(f.name)


def test_voce_delega_all_esecutore():
    from jarvis.guardia import solo_esecutore
    os.environ["JARVIS_DELEGA"] = "1"
    try:
        assert solo_esecutore({"tool_name": "PowerShell"}) is not None           # la voce non lavora
        assert solo_esecutore({"tool_name": "Agent"}) is None                    # ma può affidare il compito
        assert solo_esecutore({"tool_name": "PowerShell", "agent_type": "esecutore"}) is None
    finally:
        del os.environ["JARVIS_DELEGA"]
    assert solo_esecutore({"tool_name": "PowerShell"}) is None                   # fuori da Jarvis: nessun vincolo


def test_interruzione_ignora_la_sua_eco():
    i = Interruzione()
    for n in range(200):                     # Jarvis parla, la sua voce rientra al 10-25%
        rif = 0.08 + 0.06 * (n % 7) / 7
        assert not i.passo(0.95, rif * (0.10 + 0.15 * (n % 3) / 3), rif, 1.6), n


def test_interruzione_sente_chi_gli_parla_sopra():
    i = Interruzione()
    for n in range(30):
        i.passo(0.95, 0.012, 0.1, 1.6)       # impara l'eco
    scatti = [i.passo(0.98, 0.07, 0.1, 1.6) for _ in range(10)]   # una persona più forte dell'eco
    assert True in scatti and scatti.index(True) <= 6
    assert not Interruzione().passo(0.99, 0.5, 0.0, 1.6)          # la sua voce non è ancora partita


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
