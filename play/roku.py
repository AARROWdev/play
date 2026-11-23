"""Roku TVs on the home network: find them, ask what state they are in, and install PLAY's TV app on them.

Everything here is standard library only, so the installed PLAY needs nothing extra:
  find()          SSDP search (what Roku's own phone app does) plus any addresses PLAY already knows
  info(ip)        the TV's name, model, serial, and whether Developer Mode is on (ECP, port 8060)
  dev_app(ip)     the developer app on it now, if any (a Roku holds one at a time)
  install(...)    upload the TV app to the TV's developer installer (port 80, user 'rokudev')
  protect/unprotect   keep a TV's developer password encrypted for this Windows user (DPAPI)
"""
import base64
import ctypes
import io
import os
import re
import socket
import sys
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

# the remote-control steps that turn Developer Mode on (Roku's own sequence)
DEV_MODE_KEYS = 'Home, Home, Home, Up, Up, Right, Left, Right, Left, Right'


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


def package(folder=None):
    """The TV app as a zip, built in memory from its source folder."""
    folder = folder or app_dir()
    if not os.path.isfile(os.path.join(folder, 'manifest')):
        raise OSError(f'The TV app is missing from this copy of PLAY ({folder})')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                p = os.path.join(root, name)
                z.write(p, os.path.relpath(p, folder).replace(os.sep, '/'))
    return buf.getvalue()


# ------------------------------------------------------------------ finding TVs
def _ssdp_once(local_ip, wait):
    """Ask one adapter's network for Roku players. Returns {ip: serial-or-''}."""
    msg = ('M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\nMan: "ssdp:discover"\r\n'
           'ST: roku:ecp\r\nMX: 2\r\n\r\n').encode()
    found = {}
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
            text = data.decode('latin-1', 'replace')
            if 'roku:ecp' not in text.lower():
                continue
            m = re.search(r'(?im)^location:\s*http://([0-9.]+):', text)
            usn = re.search(r'(?im)^usn:\s*uuid:roku:ecp:(\S+)', text)
            found[m.group(1) if m else ip] = usn.group(1) if usn else ''
    except OSError:
        pass
    finally:
        s.close()
    return found


def find(local_ips=(), also=(), wait=2.5):
    """Addresses of Roku players: SSDP on every adapter PLAY serves on, plus 'also' (TVs that already talk to
    PLAY, addresses typed by hand). Sorted, no repeats."""
    ips = set(a for a in also if a)
    for local in (list(local_ips) or [None]):
        ips.update(_ssdp_once(local, wait))
    return sorted(ips, key=lambda a: tuple(int(x) if x.isdigit() else 0 for x in a.split('.')))


# ------------------------------------------------------------------ asking a TV
class Blocked(OSError):
    """The TV answered but refused: its "Control by mobile apps" setting is off (or Limited)."""


def _get(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
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
        except (OSError, ET.ParseError):
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
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm))
    body, ctype = _multipart({'mysubmit': 'Install'}, {'archive': ('PLAY-roku.zip', zip_bytes, 'application/zip')})
    req = urllib.request.Request(url, data=body, headers={'Content-Type': ctype}, method='POST')
    try:
        with opener.open(req, timeout=timeout) as r:
            text = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise WrongPassword('The TV did not accept that developer password') from e
        raise OSError(f'The TV answered {e.code}') from e
    msgs = re.findall(r'"text":"([^"]+)"', text) or re.findall(r'<font color="red">(.*?)</font>', text)
    msg = ' '.join(m.strip() for m in msgs if m.strip()) or 'Installed'
    low = msg.lower()
    if 'success' in low or 'identical' in low:
        return msg
    raise OSError(msg)


def launch(ip, timeout=4):
    """Open the developer app (PLAY) on the TV."""
    req = urllib.request.Request(f'http://{ip}:{ECP_PORT}/launch/dev', data=b'', method='POST')
    with urllib.request.urlopen(req, timeout=timeout):
        pass


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
