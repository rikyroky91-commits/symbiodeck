"""Prove delle sessioni di Esecutore (registro, _righe, conta_domande, continuata_nell_app)."""
import json
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from jarvis.esecutore import Esecutore  # noqa: E402


def _scrivi_righe(percorso, righe):
    """Scrive righe JSONL in un file."""
    with open(percorso, "w", encoding="utf-8") as f:
        for riga in righe:
            f.write(riga + "\n")


def test_conta_domande_base():
    """Due righe user e una assistant -> conta_domande()==2."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            json.dumps({"type": "user", "message": {"content": "ciao"}}),
            json.dumps({"type": "user", "message": {"content": "come stai?"}}),
            json.dumps({"type": "assistant", "message": {"content": "tutto bene"}}),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        assert e.conta_domande() == 2


def test_content_lista_non_conta():
    """Una riga user con content lista (risultato di strumento) non conta."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            json.dumps({"type": "user", "message": {"content": "ciao"}}, ensure_ascii=False),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "risultato"}]}}, ensure_ascii=False),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        assert e.conta_domande() == 1


def test_task_notification_non_conta():
    """Una riga user con content che inizia con <task-notification> non conta."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            json.dumps({"type": "user", "message": {"content": "ciao"}}, ensure_ascii=False),
            json.dumps({"type": "user", "message": {"content": "<task-notification>fine lavoro"}}, ensure_ascii=False),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        assert e.conta_domande() == 1


def test_righe_non_json_ignore():
    """Righe non JSON vengono ignorate."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            "questa non e json",
            json.dumps({"type": "user", "message": {"content": "ciao"}}, ensure_ascii=False),
            "{malformato",
            json.dumps({"type": "user", "message": {"content": "come stai?"}}, ensure_ascii=False),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        assert e.conta_domande() == 2


def test_continuata_nell_app_false_poi_true():
    """e._domande_note=2 e il file ha 2 domande -> False; aggiungi una domanda -> True."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            json.dumps({"type": "user", "message": {"content": "ciao"}}, ensure_ascii=False),
            json.dumps({"type": "user", "message": {"content": "come stai?"}}, ensure_ascii=False),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        e._domande_note = 2
        assert e.continuata_nell_app() is False
        # aggiungi una domanda
        with open(registro_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "nuova domanda"}}, ensure_ascii=False) + "\n")
        assert e.continuata_nell_app() is True


def test_continuata_nell_app_domande_note_none():
    """e._domande_note=None -> False."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        registro_path = tmp / "sessione.jsonl"
        _scrivi_righe(registro_path, [
            json.dumps({"type": "user", "message": {"content": "ciao"}}, ensure_ascii=False),
        ])
        e = Esecutore(dati=tmp, impostazioni={}, porta=0, chiave_interna="", file_config=tmp / "x.json")
        e.registro = lambda sessione=None: registro_path
        e.sessione = "s1"
        e._domande_note = None
        assert e.continuata_nell_app() is False


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            f()
            print("ok", nome)
