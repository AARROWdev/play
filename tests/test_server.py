"""Server: API, streaming with Range, discovery, no paths outside the library. Ports are picked by the OS."""
import http.client
import json
import os
import socket

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
