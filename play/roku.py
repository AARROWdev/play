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
import http.client
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

from .server import home_address as lan_address   # one rule for "on the home network"

ECP_PORT = 8060
SSDP = ('239.255.255.250', 1900)
DEV_USER = 'rokudev'
APP_TITLE = 'PLAY'
MAX_REPLY = 1 << 20          # a TV's answers are a few KB: anything bigger isn't one

DEADLINE = 30               # seconds for a whole question and its answer, however slowly they go


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A TV answers where it was asked: a redirect (to another host, to this PC) is refused, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# TVs are reached directly, never through a proxy (on Windows an IP address on the home network isn't covered by
# the proxy bypass list, so a system, VPN or office proxy would get the questions and the password exchange), and
# never by a redirect
def opener(*handlers):
    """An opener that goes straight to the TV: no proxy (whatever the system or environment says) and no
    redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), _DeadlineHTTP(), *handlers)



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
    """The whole answer, at most MAX_REPLY bytes."""
    chunks, n = [], 0
    while True:
        chunk = r.read1(65536) if hasattr(r, 'read1') else r.read(65536)
        if not chunk:
            return b''.join(chunks)
        n += len(chunk)
        if n > MAX_REPLY:
            raise _OwnError('The answer was too long to be from a TV')
        chunks.append(chunk)


class _OwnError(OSError):
    """An answer PLAY judged (too long, a challenge it can't use, a refused password): passed on as it is."""


UNRECOGNISED = "The TV asked for its password in a way PLAY doesn't recognise"


class TookTooLong(_OwnError):
    """The time was up. uploaded: the app had been sent in full, with a password, before it was (so it may or may
    not be on the TV: nothing says whether the TV took it)."""

    def __init__(self, msg, uploaded=False):
        super().__init__(msg)
        self.uploaded = uploaded


class _Deadline:
    """The connections one call opens, closed when its time is up: whatever the call is doing (sending the app,
    waiting for headers, reading a reply that trickles in) fails at once, in the call's own thread. Nothing goes on
    in the background afterwards. A call inside a call reports to the outer one too (its connections, what it
    sent)."""

    def __init__(self, seconds, outer=None):
        self.lock, self.socks, self.expired, self.outer = threading.Lock(), [], False, outer
        self.end = time.monotonic() + seconds
        self.cut = False                       # the cut-off ended this call (closed its connection, or its connect)
        self.uploaded = False                  # the latest request: a body, with a password, went out in full

    def left(self):
        return max(0.05, self.end - time.monotonic())

    def add(self, sock):
        with self.lock:
            if self.expired:
                self.cut = True
                _hard_close(sock)
            else:
                self.socks.append(sock)
        if self.outer is not None:
            self.outer.add(sock)

    def sent(self, uploaded):
        self.uploaded = uploaded
        if self.outer is not None:
            self.outer.sent(uploaded)

    def expire(self):
        with self.lock:
            self.expired = True
            self.cut = self.cut or bool(self.socks)
            for s in self.socks:
                _hard_close(s)


class _Watchdog:
    """One thread for every deadline (not one per call): it sleeps until the soonest one, and expires it unless
    its call finished first."""

    def __init__(self, idle=60):
        self.cond, self.pending, self.thread, self.idle = threading.Condition(), [], None, idle

    def watch(self, deadline):
        with self.cond:
            self.pending.append(deadline)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name='play-deadline', daemon=True)
                self.thread.start()
            self.cond.notify()

    def finished(self, deadline):
        with self.cond:                        # (under the watchdog's lock: it can't be expiring it meanwhile)
            if deadline in self.pending:
                self.pending.remove(deadline)

    def _run(self):
        with self.cond:
            while True:
                now = time.monotonic()
                for d in [d for d in self.pending if d.end <= now]:
                    self.pending.remove(d)
                    d.expire()
                if self.pending:
                    self.cond.wait(min(d.end for d in self.pending) - now)
                elif not self.cond.wait_for(lambda: self.pending, self.idle):   # nothing to watch for a while
                    self.thread = None
                    return


