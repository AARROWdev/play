"""Set up TVs: the Roku protocol helpers against a fake TV, and the wizard with stand-in TVs. Offscreen, no real
network beyond localhost, never a real TV."""
import hashlib
import socket
import urllib.error
import io
import os
import re
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from play import library, roku, tvsetup  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRLF = bytes([13, 10])


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def never_a_real_tv(monkeypatch):
    """No test here may reach a real TV: searching the network, asking a TV, or installing anywhere but this PC's
    own fake installer fails the test, also when it happens in a worker thread (checked at the end)."""
    tried = []

    def refuse(name):
        def f(*a, **k):
            tried.append((name, a))
            raise AssertionError(f'a test tried to reach the network: {name}{a}')
        return f
    for name in ('find', 'info', 'dev_app', '_ssdp_once'):
        monkeypatch.setattr(roku, name, refuse(name))
    real_install = roku.install

    def install(ip, *a, **k):
        if ip != '127.0.0.1':
            tried.append(('install', ip))
            raise AssertionError(f'a test tried to install on {ip}')
        return real_install(ip, *a, **k)
    monkeypatch.setattr(roku, 'install', install)
    yield
    assert not tried, f'reached for the network: {tried}'


# ------------------------------------------------------------------ a fake Roku developer installer (digest auth)
class FakeInstaller(BaseHTTPRequestHandler):
    password = 'secret'
    received = []
    attempts = 0
    uploads = 0                   # POSTs that carried the app
    stale_once = False            # answer the first good password with "stale nonce" (a fresh one is wanted)
    stale_always = False          # a TV that always says "stale"
    preflight_404 = False         # no challenge up front (the upload itself gets it)
    refuse_bare = False           # a refusal of the password without any WWW-Authenticate
    nonce = 'n0nce'
    reply = b'<script>var m = [{"text":"Application Received: 1 bytes stored."},{"text":"Install Success."}]</script>'

    def log_message(self, *a):
        pass

    def _challenge(self, stale=False):
        self.send_response(401)
        self.send_header('WWW-Authenticate', f'Digest realm="rokudev", nonce="{FakeInstaller.nonce}", qop="auth"'
                         + (', stale=true' if stale else ''))
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if FakeInstaller.preflight_404:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self._challenge()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if b'ZIP' in body:
            FakeInstaller.uploads += 1
        auth = self.headers.get('Authorization') or ''
        if not auth.startswith('Digest '):
            return self._challenge()
        FakeInstaller.attempts += 1
        p = dict(re.findall(r'(\w+)="?([^",]+)"?', auth[7:]))
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        ha1 = md5(f"{p['username']}:rokudev:{self.password}")
        ha2 = md5(f"POST:{p['uri']}")
        want = md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}")
        if p.get('username') != 'rokudev' or p.get('response') != want:
            if FakeInstaller.refuse_bare:
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self._challenge()
        if FakeInstaller.stale_once or FakeInstaller.stale_always:
            FakeInstaller.stale_once, FakeInstaller.nonce = False, FakeInstaller.nonce + 'x'
            return self._challenge(stale=True)
        if p['nonce'] != FakeInstaller.nonce:
            return self._challenge()
        FakeInstaller.received.append(body)
        out = FakeInstaller.reply
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def tv():
    FakeInstaller.received, FakeInstaller.attempts, FakeInstaller.uploads = [], 0, 0
    FakeInstaller.stale_once, FakeInstaller.nonce = False, 'n0nce'
    FakeInstaller.stale_always = FakeInstaller.preflight_404 = FakeInstaller.refuse_bare = False
    FakeInstaller.reply = b'<script>var m = [{"text":"Install Success."}]</script>'
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeInstaller)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    FakeInstaller.stale_once = FakeInstaller.stale_always = False
    FakeInstaller.preflight_404 = FakeInstaller.refuse_bare = False


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
    assert sorted(z.namelist()) == [
        'components/DiscoverTask.brs', 'components/DiscoverTask.xml', 'components/LoadTask.brs',
        'components/LoadTask.xml', 'components/MainScene.brs', 'components/MainScene.xml', 'images/icon.png',
        'images/icon_fhd.png', 'images/icon_hd.png', 'images/icon_sd.png', 'images/splash.png', 'manifest',
        'source/main.brs']
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
        return {'ip': ip, 'serial': 'SER-' + ip, 'model': 'TCL', 'is_tv': True, **t['info']}

    def dev_app(self, ip):
        return self.tvs[ip].get('app')

    def package(self):
        return b'zip'

    def install(self, ip, pw, data):
        if pw != self.tvs[ip].get('password', 'pw'):
            raise roku.WrongPassword('The TV did not accept that developer password')
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
    assert library.Library(lib.dir).data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:pw'}
    d.install_current()                                                     # again: no question this time
    wait_for(lambda: len(net.installs) == 2)
    assert len(asked) == 1


def test_a_wrong_password_is_forgotten(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'new'}})
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:old'})
    asked = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(a[2]) or ('', False))   # cancel
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED and asked)
    assert 'password' in d.detail.text() and lib.data['tv_passwords'] == {}
    assert "didn't work" in asked[0]                                        # asked again, once


