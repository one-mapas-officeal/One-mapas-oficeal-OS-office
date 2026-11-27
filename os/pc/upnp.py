"""UPnP (IGD): pede ao roteador para abrir uma porta para este PC. Só usa a biblioteca padrão.

    from upnp import abrir_porta
    r = abrir_porta(8090)   # {"ok": True, "ip_externo": "...", "ip_local": "...", "erro": None}
"""
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SSDP = ("239.255.255.250", 1900)
TIPOS = ("urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
         "urn:schemas-upnp-org:service:WANPPPConnection:1")


def _descobrir(timeout=3):
    """Procura o roteador na rede local (SSDP). Devolve as URLs de descrição encontradas."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    locais = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        s.sendto(msg, SSDP)
        while True:
            try:
                dados, _ = s.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", dados)
            if m and m.group(1).decode() not in locais:
                locais.append(m.group(1).decode())
    finally:
        s.close()
    return locais


def _servico(local):
    with urllib.request.urlopen(local, timeout=5) as r:
        xml = r.read()
    raiz = ET.fromstring(xml)
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    base = raiz.findtext("d:URLBase", default="", namespaces=ns) or local
    for sv in raiz.iter("{urn:schemas-upnp-org:device-1-0}service"):
        tipo = sv.findtext("d:serviceType", default="", namespaces=ns)
        if tipo in TIPOS:
            return urllib.parse.urljoin(base, sv.findtext("d:controlURL", default="", namespaces=ns)), tipo
    return None


def _soap(url, tipo, acao, args):
    corpo = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{acao} xmlns:u="{tipo}">{corpo}</u:{acao}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(url, data=env, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{tipo}#{acao}"'})
    with urllib.request.urlopen(req, timeout=6) as r:
        return r.read().decode(errors="replace")


def ip_local_para(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1900))
        return s.getsockname()[0]
    finally:
        s.close()


def abrir_porta(porta, descricao="OneMapas OS", duracao=7200):
    res = {"ok": False, "ip_externo": None, "ip_local": None, "erro": None}
    try:
        locais = _descobrir()
        if not locais:
            res["erro"] = "roteador sem UPnP (ou UPnP desligado)"
            return res
        for local in locais:
            sv = _servico(local)
            if not sv:
                continue
            url, tipo = sv
            ip_local = ip_local_para(urllib.parse.urlsplit(url).hostname)
            res["ip_local"] = ip_local
            try:
                m = re.search(r"<NewExternalIPAddress>([^<]*)<", _soap(url, tipo, "GetExternalIPAddress", {}))
                res["ip_externo"] = m.group(1) if m else None
            except Exception:
                pass
            args = {"NewRemoteHost": "", "NewExternalPort": porta, "NewProtocol": "TCP", "NewInternalPort": porta,
                    "NewInternalClient": ip_local, "NewEnabled": 1, "NewPortMappingDescription": descricao,
                    "NewLeaseDuration": duracao}
            for tentativa in (duracao, 0):  # alguns roteadores só aceitam duração 0 (permanente)
                try:
                    _soap(url, tipo, "AddPortMapping", {**args, "NewLeaseDuration": tentativa})
                    res["ok"] = True
                    return res
                except Exception as e:
                    res["erro"] = f"o roteador recusou: {e.__class__.__name__}"
        res["erro"] = res["erro"] or "roteador sem serviço de porta (WANIPConnection)"
    except Exception as e:
        res["erro"] = f"{e.__class__.__name__}: {e}"[:160]
    return res
