"""Prove di guardia.main come processo separato (JSON su stdin, JARVIS_DELEGA, uscita deny): python tests/test_jarvis_guardia_processo.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

GUARDIA = str(Path(__file__).resolve().parent.parent / "server" / "jarvis" / "guardia.py")


def test_delega_deny():
    """JARVIS_DELEGA=1 + PowerShell -> permissionDecision == 'deny'."""
    env = os.environ.copy()
    env["JARVIS_DELEGA"] = "1"
    dati = {"tool_name": "PowerShell", "tool_input": {"command": "ls"}}
    p = subprocess.run([sys.executable, GUARDIA], input=json.dumps(dati).encode(),
                       capture_output=True, env=env)
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_delega_esecutore_non_deny():
    """Stesso input ma agent_type='esecutore' -> stdout vuoto (nessuna conferma richiesta)."""
    env = os.environ.copy()
    env["JARVIS_DELEGA"] = "1"
    dati = {"tool_name": "PowerShell", "tool_input": {"command": "ls"}, "agent_type": "esecutore"}
    p = subprocess.run([sys.executable, GUARDIA], input=json.dumps(dati).encode(),
                       capture_output=True, env=env)
    assert p.stdout.strip() == b""


def test_remove_item_porta_chiusa_deny():
    """Senza JARVIS_DELEGA, Remove-Item x, JARVIS_PORTA=1 (chiusa) -> deny."""
    env = os.environ.copy()
    env.pop("JARVIS_DELEGA", None)
    env["JARVIS_PORTA"] = "1"
    dati = {"tool_name": "PowerShell", "tool_input": {"command": "Remove-Item x"}}
    p = subprocess.run([sys.executable, GUARDIA], input=json.dumps(dati).encode(),
                       capture_output=True, env=env)
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_input_non_json_stdout_vuoto():
    """Input non JSON -> nessun errore, stdout vuoto."""
    env = os.environ.copy()
    p = subprocess.run([sys.executable, GUARDIA], input=b"non e json",
                       capture_output=True, env=env)
    assert p.stdout == b""


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
