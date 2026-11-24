"""HTTPS in rete di casa per Jarvis XR (il Quest 3 apre la realtà mista e il microfono solo da pagine https:// o da
localhost). Il pannello genera da solo un certificato per gli indirizzi del PC e serve le stesse pagine e API anche
sulla porta 47443 in HTTPS: niente cavo, niente Tailscale. Il visore la prima volta avvisa che il certificato non è
di un'autorità ("Avanzate" > "Procedi"), poi se lo ricorda. Il certificato si rifà da solo se cambiano gli indirizzi
del PC (e allora il visore lo richiede una volta).

File in server/xr-certificato/ (fuori da git). Serve il pacchetto `cryptography` (in requirements-jarvis.txt).
"""
import datetime
import ipaddress
import json
import socket
import ssl
from pathlib import Path

PORTA_XR = 47443
CARTELLA = Path(__file__).resolve().parent / "xr-certificato"


def _nomi(indirizzi):
    nomi = {"localhost", socket.gethostname().lower()}
    ip = {"127.0.0.1"} | {str(ipaddress.ip_address(a)) for a in indirizzi}
    return sorted(nomi), sorted(ip)


def certificato(indirizzi, cartella=CARTELLA):
    """(file del certificato, file della chiave), rifatti se mancano o se gli indirizzi del PC sono cambiati."""
    cartella.mkdir(parents=True, exist_ok=True)
    crt, key, info = cartella / "jarvis-xr.crt", cartella / "jarvis-xr.key", cartella / "indirizzi.json"
    nomi, ip = _nomi(indirizzi)
    try:
        if crt.exists() and key.exists() and json.loads(info.read_text(encoding="utf-8")) == {"nomi": nomi, "ip": ip}:
            return crt, key
    except (OSError, ValueError):
        pass
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis XR (" + nomi[-1] + ")")])
    ora = datetime.datetime.now(datetime.timezone.utc)
    alt = [x509.DNSName(n) for n in nomi] + [x509.IPAddress(ipaddress.ip_address(a)) for a in ip]
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chiave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ora - datetime.timedelta(days=1)).not_valid_after(ora + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(chiave, hashes.SHA256()))
    key.write_bytes(chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                         serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    info.write_text(json.dumps({"nomi": nomi, "ip": ip}), encoding="utf-8")
    return crt, key


def contesto(indirizzi, cartella=CARTELLA):
    crt, key = certificato(indirizzi, cartella)
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.load_cert_chain(crt, key)
    return c


def avvolgi(server, ctx):
    """Il server ascolta in TLS; la stretta di mano si fa nel thread della richiesta (un client lento non blocca
    gli altri)."""
    server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    finish = server.finish_request

    def finish_request(request, client_address):
        try:
            request.settimeout(15)
            request.do_handshake()
            request.settimeout(None)
        except (ssl.SSLError, OSError):
            return   # certificato rifiutato, client sparito: niente da rispondere
        finish(request, client_address)
    server.finish_request = finish_request
    return server