def test_a_wrong_password_can_be_typed_again(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'new'}})
    answers = iter([('old', True), ('new', True)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(answers))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    assert net.installs == [('10.0.0.6', 'new')]


def test_replacing_another_developer_app_asks_first(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.8': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('Other', '2.0')}})
    asked = []
    monkeypatch.setattr(tvsetup, 'confirm', lambda parent, text: asked.append(text) or False)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.8')
    d.install_current()
    QApplication.processEvents()
    assert net.installs == [] and '"Other"' in asked[0]


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



# ------------------------------------------------------------------ 49232b5 review
def test_a_garbled_answer_doesnt_leave_a_tv_checking(qapp, tmp_path, monkeypatch):
    import http.client

    class Garbled(FakeNet):
        def dev_app(self, ip):
            raise http.client.BadStatusLine('garbage')
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = Garbled({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') not in (None, tvsetup.CHECKING))
    assert d.tv['10.0.0.6']['status'] == tvsetup.OFFLINE


def test_ssdp_answers_are_only_trusted_from_where_they_came():
    reply = b'HTTP/1.1 200 OK\r\nST: roku:ecp\r\nLOCATION: http://%s:8060/\r\n\r\n'
    assert roku.ssdp_reply_ok(reply % b'192.168.1.50', '192.168.1.50')
    assert not roku.ssdp_reply_ok(reply % b'203.0.113.9', '192.168.1.50')       # points somewhere else
    assert not roku.ssdp_reply_ok(reply % b'203.0.113.9', '203.0.113.9')        # not a home network
    assert not roku.ssdp_reply_ok(b'HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\n\r\n', '192.168.1.50')
    assert not roku.lan_address('100.100.1.1') and not roku.lan_address('127.0.0.1')


def test_install_never_goes_through_a_proxy(tv, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')                     # a proxy that isn't there
    monkeypatch.setenv('NO_PROXY', '')
    assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_a_wrong_password_is_tried_once(tv):
    with pytest.raises(roku.WrongPassword):
        roku.install('127.0.0.1', 'nope', b'ZIP', port=tv)
    assert FakeInstaller.attempts == 1                                          # not five more uploads


def test_an_answer_that_isnt_a_success_isnt_one(tv):
    FakeInstaller.reply = b'<html>something else</html>'
    with pytest.raises(OSError):
        roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_the_password_is_bound_to_the_tv_and_its_address(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.7': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'x'}})
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:pw'})      # another address (or a copied serial)
    asked = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(1) or ('', False))
    select(d, '10.0.0.7')
    d.install_current()
    assert asked and net.installs == []                                     # asked, not sent


def test_no_password_is_kept_for_a_tv_that_didnt_say_its_serial(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.9': {'info': {}, 'blocked': True, 'password': 'pw'}})
    monkeypatch.setattr(tvsetup, 'confirm', lambda *a: True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.9')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.9']['status'] == tvsetup.DONE)
    assert lib.data['tv_passwords'] == {}


def test_reinstalling_where_the_app_is_unknown_asks_first(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True},
                                                                    'app': None}}, served=['10.0.0.6'])
    assert d.tv['10.0.0.6']['status'] == tvsetup.INSTALLED
    asked = []
    monkeypatch.setattr(tvsetup, 'confirm', lambda parent, text: asked.append(text) or False)
    select(d, '10.0.0.6')
    d.install_current()
    assert asked and net.installs == []


def test_closing_mid_install_still_keeps_the_password(qapp, tmp_path, monkeypatch):
    import shiboken6
    gate = threading.Event()

    class Slow(FakeNet):
        def install(self, ip, pw, data):
            gate.wait(5)
            return super().install(ip, pw, data)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = Slow({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'pw'}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()
    d.search()                                                              # a search meanwhile leaves it be
    wait_for(lambda: not d.searching)
    assert d.tv['10.0.0.6']['status'] == tvsetup.INSTALLING
    d.release()                                                             # closed mid-install
    QApplication.processEvents()
    assert shiboken6.isValid(d)                                             # kept until the install is done
    gate.set()
    wait_for(lambda: lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:pw'})



# ------------------------------------------------------------------ 347f657 review
class FakeEcp(BaseHTTPRequestHandler):
    redirect = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        if FakeEcp.redirect and self.path != '/elsewhere':
            self.send_response(302)
            self.send_header('Location', FakeEcp.redirect)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        out = b'<device-info><serial-number>X1</serial-number></device-info>'
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def ecp():
    FakeEcp.redirect = False
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeEcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/query/device-info'
    srv.shutdown()
    srv.server_close()


def test_the_app_is_uploaded_once(tv):
    assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)
    assert FakeInstaller.uploads == 1


def test_a_stale_nonce_is_retried_not_taken_as_a_wrong_password(tv):
    FakeInstaller.stale_once = True
    assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)


def test_a_failure_message_is_a_failure(tv):
    for reply in (b'"text":"Install unsuccessful"', b'"text":"Install Failure: success not reached"'):
        FakeInstaller.reply = reply
        with pytest.raises(OSError):
            roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_questions_go_direct_and_dont_follow_redirects(ecp, monkeypatch):
    import subprocess
    import sys
    import urllib.request
    # roku's own opener, as loaded with a proxy set that isn't there (in a fresh Python, so it's made then)
    env = dict(os.environ, HTTP_PROXY='http://127.0.0.1:9', NO_PROXY='', PYTHONPATH=ROOT)
    code = f'from play import roku; print(roku._get({ecp!r}))'
    out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=60)
    assert 'X1' in out.stdout, out.stderr
    # it has no redirect handler that follows
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in roku._DIRECT.handlers)
    assert any(isinstance(h, roku._NoRedirect) for h in roku._DIRECT.handlers)
    # a 302 to a page that answers is refused, its answer never read
    target = ThreadingHTTPServer(('127.0.0.1', 0), FakeEcp)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    try:
        FakeEcp.redirect = f'http://127.0.0.1:{target.server_address[1]}/elsewhere'
        with pytest.raises(urllib.error.HTTPError):
            roku._get(ecp)
    finally:
        target.shutdown()
        target.server_close()


