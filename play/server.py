"""The movie server the Roku app talks to, plus LAN discovery so the TV finds this PC by itself.

    GET  /api/info              -> {"name": "PLAY", "pc": <computer name>, "version", "accent"}
    GET  /api/tv?side=home|kids -> the movies (collections as single entries) and shows for that side of the TV app
    GET  /media/<id>            -> the file, with HTTP Range support (the Roku seeks with Range requests)
    UDP  8601  "PLAY-DISCOVER"   -> {"name": "PLAY", "pc": ..., "port": <http port>} back to the sender

Only files found in the library's folders can be streamed: ids are looked up, never turned into paths.
"""
import json
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = '1.0'
DISCOVERY_PORT = 8601
DISCOVER = b'PLAY-DISCOVER'
CHUNK = 1 << 20
TYPES = {'mkv': 'video/x-matroska', 'mp4': 'video/mp4', 'mov': 'video/quicktime', 'ts': 'video/mp2t',
         'webm': 'video/webm', 'avi': 'video/x-msvideo'}


class _HTTP(ThreadingHTTPServer):
    # Python's default SO_REUSEADDR lets a second program bind the same port on Windows; take it exclusively.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class PlayServer:
    def __init__(self, lib, port=8600, accent=lambda: '#2f9bff', log=None, discovery_port=DISCOVERY_PORT):
        self.lib = lib
        self.port = port
        self.discovery_port = discovery_port
        self.accent = accent
        self.log = log or (lambda msg: None)
        self.clients = {}            # ip -> last request time, for "TVs connected"
        self.httpd = None
        self.udp = None
        self.error = None

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
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                u.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            u.bind(('0.0.0.0', self.discovery_port))
            self.discovery_port = u.getsockname()[1]
            u.settimeout(0.5)
            self.udp = u
            threading.Thread(target=self._discovery, name='play-discovery', daemon=True).start()
        except OSError as e:          # another PLAY already answers; TVs can still use the address by hand
            u.close()
            self.udp = None
            self.log(f'discovery off: {e}')
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

    def tvs(self, within=600):
        now = time.monotonic()
        return sorted(ip for ip, t in self.clients.items() if now - t < within)

    def _discovery(self):
        u = self.udp
        while self.udp is u:
            try:
                data, addr = u.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.strip().startswith(DISCOVER):
                reply = json.dumps({'name': 'PLAY', 'pc': socket.gethostname(), 'port': self.port}).encode()
                try:
                    u.sendto(reply, addr)
                except OSError:
                    pass


class _Handler(BaseHTTPRequestHandler):
    srv = None
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *a):
        self.srv.log('%s %s' % (self.client_address[0], fmt % a))

    def do_HEAD(self):
        self.do_GET(head=True)

    def _json(self, obj, head=False):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head:
            self.wfile.write(b)

    def do_GET(self, head=False):
        srv = self.srv
        srv.clients[self.client_address[0]] = time.monotonic()
        u = urlparse(self.path)
        try:
            if u.path == '/api/info':
                return self._json({'name': 'PLAY', 'pc': socket.gethostname(), 'version': VERSION,
                                   'accent': srv.accent()}, head)
            if u.path == '/api/tv':
                side = (parse_qs(u.query).get('side') or ['home'])[0].lower()
                view = srv.lib.tv_view(kids=(side == 'kids'))
                view['accent'] = srv.accent()
                return self._json(view, head)
            m = re.fullmatch(r'/media/([0-9a-f]{16})', u.path)
            if not m:
                return self.send_error(404)
            self._stream(m.group(1), head)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass                       # the Roku drops connections whenever it seeks

    def _stream(self, fp, head):
        lib = self.srv.lib
        p = lib.path_for(fp)
        f = None
        for attempt in (0, 1):
            if p:
                try:
                    f = open(p, 'rb')
                    break
                except OSError:
                    pass
            if attempt == 0:           # moved or renamed since the last scan
                lib.scan()
                p = lib.path_for(fp)
        if f is None:
            return self.send_error(404)
        with f:
            size = os.fstat(f.fileno()).st_size
            start, end = 0, size - 1
            r = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', '').strip())
            if r and (r.group(1) or r.group(2)):
                if r.group(1):
                    start = int(r.group(1))
                    if r.group(2):
                        end = min(int(r.group(2)), size - 1)
                else:
                    start = max(size - int(r.group(2)), 0)
                if start >= size or end < start:
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
