"""docs/library.png: the library window over a made-up demo library (tiny placeholder files in a temp folder)."""
import os
import shutil
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from play import app as pa, library, server  # noqa: E402

DEMO = {
    'Movies': ['Paddington (2014).mkv', 'Paddington 2 (2017).mkv', 'Finding Nemo (2003).mp4', 'Up (2009).mkv',
               'The Incredibles (2004).mkv', 'Coco (2017).mp4', 'Moana (2016).mkv', 'Zootopia (2016).mp4',
               'Frozen (2013).mkv', 'Kung Fu Panda (2008).mp4', 'Birthday Party Video.avi'],
    'Movies/How to Train Your Dragon': ['How to Train Your Dragon (2010).mkv',
                                        'How to Train Your Dragon 2 (2014).mkv',
                                        'How to Train Your Dragon - The Hidden World (2019).mkv'],
    'Shows/Bluey/Season 1': ['Bluey S01E01 Magic Xylophone.mkv', 'Bluey S01E02 Hospital.mkv',
                             'Bluey S01E03 Keepy Uppy.mkv', 'Bluey S01E05.mkv'],
}
KIDS = ('Frozen', 'Kung Fu Panda', 'Moana', 'Bluey')


def main():
    # a neutral path, so the screenshot shows no user name (Public Videos exists on every Windows PC)
    public = os.path.join(os.environ.get('PUBLIC', r'C:\Users\Public'), 'Videos')
    tmp = tempfile.mkdtemp(prefix='PLAY demo ', dir=public)
    media = os.path.join(tmp, 'Media')
    for sub, files in DEMO.items():
        os.makedirs(os.path.join(media, sub), exist_ok=True)
        for n, f in enumerate(files):
            path = os.path.join(media, sub, f)
            with open(path, 'wb') as fh:
                fh.write(f.encode() * 4000)

    library.SETTLE = 0                    # the demo files were written a moment ago
    server.lan_ips = lambda: ['192.168.1.20']  # a public image must not show this PC's real address
    qa = QApplication([])
    qa.setFont(QFont('Segoe UI', 9))      # offscreen Qt has no system font of its own
    lib = library.Library(os.path.join(tmp, 'store'))
    lib.set_folders([{'path': os.path.join(media, 'Movies')}, {'path': os.path.join(media, 'Shows')}])
    pa.apply_skin(qa, 'Blue')
    srv = server.PlayServer(lib, port=0, discovery_port=0)
    srv.start()
    srv.port = 8600                       # what a real install shows
    lib.scan()
    lib.set_items([i['id'] for i in lib.current() if i['title'] in KIDS or i.get('show') in KIDS], kids=True)
    # the placeholders are a few KB: show film-like sizes in the picture (display only; nothing is written)
    for n, i in enumerate(lib._items):
        i['size'] = ((380 + 37 * n) if i['kind'] == 'episode' else (1400 + 611 * n % 3100)) * 2 ** 20
    w = pa.Window(lib, srv, pa.Scanner(lib))
    w.timer.stop()
    w.refresh()
    w._reselect({i['id'] for i in lib.items(3600) if i['title'] == 'Paddington 2'})
    w._sel_changed()
    w.resize(1360, 640)
    w.show()
    out = os.path.join(ROOT, 'docs', 'library.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    QTimer.singleShot(600, lambda: (w.grab().save(out), qa.quit()))
    qa.exec()
    srv.port = 0
    srv.stop()
    shutil.rmtree(tmp, ignore_errors=True)
    print('wrote', out)


if __name__ == '__main__':
    main()