class SlowEcp(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        time.sleep(3)                               # the headers never come in time
        try:
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
        except OSError:
            pass


def test_a_request_that_trickles_along_is_cut_off(monkeypatch):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), SlowEcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(roku, 'DEADLINE', 0.5)
        t = time.monotonic()
        with pytest.raises(OSError, match='too long to answer'):
            roku._get(f'http://127.0.0.1:{srv.server_address[1]}/query/device-info')
        assert time.monotonic() - t < 2                                      # cut off at the deadline, in place
        time.sleep(0.2)
        assert roku._watchdog.pending == []                                    # nothing left to watch
        assert len([th for th in threading.enumerate() if th.name == 'play-deadline']) <= 1   # one, shared
    finally:
        srv.shutdown()
        srv.server_close()
    assert roku.within(1, lambda: 'ok') == 'ok'
    with pytest.raises(KeyError):
        roku.within(1, {}.__getitem__, 'x')                                   # its own errors come through


def test_the_replace_question_is_plain_text(qapp, monkeypatch):
    seen = []

    def exec_(box):
        seen.append(box.textFormat())
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, 'exec', exec_)
    assert tvsetup.confirm(None, '<b>Other</b>') is False
    assert seen == [Qt.PlainText]


def test_after_installing_the_app_is_known(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True},
                                                                    'app': None, 'password': 'pw'}})
    monkeypatch.setattr(tvsetup, 'confirm', lambda *a: True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    asked = []
    monkeypatch.setattr(tvsetup, 'confirm', lambda *a: asked.append(1) or True)
    d.install_current()                                                     # Reinstall: PLAY is known to be there
    wait_for(lambda: len(net.installs) == 2)
    assert asked == []


def test_add_by_address_leaves_an_installing_tv_alone(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True},
                                                                    'app': ('', '')}})
    d.tv['10.0.0.6']['status'] = tvsetup.INSTALLING
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('10.0.0.6', True))
    d.add_by_address()
    assert d.tv['10.0.0.6']['status'] == tvsetup.INSTALLING
    warned = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warned.append(a))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('8.8.8.8', True))
    d.add_by_address()                                                      # not a home-network address
    assert warned and '8.8.8.8' not in d.tv


def test_a_closed_wizard_checks_nothing_new(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {})
    d.release()
    d._found(['10.0.0.6'])
    assert d.tv == {}


def test_old_password_keys_are_dropped(qapp, tmp_path, monkeypatch):
    (tmp_path / 'library.json').write_text('{"tv_passwords": {"SER-10.0.0.6": "enc:x", "SER-10.0.0.6@10.0.0.5": '
                                           '"enc:old"}}', encoding='utf-8')
    assert library.Library(str(tmp_path)).data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.5': 'enc:old'}
    d, lib, net = dialog(qapp, tmp_path, monkeypatch, {'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True},
                                                                    'app': ('', ''), 'password': 'pw'}})
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    assert lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:pw'}    # the TV's old address is gone



# ------------------------------------------------------------------ 9191676 review
def test_a_quoted_stale_nonce_is_retried(tv, monkeypatch):
    def quoted(self, stale=False):
        self.send_response(401)
        self.send_header('WWW-Authenticate', f'Digest realm="rokudev", nonce="{FakeInstaller.nonce}", qop="auth"'
                         + (', stale="true"' if stale else ''))
        self.send_header('Content-Length', '0')
        self.end_headers()
    monkeypatch.setattr(FakeInstaller, '_challenge', quoted)
    FakeInstaller.stale_once = True
    assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)


def test_an_odd_challenge_falls_back_to_the_upload_itself():
    assert roku._challenge('Digest realm="rokudev",, nonce="n"') == {'realm': 'rokudev', 'nonce': 'n'}
    assert roku._challenge('Basic realm="x"') == {}
    assert roku._challenge('Digest ,,,') == {}


def test_no_answer_to_the_first_question_still_installs(tv, monkeypatch):
    real_open = roku._DIRECT.open

    def flaky(req, *a, **k):
        if getattr(req, 'method', None) == 'GET':
            raise urllib.error.URLError('timed out')
        return real_open(req, *a, **k)
    monkeypatch.setattr(roku._DIRECT, 'open', flaky)
    assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)


def test_a_refused_password_elsewhere_keeps_the_tvs_password(qapp, tmp_path, monkeypatch):
    d, lib, net = dialog(qapp, tmp_path, monkeypatch,
                         {'10.0.0.7': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'x'}})
    net.tvs['10.0.0.7']['info']['serial'] = 'SER-10.0.0.6'                   # the same serial, another address
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:pw'})
    d._check('10.0.0.7')
    wait_for(lambda: d.tv['10.0.0.7']['status'] == tvsetup.READY)
    answers = iter([('wrong', True), ('', False)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(answers))
    select(d, '10.0.0.7')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.7']['status'] == tvsetup.FAILED)
    QApplication.processEvents()
    assert lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:pw'}


def test_a_wrong_password_while_closed_is_asked_when_reopened(qapp, tmp_path, monkeypatch):
    gate = threading.Event()

    class Slow(FakeNet):
        def install(self, ip, pw, data):
            gate.wait(5)
            return super().install(ip, pw, data)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = Slow({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'right'}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    asked = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(a[2]) or ('wrong', True))
    select(d, '10.0.0.6')
    d.install_current()
    d.release()
    gate.set()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED)
    QApplication.processEvents()
    assert len(asked) == 1                                                  # not asked while closed
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: asked.append(a[2]) or ('right', True))
    d.reopen()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    assert "didn't work" in asked[1]



