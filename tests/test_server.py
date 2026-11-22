"""Server: API, streaming with Range, discovery, no paths outside the library. Ports are picked by the OS."""
import http.client
import json
import os
import socket
import time

import pytest

from play import library, server
from test_library import make


@pytest.fixture
def srv(tmp_path):
    home, kids = tmp_path / 'home', tmp_path / 'kids'
    make(str(home), 'Jungle Park 1993.mp4', bytes(range(256)) * 1000)
    make(str(home), 'Jungle World 2015.mp4')
    make(str(home), 'Ann Droid S01E01.mkv')
    make(str(kids), 'Toy Shelf.mp4')
    (tmp_path / 'secret.txt').write_text('outside the library')
    lib = library.Library(str(tmp_path / 'store'))
    lib.set_folders([{'path': str(home)}, {'path': str(kids), 'kids': True}])
    s = server.PlayServer(lib, port=0, discovery_port=0, accent=lambda: '#ff8a1f')
    assert s.start()
    yield s
    s.stop()


def get(s, path, headers=None, method='GET'):
    c = http.client.HTTPConnection('127.0.0.1', s.port, timeout=5)
    c.request(method, path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def jp(s):
    return next(i for i in s.lib.items(0) if i['title'] == 'Jungle Park')


def test_info_and_accent(srv):
    r, b = get(srv, '/api/info')
    d = json.loads(b)
    assert r.status == 200 and d['name'] == 'PLAY' and d['accent'] == '#ff8a1f'


def test_tv_home_and_kids(srv):
    home = json.loads(get(srv, '/api/tv?side=home')[1])
    assert {m['title'] for m in home['movies']} == {'Jungle Park', 'Jungle World', 'Toy Shelf'}
    assert home['shows'][0]['name'] == 'Ann Droid'
    kids = json.loads(get(srv, '/api/tv?side=kids')[1])
    assert [m['title'] for m in kids['movies']] == ['Toy Shelf'] and kids['shows'] == []


def test_full_and_range_requests(srv):
    data = open(jp(srv)['path'], 'rb').read()
    url = '/media/' + jp(srv)['id']
    r, b = get(srv, url)
    assert r.status == 200 and b == data and r.getheader('Accept-Ranges') == 'bytes'
    r, b = get(srv, url, {'Range': 'bytes=100-199'})
    assert r.status == 206 and b == data[100:200] and r.getheader('Content-Range') == f'bytes 100-199/{len(data)}'
    r, b = get(srv, url, {'Range': 'bytes=-50'})
    assert r.status == 206 and b == data[-50:]
    r, b = get(srv, url, {'Range': f'bytes={len(data) - 10}-'})
    assert b == data[-10:]
    r, b = get(srv, url, {'Range': f'bytes={len(data)}-'})
    assert r.status == 416 and r.getheader('Content-Range') == f'bytes */{len(data)}'


def test_head_sends_no_body(srv):
    r, b = get(srv, '/media/' + jp(srv)['id'], method='HEAD')
    assert r.status == 200 and b == b'' and int(r.getheader('Content-Length')) == 256000


def test_keepalive_serves_two_requests_on_one_connection(srv):
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=5)
    for _ in range(2):
        c.request('GET', '/media/' + jp(srv)['id'], headers={'Range': 'bytes=0-9'})
        assert c.getresponse().read() == bytes(range(10))
    c.close()


def test_renamed_file_still_streams(srv, tmp_path):
    it = jp(srv)
    os.rename(it['path'], str(tmp_path / 'home' / 'jp.mp4'))
    srv.lib._scanned -= 3              # past the 2 s rescan limit for a known file that moved
    r, b = get(srv, '/media/' + it['id'], {'Range': 'bytes=0-3'})
    assert r.status == 206 and b == bytes(range(4))


@pytest.mark.parametrize('path', ['/media/0000000000000000', '/media/../secret.txt', '/media/..%2F..%2Fsecret.txt',
                                  '/media/' + 'a' * 16 + '/x', '/secret.txt', '/api/nope'])
def test_unknown_or_escaping_paths_are_404(srv, path):
    r, b = get(srv, path)
    assert r.status == 404 and b'outside the library' not in b


def test_clients_are_counted(srv):
    get(srv, '/api/info')
    assert srv.tvs() == ['127.0.0.1']


def test_discovery_answers(srv):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.settimeout(3)
    u.sendto(b'PLAY-DISCOVER', ('127.0.0.1', srv.discovery_port))
    d = json.loads(u.recvfrom(512)[0])
    u.close()
    assert d['name'] == 'PLAY' and d['port'] == srv.port


