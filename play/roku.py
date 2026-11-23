"""Roku TVs on the home network: find them, ask what state they are in, and install PLAY's TV app on them.

Everything here is standard library only, so the installed PLAY needs nothing extra:
  find()          SSDP search (what Roku's own phone app does) plus any addresses PLAY already knows
  info(ip)        the TV's name, model, serial, and whether Developer Mode is on (ECP, port 8060)
  dev_app(ip)     the developer app on it now, if any (a Roku holds one at a time)
  install(...)    upload the TV app to the TV's developer installer (port 80, user 'rokudev', one try)
  protect/unprotect   keep a TV's developer password encrypted for this Windows user (DPAPI)
"""
import base64
import ctypes
import io
import ipaddress
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from ctypes import wintypes

ECP_PORT = 8060
SSDP = ('239.255.255.250', 1900)
DEV_USER = 'rokudev'
APP_TITLE = 'PLAY'
MAX_REPLY = 1 << 20          # a TV's answers are a few KB: anything bigger isn't one

# TVs are reached directly, never through a proxy: on Windows an IP address on the home network isn't covered by
# the proxy bypass list, so a system, VPN or office proxy would get the questions (and the password exchange)
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_HOME_NETWORKS = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]


def lan_address(ip):
    """Is ip an address on a home network (where a TV can be): 10.x, 172.16-31.x or 192.168.x?"""
    try:
        a = ipaddress.IPv4Address(str(ip))
    except ValueError:
        return False
    return any(a in n for n in _HOME_NETWORKS)


def app_dir():
    """The TV app's source folder: next to the code, or inside the PyInstaller bundle."""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'roku')


def app_version(folder=None):
    """major.minor.build from the TV app's manifest, as a Roku reports it for an installed app."""
    v = {}
    try:
        with open(os.path.join(folder or app_dir(), 'manifest'), encoding='utf-8') as f:
            for line in f:
                k, _, val = line.strip().partition('=')
                v[k] = val
    except OSError:
        return ''
    return '.'.join(v.get(k, '0') for k in ('major_version', 'minor_version', 'build_version'))


# what the TV app is made of: anything else in the folder (dotfiles, a stray zip) stays out of the package
_APP_FILES = {'source': ('.brs',), 'components': ('.brs', '.xml'), 'images': ('.png', '.jpg')}


def package(folder=None):
    """The TV app as a zip, built in memory from its source folder (the manifest, source and components code,
    images)."""
    folder = folder or app_dir()
    if not os.path.isfile(os.path.join(folder, 'manifest')):
        raise OSError(f'The TV app is missing from this copy of PLAY ({folder})')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(folder, 'manifest'), 'manifest')
        for top, exts in _APP_FILES.items():
            for root, dirs, files in os.walk(os.path.join(folder, top)):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
                for name in sorted(files):
                    if not name.startswith('.') and os.path.splitext(name)[1].lower() in exts:
                        p = os.path.join(root, name)
                        z.write(p, os.path.relpath(p, folder).replace(os.sep, '/'))
    return buf.getvalue()


# ------------------------------------------------------------------ finding TVs
def ssdp_reply_ok(data, ip):
    """Is this SSDP answer, from address ip, a Roku on the local network? Its LOCATION, if it has one, must name the
    same address it came from (a reply that points elsewhere could send PLAY's questions, and a password, anywhere)."""
    text = data.decode('latin-1', 'replace')
    if 'roku:ecp' not in text.lower() or not lan_address(ip):
        return False
    m = re.search(r'(?im)^location:\s*http://([^/:\s]+)', text)
    return not m or m.group(1) == ip