# ------------------------------------------------------------------ f66fff0 review
def test_an_install_cut_off_stops_and_doesnt_carry_on(tv, monkeypatch):
    def drip_post(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        FakeInstaller.uploads += b'ZIP' in body
        if FakeInstaller.uploads == 1:              # the first upload: a "stale nonce" answer, a line at a time
            for line in (b'HTTP/1.1 401 Unauthorized' + CRLF,
                         b'WWW-Authenticate: Digest realm="rokudev", nonce="n2", qop="auth", stale=true' + CRLF,
                         b'Content-Length: 0' + CRLF, CRLF):
                time.sleep(0.3)                     # each line well inside the per-step timeout
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except OSError:
                    return
        else:
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
    monkeypatch.setattr(FakeInstaller, 'do_POST', drip_post)
    with pytest.raises(OSError, match='too long'):
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=0.8, port=tv)
    time.sleep(2.5)                                 # a call left running would have uploaded again by now
    assert FakeInstaller.uploads == 1


def test_a_challenge_without_a_nonce_is_explained(tv, monkeypatch):
    def no_nonce(self, stale=False):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Digest realm="rokudev", qop="auth"')
        self.send_header('Content-Length', '0')
        self.end_headers()
    monkeypatch.setattr(FakeInstaller, '_challenge', no_nonce)
    with pytest.raises(OSError, match="recognise"):
        roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_reopening_during_an_install_searches_and_keeps_the_install(qapp, tmp_path, monkeypatch):
    gate = threading.Event()
    searched = []

    class Slow(FakeNet):
        def install(self, ip, pw, data):
            gate.wait(5)
            return super().install(ip, pw, data)

        def find(self, local, known):
            searched.append(1)
            return super().find(local, known)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = Slow({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'pw'}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()
    d.release()
    n = len(searched)
    d.reopen()
    wait_for(lambda: len(searched) > n and not d.searching)
    QApplication.processEvents()
    assert d.tv['10.0.0.6']['status'] == tvsetup.INSTALLING and not d.go.isEnabled()
    gate.set()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)



def test_connecting_counts_against_the_time(monkeypatch):
    seen = []
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', lambda self: seen.append(self.timeout))
    monkeypatch.setattr(roku._Deadline, 'add', lambda self, sock: None)
    roku.within(0.5, lambda: roku._Connection('127.0.0.1', 9, timeout=120).connect())
    assert seen and seen[0] <= 0.6                  # the time left, plus a moment for the watchdog to call it


def test_odd_password_challenges_are_explained(tv, monkeypatch):
    for header in ('NTLM', ''):
        def challenge(self, stale=False, header=header):
            self.send_response(401)
            self.send_header('WWW-Authenticate', header)
            self.send_header('Content-Length', '0')
            self.end_headers()
        monkeypatch.setattr(FakeInstaller, '_challenge', challenge)
        with pytest.raises(OSError, match='recognise'):
            roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_a_call_inside_a_call_gives_the_outer_deadline_back():
    def outer():
        before = roku._current.deadline
        roku.within(5, lambda: None)
        return roku._current.deadline is before
    assert roku.within(5, outer) is True


def test_the_tvs_own_answer_at_the_limit_isnt_called_too_long():
    def refuse():
        time.sleep(0.3)
        raise urllib.error.HTTPError('http://tv/', 401, 'no', {}, None)
    with pytest.raises(urllib.error.HTTPError):
        roku.within(0.1, refuse)


# ------------------------------------------------------------------ f21695d review
class SlowAuthInstaller(FakeInstaller):
    """Answers a password challenge fast, but an upload that carries a password very slowly (as a TV busy
    installing, or one that has hung)."""
    preflight_404 = False

    def do_GET(self):
        if SlowAuthInstaller.preflight_404:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self._challenge()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        FakeInstaller.uploads += b'ZIP' in body
        if not self.headers.get('Authorization'):
            return self._challenge()
        time.sleep(3)


@pytest.fixture
def slow_tv():
    FakeInstaller.uploads, FakeInstaller.nonce, SlowAuthInstaller.preflight_404 = 0, 'n0nce', False
    srv = ThreadingHTTPServer(('127.0.0.1', 0), SlowAuthInstaller)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_no_answer_after_sending_the_app_with_the_password(slow_tv):
    with pytest.raises(roku.TookTooLong) as e:
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=0.8, port=slow_tv)
    assert e.value.uploaded


def test_an_upload_without_a_password_doesnt_count_as_sent(slow_tv, monkeypatch):
    monkeypatch.setattr(SlowAuthInstaller, 'preflight_404', True)          # no challenge up front: the first upload goes without one
    with pytest.raises(roku.TookTooLong) as e:
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=0.8, port=slow_tv)
    assert FakeInstaller.uploads == 2               # the bare one, then the one with the password ...
    assert e.value.uploaded                         # ... which is the one that counts


