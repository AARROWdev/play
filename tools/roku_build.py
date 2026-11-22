"""Package the Roku app, and optionally sideload it onto a TV in developer mode.

    python tools/roku_build.py                    -> dist/PLAY-roku.zip
    python tools/roku_build.py 192.168.1.50       -> zip + install on that TV (asks for the developer password,
                                                     or reads ROKU_DEV_PASSWORD)

Installing replaces whatever developer app is on that TV (a Roku holds one at a time).
"""
import getpass
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'roku')
OUT = os.path.join(ROOT, 'dist', 'PLAY-roku.zip')


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(APP):
            for f in sorted(files):
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, APP).replace(os.sep, '/'))
    print('built', OUT)


def install(tv):
    import requests
    from requests.auth import HTTPDigestAuth
    pw = os.environ.get('ROKU_DEV_PASSWORD') or getpass.getpass('Roku developer password: ')
    with open(OUT, 'rb') as f:
        r = requests.post(f'http://{tv}/plugin_install', auth=HTTPDigestAuth('rokudev', pw),
                          files={'archive': ('PLAY-roku.zip', f, 'application/zip')},
                          data={'mysubmit': 'Install'}, timeout=120)
    msgs = re.findall(r'"text":"([^"]+)"', r.text) or re.findall(r'<font color="red">(.*?)</font>', r.text)
    print('install:', r.status_code, '|', ' / '.join(msgs) or r.text[:200])


if __name__ == '__main__':
    build()
    if len(sys.argv) > 1:
        install(sys.argv[1])
