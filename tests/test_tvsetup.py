"""Set up TVs: the Roku protocol helpers against a fake TV, and the wizard with stand-in TVs. Offscreen, no real
network beyond localhost, never a real TV."""
import hashlib
import io
import os
import re
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from play import library, roku, tvsetup  # noqa: E402


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def never_a_real_tv(monkeypatch):
    """No test here may reach a real TV: searching the network, asking a TV, or installing anywhere but this PC's
    own fake installer fails the test."""
    def refuse(*a, **k):
        raise AssertionError(f'a test tried to reach the network: {a}')
    for name in ('find', 'info', 'dev_app', 'launch', '_ssdp_once'):
        monkeypatch.setattr(roku, name, refuse)
    real_install = roku.install

    def install(ip, *a, **k):
        assert ip == '127.0.0.1', f'a test tried to install on {ip}'
        return real_install(ip, *a, **k)
    monkeypatch.setattr(roku, 'install', install)


# ------------------------------------------------------------------ a fake Roku developer installer (digest auth)
class FakeInstaller(BaseHTTPRequestHandler):
    password = 'secret'
    received = []

    def log_message(self, *a):
        pass

    def _challenge(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Digest realm="rokudev", nonce="n0nce", qop="auth"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        auth = self.headers.get('Authorization') or ''
        if not auth.startswith('Digest '):
            return self._challenge()
        p = dict(re.findall(r'(\w+)="?([^",]+)"?', auth[7:]))
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        ha1 = md5(f"{p['username']}:rokudev:{self.password}")
        ha2 = md5(f"POST:{p['uri']}")
        want = md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}")
        if p.get('username') != 'rokudev' or p.get('response') != want:
            return self._challenge()
        FakeInstaller.received.append(body)
        out = b'<script>var m = [{"text":"Application Received: 1 bytes stored."},{"text":"Install Success."}]</script>'
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def tv():
    FakeInstaller.received = []
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeInstaller)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_install_sends_the_app_with_the_developer_password(tv):
    msg = roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)
    assert 'Install Success' in msg
    (body,) = FakeInstaller.received
    assert b'ZIPDATA' in body and b'name="archive"' in body and b'Install' in body


def test_a_wrong_password_is_reported_as_such(tv):
    with pytest.raises(roku.WrongPassword):
        roku.install('127.0.0.1', 'nope', b'ZIPDATA', port=tv)
    assert FakeInstaller.received == []


def test_the_package_is_the_tv_app():
    z = zipfile.ZipFile(io.BytesIO(roku.package()))
    names = z.namelist()
    assert 'manifest' in names and any(n.startswith('components/') for n in names)
    assert roku.app_version() == '1.0.0'


def test_passwords_are_encrypted_for_this_user():
    t = roku.protect('pässword')
    assert 'pässword' not in t and roku.unprotect(t) == 'pässword'
    assert roku.unprotect('not a token') is None


def test_newer():
    assert tvsetup.newer('1.0.2', '1.0.0') and not tvsetup.newer('1.0.0', '1.0.0') and not tvsetup.newer('', '1.0')


# ------------------------------------------------------------------ the wizard, with stand-in TVs
class FakeNet:
    APP_TITLE = roku.APP_TITLE

    def __init__(self, tvs):
        self.tvs = tvs                    # ip -> dict(info=..., app=..., blocked=bool, password=...)
        self.installs = []

    def find(self, local, known):
        return sorted(set(self.tvs) | set(known))

    def info(self, ip):
        t = self.tvs.get(ip)
        if t is None:
            raise OSError('no route')
        if t.get('blocked'):
            raise roku.Blocked('403')
        return dict(ip=ip, serial='SER-' + ip, model='TCL', is_tv=True, **t['info'])

    def dev_app(self, ip):
        return self.tvs[ip].get('app')

    def package(self):
        return b'zip'

    def install(self, ip, pw, data):
        if pw != self.tvs[ip].get('password', 'pw'):
            raise roku.WrongPassword('no')
        self.installs.append((ip, pw))
        self.tvs[ip]['app'] = ('PLAY', roku.app_version())
        return 'Install Success.'

    protect = staticmethod(lambda pw: 'enc:' + pw)
    unprotect = staticmethod(lambda t: t[4:] if t.startswith('enc:') else None)


