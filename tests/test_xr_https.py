"""Prove dell'HTTPS di Jarvis XR (2026-11-24): certificato fatto in casa, server TLS con la stretta di mano nel thread,
inoltro di /jarvis/* a Jarvis (risposte, eventi in diretta, video a pezzi). python -m pytest tests/test_xr_https.py"""
import ctypes
import http.client
import json
import ssl
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
pytest.importorskip("cryptography")
import xr_https  # noqa: E402


def _pannello():
    """pannello.py usa Windows già all'import: qui con user32 finto, solo per prendere il gestore HTTP."""
    with mock.patch.object(ctypes, "windll", mock.MagicMock(), create=True), \
            mock.patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
        sys.modules.pop("pannello", None)
        import pannello
    return pannello


def test_certificato_e_rinnovo():
    d = Path(tempfile.mkdtemp())
    crt, key = xr_https.certificato(["192.168.1.50"], d)
    from cryptography import x509
    c = x509.load_pem_x509_certificate(crt.read_bytes())
    alt = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "192.168.1.50" in [str(i) for i in alt.get_values_for_type(x509.IPAddress)]
    prima = crt.read_bytes()
    xr_https.certificato(["192.168.1.50"], d)
    assert crt.read_bytes() == prima                       # stessi indirizzi: stesso certificato
    xr_https.certificato(["192.168.1.77"], d)
    assert crt.read_bytes() != prima                       # IP cambiato: rifatto


class _JarvisFinto(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/jarvis/flusso"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.close_connection = True   # come Jarvis: il flusso finisce quando si chiude
            for i in range(3):
                self.wfile.write(f"data: {json.dumps({'n': i, 'token': self.headers.get('X-Token')})}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.15)
            return
        if self.path.startswith("/jarvis/file/dati"):
            dati = bytes(range(256)) * 4
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", f"bytes 10-19/{len(dati)}")
            self.send_header("Content-Length", "10")
            self.end_headers()
            self.wfile.write(dati[10:20] if self.headers.get("Range") == "bytes=10-19" else b"sbagliato!")
            return
        corpo = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        corpo = json.dumps({"ricevuto": len(self.rfile.read(n)), "tipo": self.headers.get("Content-Type")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def test_https_e_inoltro_a_jarvis():
    p = _pannello()
    jarvis = ThreadingHTTPServer(("127.0.0.1", 0), _JarvisFinto)
    threading.Thread(target=jarvis.serve_forever, daemon=True).start()
    srv = p.ServerUnico(("127.0.0.1", 0), p.Gestore)
    srv.daemon_threads = True
    xr_https.avvolgi(srv, xr_https.contesto(["127.0.0.1"], Path(tempfile.mkdtemp())))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    porta = srv.server_address[1]
    nessun_controllo = ssl._create_unverified_context()   # come il visore dopo "Procedi"
    with mock.patch.object(p, "PORTA_JARVIS", jarvis.server_address[1]):
        # un client che si collega e non fa la stretta di mano non blocca gli altri
        import socket
        lento = socket.create_connection(("127.0.0.1", porta))
        c = http.client.HTTPSConnection("127.0.0.1", porta, context=nessun_controllo, timeout=10)
        c.request("GET", "/jarvis/stato", headers={"X-Token": "k"})
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read()) == {"ok": True}
        # eventi in diretta: arrivano man mano, non tutti alla fine
        c = http.client.HTTPSConnection("127.0.0.1", porta, context=nessun_controllo, timeout=10)
        c.request("GET", "/jarvis/flusso?xr=1", headers={"X-Token": "chiave"})
        r = c.getresponse()
        t0 = time.time()
        primo = r.readline()
        assert b'"n": 0' in primo and b'"token": "chiave"' in primo and time.time() - t0 < 0.3
        resto = r.read()
        assert b'"n": 2' in resto
        # POST con un corpo binario (la voce del visore)
        c = http.client.HTTPSConnection("127.0.0.1", porta, context=nessun_controllo, timeout=10)
        c.request("POST", "/jarvis/voce", body=b"\x00" * 32000, headers={"X-Token": "k", "Content-Type": "application/octet-stream"})
        assert json.loads(c.getresponse().read()) == {"ricevuto": 32000, "tipo": "application/octet-stream"}
        # video a pezzi
        c = http.client.HTTPSConnection("127.0.0.1", porta, context=nessun_controllo, timeout=10)
        c.request("GET", "/jarvis/file/dati?percorso=x", headers={"X-Token": "k", "Range": "bytes=10-19"})
        r = c.getresponse()
        assert r.status == 206 and r.getheader("Content-Range") == "bytes 10-19/1024" and r.read() == bytes(range(10, 20))
        lento.close()
    with mock.patch.object(p, "PORTA_JARVIS", 1):   # Jarvis spento
        c = http.client.HTTPSConnection("127.0.0.1", porta, context=nessun_controllo, timeout=10)
        c.request("GET", "/jarvis/stato")
        assert c.getresponse().status == 502
    srv.shutdown()
    jarvis.shutdown()