def test_no_answer_after_the_app_went_is_unsure_and_keeps_no_password(qapp, tmp_path, monkeypatch):
    class Late(FakeNet):
        def install(self, ip, pw, data):
            raise roku.TookTooLong('The TV took too long to answer', uploaded=True)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.9': 'enc:old'})        # the same TV, another address
    net = Late({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('maybe', True))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.UNSURE)
    assert "didn't say how it went" in d.detail.text() and "may have reached" in d.detail.text()
    assert d.go.text() == 'Install again' and d.go.isEnabled()
    assert lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.9': 'enc:old'}   # nothing stored, nothing pruned


def test_a_slow_check_that_lands_after_an_install_is_ignored(qapp, tmp_path, monkeypatch):
    gate, slow = threading.Event(), {'on': False}

    class SlowCheck(FakeNet):
        def info(self, ip):
            if slow['on']:
                gate.wait(5)
                return dict(super().info(ip), dev_mode=True)
            return super().info(ip)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    net = SlowCheck({'10.0.0.6': {'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', ''), 'password': 'pw'}})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=net)
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    slow['on'] = True
    d._check('10.0.0.6')                            # a check starts, and hangs ...
    d.tv['10.0.0.6']['status'] = tvsetup.READY
    monkeypatch.setattr(tvsetup, 'confirm', lambda *a: True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('pw', True))
    select(d, '10.0.0.6')
    d.install_current()                             # ... an install starts and finishes ...
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.DONE)
    gate.set()                                      # ... then the old check answers
    time.sleep(0.3)
    QApplication.processEvents()
    assert d.tv['10.0.0.6']['status'] == tvsetup.DONE


def test_a_call_that_finishes_in_time_leaves_nothing_behind():
    assert roku.within(5, lambda: 'ok') == 'ok'
    assert roku._watchdog.pending == []


def test_the_watchdog_stays_up_after_a_cut_off(monkeypatch):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), SlowEcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(roku, 'DEADLINE', 0.3)
        with pytest.raises(roku.TookTooLong):
            roku._get(f'http://127.0.0.1:{srv.server_address[1]}/query/device-info')
    finally:
        srv.shutdown()
        srv.server_close()
    time.sleep(0.2)
    assert roku._watchdog.thread is not None and roku._watchdog.thread.is_alive()


def test_the_watchdog_rests_when_idle_and_wakes_again():
    dog = roku._Watchdog(idle=0.2)
    d = roku._Deadline(5)
    dog.watch(d)
    dog.finished(d)
    time.sleep(0.6)
    assert dog.thread is None                                                # idle: gone
    late = roku._Deadline(0.1)
    dog.watch(late)                                                          # needed again: back
    time.sleep(0.4)
    assert late.expired


def test_a_connect_that_times_out_at_the_end_is_too_long(monkeypatch):
    def connect(self):
        time.sleep(min(self.timeout, 2))
        raise socket.timeout('timed out')
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', connect)
    with pytest.raises(roku.TookTooLong):
        roku.within(0.3, lambda: roku._Connection('127.0.0.1', 9, timeout=120).connect())


def test_a_call_inside_a_call_is_capped_by_the_outer_one():
    seen = []

    def outer():
        roku.within(60, lambda: seen.append(roku._current.deadline.left()))
    roku.within(1, outer)
    assert seen[0] <= 1


def test_plays_own_mistakes_arent_called_an_odd_challenge(tv, monkeypatch):
    FakeInstaller.preflight_404 = True              # so the password goes with urllib's retry of the upload
    real = roku._Connection.request

    def broken(self, method, url, body=None, headers={}, **kw):
        if body and 'Authorization' in headers:     # the upload that carries the password (or its retry)
            raise TypeError('a bug in PLAY')
        return real(self, method, url, body, headers, **kw)
    monkeypatch.setattr(roku._Connection, 'request', broken)
    with pytest.raises(TypeError):
        roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_challenges_without_a_realm_or_with_auth_int_are_explained(tv, monkeypatch):
    for header in ('Digest nonce="n", qop="auth"', 'Digest realm="rokudev", nonce="n", qop="auth-int"'):
        def challenge(self, stale=False, header=header):
            self.send_response(401)
            self.send_header('WWW-Authenticate', header)
            self.send_header('Content-Length', '0')
            self.end_headers()
        monkeypatch.setattr(FakeInstaller, '_challenge', challenge)
        with pytest.raises(roku.Unrecognised, match='recognise'):
            roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_a_bare_upload_doesnt_count_as_sent(monkeypatch):
    class BareSlow(SlowAuthInstaller):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            time.sleep(3)                           # no challenge: the upload without a password just hangs
    monkeypatch.setattr(SlowAuthInstaller, 'preflight_404', True)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), BareSlow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(roku.TookTooLong) as e:
            roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=0.8, port=srv.server_address[1])
        assert not e.value.uploaded                 # so the wizard says it failed, not "may have reached"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_call_inside_a_call_reports_what_it_sent():
    outer_seen = {}

    def outer():
        roku.within(5, lambda: roku._current.deadline.sent(True))
        outer_seen['uploaded'] = roku._current.deadline.uploaded
    roku.within(5, outer)
    assert outer_seen['uploaded'] is True



# ------------------------------------------------------------------ 66c4407 review
def test_a_refusal_without_a_challenge_is_a_wrong_password(tv):
    FakeInstaller.refuse_bare = True
    with pytest.raises(roku.WrongPassword):
        roku.install('127.0.0.1', 'nope', b'ZIP', port=tv)