_watchdog = _Watchdog()


def _hard_close(sock):
    """Close a socket now, from the watchdog, while its call may be waiting on it. On Windows a waiting read is
    woken by neither shutdown() nor close() while the socket's reader (makefile) still holds it; closing the
    handle itself does, and that is what Windows documents for cancelling a blocking call. The socket object lets
    go of the handle first (detach), so its owner can't use or close that number again afterwards: any further
    call on it fails at once. What remains is a call already inside Windows on that handle, which Windows ends
    with an error."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        fd = sock.detach()
    except OSError:
        return
    if fd is not None and fd >= 0:
        try:
            socket.close(fd)
        except OSError:
            pass


_current = threading.local()            # the deadline of the call running in this thread


class _Connection(http.client.HTTPConnection):
    def connect(self):
        deadline = getattr(_current, 'deadline', None)
        if deadline is not None and (deadline.expired or time.monotonic() >= deadline.end):
            deadline.cut = True                # time is up: no new connection to the TV (on any path)
            raise socket.timeout('The time ran out before connecting')
        if deadline is not None:               # connecting counts too: a little past the time left, so the
            limit = deadline.left() + 0.1      # watchdog has always called time first
            self.timeout = min(self.timeout, limit) if self.timeout else limit
        try:
            super().connect()
        except (TimeoutError, socket.timeout):
            if deadline is not None and (deadline.expired or time.monotonic() >= deadline.end):
                deadline.cut = True            # it was the time limit that ended the connect
            raise
        if deadline is not None:
            deadline.add(self.sock)

    def request(self, method, url, body=None, headers={}, **kw):
        deadline = getattr(_current, 'deadline', None)
        if deadline is not None:
            deadline.sent(False)               # this request's own
        super().request(method, url, body, headers, **kw)
        if deadline is not None and body and any(k.lower() == 'authorization' for k in headers):
            deadline.sent(True)


class _DeadlineHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Connection, req)


_DIRECT = opener()


def within(seconds, fn, *args):
    """fn(*args), given `seconds` in all: connecting, sending, the answer's headers and body. A request that
    trickles along (a byte at a time stays under every per-step timeout) is cut off by closing its connections,
    so it can't hold a worker, or the wizard waiting for it, and nothing of it carries on. Raises TookTooLong
    for what the cut-off caused (an answer the TV gave in time, like a 401, is passed on as it is)."""
    outer = getattr(_current, 'deadline', None)
    deadline = _Deadline(min(seconds, outer.left()) if outer is not None else seconds, outer)   # never past it
    _current.deadline = deadline
    _watchdog.watch(deadline)
    try:
        return fn(*args)
    except (urllib.error.HTTPError, _OwnError):
        raise                                  # the TV's own answer, or PLAY's verdict on one
    except (OSError, http.client.HTTPException) as e:
        if deadline.cut:                       # what the cut-off itself caused
            raise TookTooLong('The TV took too long to answer', deadline.uploaded) from e
        raise
    finally:
        _watchdog.finished(deadline)
        _current.deadline = outer


def _get(url, timeout=4):
    def fetch():
        with _DIRECT.open(url, timeout=timeout) as r:
            return _read(r)
    try:
        return within(DEADLINE, fetch)
    except urllib.error.HTTPError as e:
        e.close()                              # (a Limited TV refuses every poll: don't keep each answer open)
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
class WrongPassword(_OwnError):
    pass


class Unrecognised(_OwnError):
    pass


class _OnceDigest(urllib.request.HTTPDigestAuthHandler):
    """Digest auth with one try: a refusal of the password is the wrong password (not five more uploads of the
    app). The one exception is a "stale" nonce (the TV wants a fresh one, the password was fine): one retry."""

    def http_error_401(self, req, fp, code, msg, headers):
        fp.close()                                              # this answer is done with (its body isn't needed)
        chal = _challenge(headers.get('WWW-Authenticate'))    # parsed once, here
        if req.unredirected_hdrs.get('Authorization'):          # the password went: this is the TV's answer to it
            if chal.get('stale', '').lower() != 'true' or getattr(req, '_play_stale_retry', False):
                raise urllib.error.HTTPError(req.full_url, code, msg, headers, None)   # refused
            req._play_stale_retry = True
        value = self.get_authorization(req, chal)
        if not value:                                           # a challenge it can't answer (NTLM, Basic, blank,
            raise Unrecognised(UNRECOGNISED)                    # no realm): not how a Roku asks, nor a refusal
        req.add_unredirected_header(self.auth_header, f'Digest {value}')
        return self.parent.open(req, timeout=req.timeout)       # (its own errors are its own)

    def get_authorization(self, req, chal):
        try:
            return super().get_authorization(req, chal)
        except (KeyError, ValueError, IndexError, TypeError, urllib.error.URLError) as e:   # no realm, auth-int
            raise Unrecognised(UNRECOGNISED) from e


def _challenge(value):
    """A Digest challenge's fields ({'realm': ..., 'nonce': ..., 'stale': ...}), {} when it isn't one."""
    value = value or ''
    if not value.lower().startswith('digest '):
        return {}
    try:
        return urllib.request.parse_keqv_list(filter(None, urllib.request.parse_http_list(value[7:])))
    except (ValueError, IndexError):
        return {}


def _authorization(handler, url, timeout):
    """The Authorization header for a POST to url, from a challenge fetched with a small GET first (so the app
    itself is uploaded once). None when that didn't work out (no challenge, an odd one, or no answer): then the
    POST gets its challenge the usual way."""
    try:
        with _DIRECT.open(urllib.request.Request(url, method='GET'), timeout=timeout) as r:
            _read(r)
        return None                                  # no password asked: nothing to add
    except urllib.error.HTTPError as e:
        chal = _challenge(e.headers.get('WWW-Authenticate'))
        e.close()
        if e.code != 401 or not chal:
            return None
    except OSError:                                   # no answer to the GET: let the POST find out
        return None
    auth = handler.get_authorization(urllib.request.Request(url, method='POST'), chal)   # (Unrecognised: its own)
    return f'Digest {auth}' if auth else None


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
    if not password.isascii():                   # a Roku's developer password is plain ASCII: this one can't be it
        raise WrongPassword('A Roku developer password uses only plain letters, digits and symbols')
    url = f'http://{ip}:{port}/plugin_install' if port != 80 else f'http://{ip}/plugin_install'
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, url, DEV_USER, password)
    handler = _OnceDigest(pm)
    upload = opener(handler)
    body, ctype = _multipart({'mysubmit': 'Install'}, {'archive': ('PLAY-roku.zip', zip_bytes, 'application/zip')})
    req = urllib.request.Request(url, data=body, headers={'Content-Type': ctype}, method='POST')

    def send():
        auth = _authorization(handler, url, min(timeout, 15))
        if auth:
            req.add_unredirected_header('Authorization', auth)
        with upload.open(req, timeout=timeout) as r:
            return _read(r).decode('utf-8', 'replace')
    try:
        text = within(timeout, send)
    except urllib.error.HTTPError as e:
        e.close()                                    # its connection is done with, whatever it meant
        if e.code == 401 and not req.unredirected_hdrs.get('Authorization'):   # never got as far as a password
            raise Unrecognised(UNRECOGNISED) from e
        if e.code == 401:
            raise WrongPassword('The TV did not accept that developer password') from e
        raise OSError(f'The TV answered {e.code}') from e
    msgs = re.findall(r'"text":"([^"]+)"', text) or re.findall(r'<font color="red">(.*?)</font>', text)
    msg = ' '.join(m.strip() for m in msgs if m.strip())
    low = msg.lower()
    if not re.search(r'\b(fail|failure|failed|error|unsuccessful)', low) and \
            re.search(r'\b(success|identical)\b', low):
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
