"""The movie server the Roku app talks to, plus LAN discovery so the TV finds this PC by itself.

    GET  /api/info              -> {"name": "PLAY", "pc": <computer name>, "version", "accent"}
    GET  /api/tv?side=home|kids -> the movies (collections as single entries) and shows for that side of the TV app;
                                   503 {"busy": "reading"} while PLAY reads the folders for the first time
    GET  /media/<id>            -> the file, with HTTP Range support (the Roku seeks with Range requests)
    UDP  8601  "PLAY-DISCOVER"   -> {"name": "PLAY", "pc": ..., "port": <http port>} back to the sender

Only files found in the library's folders can be streamed: ids are looked up, never turned into paths.
"""
import ipaddress
import json
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import library

VERSION = '1.0'
DISCOVERY_PORT = 8601
DISCOVER = b'PLAY-DISCOVER'
CHUNK = 1 << 20
TYPES = {'mkv': 'video/x-matroska', 'mp4': 'video/mp4', 'mov': 'video/quicktime', 'ts': 'video/mp2t', 'm2ts': 'video/mp2t',
         'webm': 'video/webm', 'avi': 'video/x-msvideo'}


class _HTTP(ThreadingHTTPServer):
    # Python's default SO_REUSEADDR lets a second program bind the same port on Windows; take it exclusively.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_ips_cache = (0.0, [])


def lan_ips(max_age=30.0):
    """This PC's private IPv4 addresses (what a TV would type), most likely first. A Tailscale/CGNAT address
    (100.64.0.0/10) is never offered. A VPN's private address can't be told from the home network's by its number,
    so the window shows the first two: home routers mostly use 192.168.x.x, and that comes first. Cached for 30 s:
    the window asks every 1.5 s."""
    global _ips_cache
    t, ips = _ips_cache
    if time.monotonic() - t < max_age and t:
        return list(ips)
    ips = _find_lan_ips()
    _ips_cache = (time.monotonic(), ips)
    return list(ips)


def _find_lan_ips():
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    ips = []
    for ip in found:
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local and a not in ipaddress.ip_network('100.64.0.0/10'):
            ips.append(ip)
    primary = _default_route_ip()                     # the adapter the PC goes out on (home, unless a VPN is up)
    return sorted(ips, key=lambda ip: (not ip.startswith('192.168.'), ip != primary, ip.startswith('172.'),
                                       ipaddress.ip_address(ip)))


def _default_route_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))                   # no packet is sent; this asks which adapter would be used
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def lan_ip():
    ips = lan_ips()
    return ips[0] if ips else None


# Home-router DNS suffixes a TV may know this PC by (pc.home.arpa, pc.fritz.box ...), besides the bare name
LAN_SUFFIXES = ('local', 'lan', 'home', 'home.arpa', 'internal', 'localdomain', 'fritz.box', 'attlocal.net',
                'mshome.net', 'domain', 'router', 'gateway')


def host_allowed(host):
    """The Host header a request may carry: an IP address (what the Roku sends), localhost, or this PC's name.
    Anything else is a web page that pointed its own domain at this PC (DNS rebinding) and is refused."""
    if not host:
        return True                                    # HTTP/1.0 clients may omit it; a browser never does
    h = host.strip().lower()
    if h.startswith('['):                              # [v6]:port
        h = h[1:h.find(']')] if ']' in h else h[1:]
    elif h.count(':') == 1:
        h = h.rsplit(':', 1)[0]
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    h = h.rstrip('.')
    pc = socket.gethostname().lower()
    names = {'localhost', pc, socket.getfqdn().lower().rstrip('.')}
    return h in names or any(h == f'{pc}.{suffix}' for suffix in LAN_SUFFIXES)