def test_a_tv_that_always_says_stale_gets_one_retry(tv):
    FakeInstaller.stale_always = True
    with pytest.raises(roku.WrongPassword):
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', port=tv)
    assert FakeInstaller.attempts == 2                                        # the upload and one retry


def test_plays_own_verdicts_come_through_even_after_the_limit():
    def judge():
        d = roku._current.deadline
        time.sleep(0.3)
        d.cut = True                                # as if the cut-off had closed something meanwhile
        raise roku.WrongPassword('refused')
    with pytest.raises(roku.WrongPassword):
        roku.within(0.1, judge)


def test_an_error_the_cut_off_didnt_cause_is_its_own():
    def reset():
        time.sleep(0.3)                             # past the limit, but nothing of this call was closed
        raise ConnectionResetError('reset by the TV')
    with pytest.raises(ConnectionResetError):
        roku.within(0.1, reset)


def test_a_retry_cut_off_before_its_upload_finished_isnt_unsure(tv, monkeypatch):
    FakeInstaller.stale_once = True                 # the upload goes, the TV asks for a fresh nonce ...
    real = roku.http.client.HTTPConnection.request
    authorized = []

    def request(self, method, url, body=None, headers={}, **kw):
        if body and any(k.lower() == 'authorization' for k in headers):
            authorized.append(1)
            if len(authorized) == 2:                # ... and the retry is still being sent when time runs out
                d, give_up = roku._current.deadline, time.monotonic() + 10
                while not d.expired and time.monotonic() < give_up:
                    time.sleep(0.02)
                raise BrokenPipeError('the connection was closed mid-upload')
        return real(self, method, url, body, headers, **kw)
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'request', request)
    with pytest.raises(roku.TookTooLong) as e:
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=1, port=tv)
    assert not e.value.uploaded                     # the first went (and was answered); the retry didn't finish


def test_a_call_inside_a_call_gives_its_connections_to_the_outer_one():
    class Sock:
        def shutdown(self, how):
            pass

        def detach(self):
            return -1
    sock, seen = Sock(), {}

    def outer():
        roku.within(5, lambda: roku._current.deadline.add(sock))
        seen['outer'] = list(roku._current.deadline.socks)
    roku.within(5, outer)
    assert seen['outer'] == [sock]



# ------------------------------------------------------------------ af3bd7b review
def test_a_challenge_that_cant_be_parsed_is_explained(tv, monkeypatch):
    def broken(self, stale=False):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Digest realm=, nonce="n"')
        self.send_header('Content-Length', '0')
        self.end_headers()
    monkeypatch.setattr(FakeInstaller, '_challenge', broken)
    with pytest.raises(roku.Unrecognised):
        roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_a_stale_challenge_that_cant_be_answered_isnt_a_wrong_password(tv, monkeypatch):
    real = FakeInstaller._challenge

    def stale_without_realm(self, stale=False):
        if not stale:
            return real(self)
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Digest nonce="n2", qop="auth", stale=true')
        self.send_header('Content-Length', '0')
        self.end_headers()
    monkeypatch.setattr(FakeInstaller, '_challenge', stale_without_realm)
    FakeInstaller.stale_once = True
    with pytest.raises(roku.Unrecognised):          # not WrongPassword: the stored password stays
        roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)


def test_the_401_is_finished_with_before_the_password_goes(tv, monkeypatch):
    import http.client as hc
    answers, open_at_upload = [], []
    real_begin, real_request = hc.HTTPResponse.begin, roku._Connection.request

    def begin(self):
        real_begin(self)
        answers.append(self)

    def request(self, method, url, body=None, headers={}, **kw):
        if body and 'Authorization' in headers:     # the password goes: every 401 before it must be closed
            open_at_upload.extend(r for r in answers if r.status == 401 and not r.isclosed())
        return real_request(self, method, url, body, headers, **kw)
    monkeypatch.setattr(hc.HTTPResponse, 'begin', begin)
    monkeypatch.setattr(roku._Connection, 'request', request)
    FakeInstaller.preflight_404 = True              # the upload itself gets the 401, then the retry goes
    roku.install('127.0.0.1', 'secret', b'ZIP', port=tv)
    assert any(r.status == 401 for r in answers) and open_at_upload == []


def test_a_late_watchdog_still_counts_a_connect_timeout(monkeypatch):
    def connect(self):
        time.sleep(min(self.timeout, 2))
        raise socket.timeout('timed out')
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', connect)
    monkeypatch.setattr(roku._watchdog, 'watch', lambda d: None)             # the watchdog never gets to it
    with pytest.raises(roku.TookTooLong):
        roku.within(0.3, lambda: roku._Connection('127.0.0.1', 9, timeout=120).connect())


def test_a_connection_made_after_the_cut_off_counts_as_cut():
    class Sock:
        closed = False

        def shutdown(self, how):
            pass

        def detach(self):
            Sock.closed = True
            return -1
    d = roku._Deadline(5)
    d.expired = True
    d.add(Sock())
    assert d.cut and Sock.closed