class FakeServer:
    def __init__(self, tvs=()):
        self._tvs = list(tvs)

    def tvs(self):
        return list(self._tvs)


def wait_for(cond, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError('timed out')


def dialog(qapp, tmp_path, monkeypatch, tvs, served=()):
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = FakeNet(tvs)
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(served), net=net)
    wait_for(lambda: not d.searching and all(t['status'] != tvsetup.CHECKING for t in d.tv.values()))
    return d, lib, net


def select(d, ip):
    d.list.setCurrentItem(d._item(ip))


def test_each_tv_shows_what_it_needs(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {
        '10.0.0.5': {'info': {'name': 'Living Room', 'dev_mode': False}},
        '10.0.0.6': {'info': {'name': 'Bedroom', 'dev_mode': True}, 'app': ('', '')},
        '10.0.0.7': {'info': {'name': 'Den', 'dev_mode': True}, 'app': ('PLAY', '0.9.0')},
        '10.0.0.8': {'info': {'name': 'Kids', 'dev_mode': True}, 'app': ('Other', '2.0.0')},
        '10.0.0.9': {'info': {}, 'blocked': True},
    })
    st = {ip: t['status'] for ip, t in d.tv.items()}
    assert st == {'10.0.0.5': tvsetup.NO_DEV, '10.0.0.6': tvsetup.READY, '10.0.0.7': tvsetup.OLDER,
                  '10.0.0.8': tvsetup.OTHER, '10.0.0.9': tvsetup.BLOCKED}
    select(d, '10.0.0.5')
    assert 'Home ×3' in d.detail.text() and not d.go.isEnabled()        # the steps, nothing to install yet
    select(d, '10.0.0.7')
    assert d.go.text() == 'Update this TV'
    select(d, '10.0.0.9')
    assert 'Control by mobile apps' in d.detail.text() and d.go.isEnabled()
    names = sorted(d.list.topLevelItem(i).text(0) for i in range(d.list.topLevelItemCount()))
    assert names == ['Bedroom', 'Den', 'Kids', 'Living Room', 'Roku']


def test_a_tv_that_already_plays_counts_as_installed(qapp, tmp_path, monkeypatch):
    d, *_ = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': None}},
                   served=['10.0.0.6'])
    assert d.tv['10.0.0.6']['status'] == tvsetup.INSTALLED


def test_install_asks_for_the_password_once_and_remembers_it(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'pw'}})
    asked = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(1) or ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    assert net.installs == [('10.0.0.6', 'pw')] and len(asked) == 1
    assert library.Library(lib.dir).data['tv_passwords'] == {'SER-10.0.0.6': 'enc:pw'}
    d.install_current()                                                     # again: no question this time
    wait_for(lambda: len(net.installs) == 2)
    assert len(asked) == 1


def test_a_wrong_password_is_forgotten(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'new'}})
    lib.set_value('tv_passwords', {'SER-10.0.0.6': 'enc:old'})
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED)
    assert 'password' in d.detail.text() and lib.data['tv_passwords'] == {}


def test_replacing_another_developer_app_asks_first(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.8': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('Other', '2.0')}})
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.No)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.8')
    d.install_current()
    QApplication.processEvents()
    assert net.installs == []


def test_add_by_address(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {})
    assert 'No Roku found' in d.detail.text()
    net.tvs['192.168.1.50'] = {'info': {'name': 'Garage', 'dev_mode': True}, 'app': ('', '')}
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: (' 192.168.1.50 ', True))
    d.add_by_address()
    wait_for(lambda: d.tv['192.168.1.50']['status'] == tvsetup.READY)
    warned = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warned.append(a))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('living room', True))
    d.add_by_address()
    assert warned and len(d.tv) == 1