def _ssdp_once(local_ip, wait):
    """Ask one adapter's network for Roku players. Returns the set of their addresses: the address each answer
    came from, only when it is on a local network and the answer's LOCATION (if any) names that same address."""
    msg = ('M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\nMan: "ssdp:discover"\r\n'
           'ST: roku:ecp\r\nMX: 2\r\n\r\n').encode()
    found = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if local_ip:
            s.bind((local_ip, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
        s.settimeout(0.3)
        s.sendto(msg, SSDP)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, (ip, _) = s.recvfrom(2048)
            except socket.timeout:
                continue
            if ssdp_reply_ok(data, ip):
                found.add(ip)
    except OSError:
        pass
    finally:
        s.close()
    return found


def find(local_ips=(), also=(), wait=2.5):
    """Addresses of Roku players: SSDP on every adapter PLAY serves on (all at once), plus 'also' (TVs that
    already talk to PLAY, addresses typed by hand). Local-network IPv4 only, sorted, no repeats."""
    ips = {a for a in also if lan_address(a)}
    lock = threading.Lock()

    def one(local):
        got = _ssdp_once(local, wait)
        with lock:
            ips.update(got)
    threads = [threading.Thread(target=one, args=(local,), daemon=True) for local in (list(local_ips) or [None])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(wait + 2)
    with lock:
        return sorted(ips, key=ipaddress.IPv4Address)


# ------------------------------------------------------------------ asking a TV
class Blocked(OSError):
    """The TV answered but refused: its "Control by mobile apps" setting is off (or Limited)."""


def _read(r):
    data = r.read(MAX_REPLY + 1)
    if len(data) > MAX_REPLY:
        raise OSError('The answer was too long to be from a TV')
    return data


def _get(url, timeout=4):
    try:
        with _DIRECT.open(url, timeout=timeout) as r:
            return _read(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Blocked(f'{e.code}') from e
        raise


def info(ip, timeout=4):
    """{'ip', 'name', 'model', 'serial', 'dev_mode', 'is_tv'} from the TV's ECP device-info.
    Raises Blocked when the TV refuses, OSError when nothing answers."""
    root = ET.fromstring(_get(f'http://{ip}:{ECP_PORT}/query/device-info', timeout))

    def t(tag):
        el = root.find(tag)
        return (el.text or '').strip() if el is not None and el.text else ''
    name = t('user-device-name') or t('friendly-device-name') or t('default-device-name') or ip
    return {'ip': ip, 'name': name, 'model': t('friendly-model-name') or t('model-name'),
            'serial': t('serial-number') or t('device-id') or ip,
            'dev_mode': t('developer-enabled').lower() == 'true', 'is_tv': t('is-tv').lower() == 'true'}


def dev_app(ip, timeout=4):
    """(title, version) of the developer app on the TV, ('', '') when there is none, or None when the TV won't
    say: with "Control by mobile apps" set to Limited the app list is refused, but the app that is open is not."""
    for what in ('apps', 'active-app'):
        try:
            root = ET.fromstring(_get(f'http://{ip}:{ECP_PORT}/query/{what}', timeout))
        except Exception:                          # refused, garbled, cut off: this one doesn't say
            continue
        for a in root.iter('app'):
            if a.get('id') == 'dev':
                return (a.text or '').strip(), a.get('version', '')
        if what == 'apps':
            return '', ''                          # the full list, and no developer app in it
    return None


# ------------------------------------------------------------------ installing
class WrongPassword(OSError):
    pass


class _OnceDigest(urllib.request.HTTPDigestAuthHandler):
    """Digest auth with one try: a second refusal is the wrong password (not five more uploads of the app)."""

    def http_error_401(self, req, fp, code, msg, headers):
        if req.unredirected_hdrs.get('Authorization'):
            raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        return super().http_error_401(req, fp, code, msg, headers)


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, data, ctype) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n'.encode())
        out.write(data)
        out.write(b'\r\n')
    out.write(f'--{boundary}--\r\n'.encode())
    return out.getvalue(), f'multipart/form-data; boundary={boundary}'


def install(ip, password, zip_bytes, timeout=120, port=80):
    """Upload the TV app. Returns the TV's message ('Install Success.' ...). Raises WrongPassword when the TV
    refuses the password, OSError when the install fails or nothing answers. The app opens on the TV."""
    url = f'http://{ip}:{port}/plugin_install' if port != 80 else f'http://{ip}/plugin_install'
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, url, DEV_USER, password)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _OnceDigest(pm))
    body, ctype = _multipart({'mysubmit': 'Install'}, {'archive': ('PLAY-roku.zip', zip_bytes, 'application/zip')})
    req = urllib.request.Request(url, data=body, headers={'Content-Type': ctype}, method='POST')
    try:
        with opener.open(req, timeout=timeout) as r:
            text = _read(r).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise WrongPassword('The TV did not accept that developer password') from e
        raise OSError(f'The TV answered {e.code}') from e
    msgs = re.findall(r'"text":"([^"]+)"', text) or re.findall(r'<font color="red">(.*?)</font>', text)
    msg = ' '.join(m.strip() for m in msgs if m.strip())
    low = msg.lower()
    if 'success' in low or 'identical' in low:
        return msg
    raise OSError(msg or 'The TV answered in a way PLAY doesn\'t recognise')


# ------------------------------------------------------------------ passwords (DPAPI, this Windows user only)
class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _crypt(fn, data):
    src = ctypes.create_string_buffer(data, len(data))
    inp, out = _Blob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char))), _Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    f = getattr(crypt32, fn)
    if not f(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):   # 0x1: no UI
        raise OSError(f'{fn} failed')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(password):
    return base64.b64encode(_crypt('CryptProtectData', password.encode('utf-8'))).decode('ascii')


def unprotect(token):
    try:
        return _crypt('CryptUnprotectData', base64.b64decode(token)).decode('utf-8')
    except (OSError, ValueError, TypeError):
        return None