class PlayServer:
    def __init__(self, lib, port=library.DEFAULT_PORT, accent=None, discovery_port=DISCOVERY_PORT):
        self.lib = lib
        self.port = port
        self.discovery_port = discovery_port
        self.accent = accent or (lambda: library.skins.accent())
        self.clients = {}            # ip -> last request time, for "TVs connected"
        self.refused = {}            # Host -> last time a request with it was turned away (shown in the window)
        self.refused_max = 20
        self._clients_lock = threading.Lock()
        self.httpd = None
        self.udp = None
        self.error = None
        self.discovery_error = None  # set when UDP 8601 couldn't be opened: TVs must be given the address by hand

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        """Start HTTP + discovery threads. Returns False (and sets .error) if the port is taken."""
        self.error = None
        handler = type('H', (_Handler,), {'srv': self})
        try:
            self.httpd = _HTTP(('0.0.0.0', self.port), handler)
        except OSError as e:
            self.httpd = None
            self.error = f'Port {self.port} is in use ({e.strerror or e}). Pick another port in Settings.'
            return False
        self.port = self.httpd.server_address[1]          # the real port when 0 (tests) was asked for
        threading.Thread(target=self.httpd.serve_forever, name='play-http', daemon=True).start()
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_error = None
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                u.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            u.bind(('0.0.0.0', self.discovery_port))
            self.discovery_port = u.getsockname()[1]
            u.settimeout(0.5)
            self.udp = u
            threading.Thread(target=self._discovery, name='play-discovery', daemon=True).start()
        except OSError as e:          # another program holds the port; TVs can still use the address by hand
            u.close()
            self.udp = None
            self.discovery_error = (f'UDP port {self.discovery_port} is in use ({e.strerror or e}), so TVs can\'t '
                                    'find this PC by themselves. Type its address on the TV instead.')
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.udp:
            u, self.udp = self.udp, None
            u.close()

    @property
    def running(self):
        return self.httpd is not None

    def seen(self, ip):
        with self._clients_lock:
            self.clients[ip] = time.monotonic()

    def tvs(self, within=600):
        now = time.monotonic()
        with self._clients_lock:
            return sorted(ip for ip, t in self.clients.items() if now - t < within)

    def refuse(self, host):
        """Remember a refused Host (printable characters only, short), keeping the 20 most recent."""
        h = ''.join(c for c in str(host) if c.isprintable())[:100]
        with self._clients_lock:
            self.refused[h] = time.monotonic()
            while len(self.refused) > self.refused_max:
                del self.refused[min(self.refused, key=self.refused.get)]

    def refused_hosts(self, within=600):
        """Refused Host names seen in the last 10 minutes, most recent last."""
        now = time.monotonic()
        with self._clients_lock:
            return [h for h, t in sorted(self.refused.items(), key=lambda kv: kv[1]) if now - t < within]

    def _discovery(self):
        u = self.udp
        while self.udp is u:
            if not self._discovery_once(u):
                time.sleep(0.05)                     # an error that keeps coming mustn't spin a core

    def _discovery_once(self, u):
        """Answer one packet. False after an error: a stray packet (a reset left by a TV that already went away) is
        no reason to stop answering, so the loop goes on unless the socket was closed."""
        try:
            data, addr = u.recvfrom(65535)
        except socket.timeout:
            return True
        except OSError:
            return False
        if data.strip().startswith(DISCOVER):
            reply = json.dumps({'name': 'PLAY', 'pc': socket.gethostname(), 'port': self.port}).encode()
            try:
                u.sendto(reply, addr)
            except OSError:
                pass
        return True


class _Handler(BaseHTTPRequestHandler):
    srv = None
    protocol_version = 'HTTP/1.1'
    timeout = 300                      # an abandoned keep-alive connection lets its thread go after 5 minutes

    def log_message(self, fmt, *a):
        pass                                # no console in the Windows app; nothing reads a request log

    def do_HEAD(self):
        self.do_GET(head=True)

    def _json(self, obj, head=False, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head:
            self.wfile.write(b)

    def do_GET(self, head=False):
        srv = self.srv
        host = self.headers.get('Host')
        if not host_allowed(host):
            srv.refuse(host)                         # the window says a TV was turned away, and by which name
            return self.send_error(403, 'Use the PC\'s IP address')
        srv.seen(self.client_address[0])
        u = urlparse(self.path)
        try:
            if u.path == '/api/info':
                return self._json({'name': 'PLAY', 'pc': socket.gethostname(), 'version': VERSION,
                                   'accent': srv.accent()}, head)
            if u.path == '/api/tv':
                if not srv.lib._scanned:            # the first read of the folders: say so now, never make the TV wait
                    return self._json({'busy': 'reading'}, head, status=503)
                side = (parse_qs(u.query).get('side') or ['home'])[0].lower()
                view = srv.lib.tv_view(kids=(side == 'kids'))
                view['accent'] = srv.accent()
                return self._json(view, head)
            m = re.fullmatch(r'/media/([0-9a-f]{16})', u.path)
            if not m:
                return self.send_error(404)
            self._stream(m.group(1), head)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            # the Roku drops connections whenever it seeks; a cut-short response must not keep the connection
            self.close_connection = True

    def _stream(self, fp, head):
        lib = self.srv.lib
        p = lib.path_for(fp)           # an unknown id rescans at most every 10 s (library.path_for)
        if not p:
            return self.send_error(404)
        try:
            f = open(p, 'rb')
        except OSError:                # moved or renamed since the last scan: one throttled rescan, then retry
            lib.rescan_throttled(every=2.0)   # a known id only, so this can't be farmed
            p = lib.path_for(fp, rescan=False)
            try:
                f = open(p, 'rb') if p else None
            except OSError:
                f = None
        if f is None:
            return self.send_error(404)
        with f:
            size = os.fstat(f.fileno()).st_size
            start, end = 0, size - 1
            r = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', '').strip())
            if r and r.group(1) and r.group(2) and int(r.group(2)) < int(r.group(1)):
                r = None               # an invalid range is ignored: the whole file (RFC 9110)
            if r and (r.group(1) or r.group(2)):
                if r.group(1):
                    start = int(r.group(1))
                    if r.group(2):
                        end = min(int(r.group(2)), size - 1)
                else:
                    start = max(size - int(r.group(2)), 0)
                if start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            else:
                self.send_response(200)
            ext = os.path.splitext(p)[1].lower().lstrip('.')
            self.send_header('Content-Type', TYPES.get(ext) or mimetypes.guess_type(p)[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            if head:
                return
            f.seek(start)
            left = end - start + 1
            while left > 0:
                buf = f.read(min(CHUNK, left))
                if not buf:            # the file shrank under us: the promised length can't be met
                    self.close_connection = True
                    break
                self.wfile.write(buf)
                left -= len(buf)