# ------------------------------------------------------------------ d877e7f review
def test_a_slow_401_body_doesnt_hold_up_the_password(monkeypatch):
    class SlowBody401(FakeInstaller):
        def _challenge(self, stale=False):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Digest realm="rokudev", nonce="n0nce", qop="auth"')
            self.send_header('Content-Length', '100000')
            self.end_headers()
            try:
                self.wfile.write(b'x')
                self.wfile.flush()
                time.sleep(5)                       # the rest of the body never comes in time
            except OSError:
                pass
    FakeInstaller.attempts, FakeInstaller.nonce = 0, 'n0nce'
    monkeypatch.setattr(FakeInstaller, 'preflight_404', True)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), SlowBody401)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = time.monotonic()
        assert 'Install Success' in roku.install('127.0.0.1', 'secret', b'ZIP', timeout=10, port=srv.server_address[1])
        assert time.monotonic() - t < 3
    finally:
        srv.shutdown()
        srv.server_close()


def test_no_new_connection_after_the_time_is_up(monkeypatch):
    connects = []
    real = roku._Connection.connect

    def connect(self):
        real(self)
        connects.append(time.monotonic())          # (connections actually made)
    monkeypatch.setattr(roku._Connection, 'connect', connect)

    real_answer = roku._OnceDigest.get_authorization

    def slow_answer(self, req, chal):               # the time runs out while the challenge is being answered
        time.sleep(0.8)
        return real_answer(self, req, chal)
    monkeypatch.setattr(roku._OnceDigest, 'get_authorization', slow_answer)
    FakeInstaller.attempts, FakeInstaller.nonce = 0, 'n0nce'
    monkeypatch.setattr(FakeInstaller, 'preflight_404', True)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeInstaller)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OSError):
            roku.install('127.0.0.1', 'secret', b'ZIP', timeout=0.5, port=srv.server_address[1])
        assert len(connects) <= 2                   # the GET and the upload; no third for the password
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_refusal_leaves_nothing_open(tv):
    FakeInstaller.refuse_bare = True
    with pytest.raises(roku.WrongPassword) as e:
        roku.install('127.0.0.1', 'nope', b'ZIP', port=tv)
    assert e.value.__cause__.fp.closed


def test_a_password_with_accents_is_an_unusable_password(monkeypatch):
    monkeypatch.setattr(roku._Connection, 'connect', lambda self: (_ for _ in ()).throw(AssertionError('connected')))
    with pytest.raises(roku.UnusablePassword, match='plain letters'):
        roku.install('127.0.0.1', 'sécret', b'ZIP', port=9)



# ------------------------------------------------------------------ 1956e62 review
def test_a_stale_answer_then_the_cut_off_isnt_unsure(tv, monkeypatch):
    FakeInstaller.stale_once = True
    real_answer = roku._OnceDigest.get_authorization
    calls = []

    def slow_second(self, req, chal):               # the stale 401 is in, then time runs out before the retry
        calls.append(1)
        if len(calls) == 2:
            time.sleep(0.8)
        return real_answer(self, req, chal)
    monkeypatch.setattr(roku._OnceDigest, 'get_authorization', slow_second)
    with pytest.raises(roku.TookTooLong) as e:
        roku.install('127.0.0.1', 'secret', b'ZIPDATA', timeout=0.5, port=tv)
    assert not e.value.uploaded                     # the TV refused that upload (stale): nothing to be unsure of


def test_no_connection_once_the_time_is_up_even_if_the_watchdog_is_late(monkeypatch):
    monkeypatch.setattr(roku._watchdog, 'watch', lambda d: None)            # it never gets to it
    made = []
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', lambda self: made.append(1))

    def late_connect():
        time.sleep(0.3)
        roku._Connection('127.0.0.1', 9, timeout=5).connect()
    with pytest.raises(roku.TookTooLong):
        roku.within(0.1, late_connect)
    assert made == []