def test_discovery_ignores_other_packets(srv):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.settimeout(1)
    u.sendto(b'hello', ('127.0.0.1', srv.discovery_port))
    with pytest.raises(socket.timeout):
        u.recvfrom(512)
    u.close()


def test_port_in_use_reports_error(srv):
    other = server.PlayServer(srv.lib, port=srv.port, discovery_port=0)
    assert other.start() is False and str(srv.port) in other.error and not other.running


def test_stop_frees_the_port(srv):
    port = srv.port
    srv.stop()
    again = server.PlayServer(srv.lib, port=port, discovery_port=0)
    assert again.start()
    again.stop()


# ------------------------------------------------------------------ review fixes (d375511)
def test_made_up_ids_dont_cause_scans(srv, monkeypatch):
    srv.lib.scan()
    calls = []
    real = srv.lib.scan
    monkeypatch.setattr(srv.lib, 'scan', lambda: calls.append(1) or real())
    for n in range(20):
        r, _ = get(srv, '/media/%016x' % (0xabc000 + n))
        assert r.status == 404
    assert calls == []


def test_unopenable_known_file_rescans_at_most_once_per_2s(srv, monkeypatch):
    it = jp(srv)
    srv.lib.scan()
    calls = []
    real = srv.lib.scan
    monkeypatch.setattr(srv.lib, 'scan', lambda: calls.append(1) or real())
    monkeypatch.setattr(srv.lib, 'path_for', lambda fp, rescan=True: it['path'] + '.gone')
    for _ in range(10):
        assert get(srv, '/media/' + it['id'])[0].status == 404
    assert calls == []                 # all within 2 s of the last scan
    srv.lib._scanned -= 3
    for _ in range(5):
        get(srv, '/media/' + it['id'])
    assert len(calls) == 1


def test_invalid_range_gets_whole_file(srv):
    data = open(jp(srv)['path'], 'rb').read()
    r, b = get(srv, '/media/' + jp(srv)['id'], {'Range': 'bytes=5-2'})
    assert r.status == 200 and b == data


def test_m2ts_type():
    assert server.TYPES['m2ts'] == 'video/mp2t'


# ------------------------------------------------------------------ baseline fixes (B5, B7, B41, B42, B53)
@pytest.mark.parametrize('host,ok', [('127.0.0.1:8600', True), ('10.0.0.5', True), ('[::1]:8600', True),
                                     ('localhost:8600', True), ('rebind.attacker.example:8600', False),
                                     ('evil.example', False), ('', True)])
def test_host_header_check(srv, host, ok):
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=5)
    c.putrequest('GET', '/api/tv?side=home', skip_host=True)
    if host:
        c.putheader('Host', host)
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    assert (r.status == 200) is ok and (b'Jungle' in body) is ok


def test_this_pcs_name_is_an_allowed_host(srv):
    assert server.host_allowed(socket.gethostname() + ':8600')


def test_discovery_survives_stray_packets(srv):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.sendto(b'x' * 4000, ('127.0.0.1', srv.discovery_port))          # oversized
    gone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)             # asks, then leaves before the answer
    gone.sendto(b'PLAY-DISCOVER', ('127.0.0.1', srv.discovery_port))
    gone.close()
    time.sleep(0.3)
    u.settimeout(3)
    u.sendto(b'PLAY-DISCOVER', ('127.0.0.1', srv.discovery_port))
    d = json.loads(u.recvfrom(512)[0])
    u.close()
    assert d['name'] == 'PLAY'


def test_discovery_port_in_use_is_reported(srv):
    other = server.PlayServer(srv.lib, port=0, discovery_port=srv.discovery_port)
    assert other.start() and other.discovery_error and str(srv.discovery_port) in other.discovery_error
    other.stop()


def test_info_has_the_pcs_address(srv):
    d = json.loads(get(srv, '/api/info')[1])
    assert 'ip' in d


def test_cut_short_response_closes_the_connection(srv, monkeypatch):
    seen = {}
    real = server._Handler._stream

    def boom(self, fp, head):
        try:
            raise TimeoutError('client stopped reading')
        finally:
            seen['h'] = self
    monkeypatch.setattr(server._Handler, '_stream', boom)
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=5)
    c.request('GET', '/media/' + jp(srv)['id'])
    try:
        c.getresponse().read()
    except (http.client.RemoteDisconnected, ConnectionError):
        pass
    c.close()
    time.sleep(0.2)
    assert seen['h'].close_connection is True
    monkeypatch.setattr(server._Handler, '_stream', real)