def test_the_wizard_shows_why_a_password_was_refused(qapp, tmp_path, monkeypatch):
    class Plain(FakeNet):
        def install(self, ip, pw, data):
            raise roku.UnusablePassword('A Roku developer password uses only plain letters, digits and symbols.')
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=Plain({'10.0.0.6': {
        'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}}))
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    prompts, answers = [], iter([('sécret', True), ('', False)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: prompts.append(a[2]) or next(answers))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED and len(prompts) == 2)
    QApplication.processEvents()
    assert 'plain letters' in d.detail.text() and 'Check the TV is on' not in d.detail.text()
    assert 'plain letters' in prompts[1] and "didn't work" not in prompts[1]



# ------------------------------------------------------------------ 4959f71 review
class Refusing(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(403)
        self.send_header('Content-Length', '0')
        self.end_headers()


def test_a_refused_question_is_closed(monkeypatch):
    closed = []
    real = urllib.error.HTTPError.close

    def close(self):
        closed.append(self.code)
        return real(self)
    monkeypatch.setattr(urllib.error.HTTPError, 'close', close)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Refusing)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(roku.Blocked):
            roku._get(f'http://127.0.0.1:{srv.server_address[1]}/query/apps')
    finally:
        srv.shutdown()
        srv.server_close()
    assert 403 in closed


def test_no_connection_once_the_outer_call_is_out_of_time(monkeypatch):
    made = []
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', lambda self: made.append(1))
    outer = roku._Deadline(60)
    outer.expired = True                            # the outer call's time is up ...
    inner = roku._Deadline(60, outer)               # ... while the inner one has plenty (by its own clock)
    monkeypatch.setattr(roku._current, 'deadline', inner, raising=False)
    with pytest.raises(socket.timeout):
        roku._Connection('127.0.0.1', 9, timeout=5).connect()
    assert made == [] and inner.cut



# ------------------------------------------------------------------ bff131d review
def test_a_password_problem_doesnt_stick_to_later_failures(qapp, tmp_path, monkeypatch):
    outcomes = iter([roku.UnusablePassword('A Roku developer password uses only plain letters, digits and symbols.'),
                     OSError('The TV answered 500')])

    class Flaky(FakeNet):
        def install(self, ip, pw, data):
            raise next(outcomes)
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:kept'})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=Flaky({'10.0.0.6': {
        'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}}))
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('fine', True))
    monkeypatch.setattr(d.net, 'unprotect', lambda token: 'sécret')          # a bad one was stored somehow
    select(d, '10.0.0.6')
    d.install_current()                                                     # unusable, then asked again ...
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED and 'answered 500' in d.tv['10.0.0.6']['msg'])
    QApplication.processEvents()
    assert "can't be used" not in d.detail.text() and 'Check the TV is on' in d.detail.text()


def test_an_unusable_password_doesnt_forget_the_stored_one(qapp, tmp_path, monkeypatch):
    class Unusable(FakeNet):
        def install(self, ip, pw, data):
            raise roku.UnusablePassword('A Roku developer password uses only plain letters, digits and symbols.')
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:kept'})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=Unusable({'10.0.0.6': {
        'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}}))
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    answers = iter([('sécret', True), ('', False)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(answers))
    monkeypatch.setattr(d.net, 'unprotect', lambda token: None)              # so it asks
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED)
    QApplication.processEvents()
    assert lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:kept'}



# ------------------------------------------------------------------ bae4a45 review
def test_no_connection_once_the_outer_calls_own_clock_has_run_out(monkeypatch):
    made = []
    monkeypatch.setattr(roku.http.client.HTTPConnection, 'connect', lambda self: made.append(1))
    outer = roku._Deadline(60)
    outer.end = roku.time.monotonic() - 1           # past its end, though the watchdog hasn't said so
    inner = roku._Deadline(60, outer)
    monkeypatch.setattr(roku._current, 'deadline', inner, raising=False)
    with pytest.raises(socket.timeout):
        roku._Connection('127.0.0.1', 9, timeout=5).connect()
    assert made == [] and not outer.expired


def _unusable_dialog(qapp, tmp_path, monkeypatch):
    class Unusable(FakeNet):
        def install(self, ip, pw, data):
            raise roku.UnusablePassword('A Roku developer password uses only plain letters, digits and symbols.')
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=Unusable({'10.0.0.6': {
        'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}}))
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    return d, lib


def test_an_unusable_stored_password_is_forgotten(qapp, tmp_path, monkeypatch):
    d, lib = _unusable_dialog(qapp, tmp_path, monkeypatch)
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:sécret'})   # (somehow) a stored one
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('', False))
    select(d, '10.0.0.6')
    d.install_current()
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED)
    QApplication.processEvents()
    assert lib.data['tv_passwords'] == {}


def test_an_unusable_password_while_closed_is_asked_on_reopen(qapp, tmp_path, monkeypatch):
    d, lib = _unusable_dialog(qapp, tmp_path, monkeypatch)
    prompts = []
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: prompts.append(a[2]) or ('sécret', True))
    select(d, '10.0.0.6')
    d.release()                                     # closed before the result is in
    d._start_install('10.0.0.6', 'sécret', False)
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED)
    QApplication.processEvents()
    assert prompts == [] and d.tv['10.0.0.6'].get('ask_again')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: prompts.append(a[2]) or ('', False))
    d.reopen()
    wait_for(lambda: prompts)
    assert 'plain letters' in prompts[0]


def test_a_re_ask_the_dialog_closed_before_is_kept(qapp, tmp_path, monkeypatch):
    d, lib = _unusable_dialog(qapp, tmp_path, monkeypatch)
    d.tv['10.0.0.6'].update(status=tvsetup.FAILED, msg='x', pw_problem=True)
    d.closing = True                                # closed between the result and the scheduled re-ask
    d._retry('10.0.0.6')
    assert d.tv['10.0.0.6'].get('ask_again')



# ------------------------------------------------------------------ 9a98fcd review
def test_a_typed_unusable_password_keeps_a_good_stored_one(qapp, tmp_path, monkeypatch):
    class Unusable(FakeNet):
        def install(self, ip, pw, data):
            raise roku.UnusablePassword('A Roku developer password uses only plain letters, digits and symbols.')
    monkeypatch.setattr(tvsetup.server, 'lan_ips', lambda: ['10.0.0.2'])
    lib = library.Library(str(tmp_path))
    lib.set_value('tv_passwords', {'SER-10.0.0.6@10.0.0.6': 'enc:good'})
    d = tvsetup.TvSetupDialog(None, lib, FakeServer(), net=Unusable({'10.0.0.6': {
        'info': {'name': 'TV', 'dev_mode': True}, 'app': ('', '')}}))
    wait_for(lambda: d.tv.get('10.0.0.6', {}).get('status') == tvsetup.READY)
    answers = iter([('sécret', True), ('', False)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(answers))
    d.tv['10.0.0.6'].update(status=tvsetup.FAILED, msg='x', pw_problem=True)
    d._retry('10.0.0.6')                            # the retry prompt: a typed password, unusable
    wait_for(lambda: d.tv['10.0.0.6']['status'] == tvsetup.FAILED and d.tv['10.0.0.6'].get('msg') != 'x')
    QApplication.processEvents()
    assert 'plain letters' in d.tv['10.0.0.6']['msg']                        # it was the unusable password
    assert lib.data['tv_passwords'] == {'SER-10.0.0.6@10.0.0.6': 'enc:good'}   # a typed one never forgets it
