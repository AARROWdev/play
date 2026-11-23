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
    'Movies': ['Beach Day (2019).mp4', 'First Steps (2018).mp4', "Grandpa's 80th (2021).mp4",
               'Backyard Camping (2020).mp4', 'School Play (2022).mp4', 'Snow Day (2021).mp4',
               'Lake House Weekend (2023).mp4', 'Science Fair (2024).mp4', 'Old Tape Transfer.avi'],
    'Movies/Christmas Mornings': ['Christmas Morning (2019).mp4', 'Christmas Morning (2020).mp4',
                                  'Christmas Morning (2021).mp4'],
    'Shows/Family Vlog/Season 1': ['Family Vlog S01E01.mp4', 'Family Vlog S01E02.mp4', 'Family Vlog S01E03.mp4',
                                   'Family Vlog S01E05.mp4'],
}
KIDS = ('First Steps', 'Backyard Camping', 'Snow Day', 'Family Vlog')


class DemoServer:
    """Stands in for PlayServer: the window only reads its state, and nothing listens on the network."""
    running, port, error, discovery_error = True, 8600, None, None   # (8600: what a real install shows)

    def tvs(self):
        return []

    def refused_hosts(self):
        return []

    def start(self):
        pass

    def stop(self):
        pass


def main():
    # a neutral path, so the screenshot shows no user name (Public Videos exists on every Windows PC)
    public = os.path.join(os.environ.get('PUBLIC', r'C:\Users\Public'), 'Videos')
    tmp = tempfile.mkdtemp(prefix='PLAY demo ', dir=public)
    try:
        media = os.path.join(tmp, 'Media')
        for sub, files in DEMO.items():
            os.makedirs(os.path.join(media, sub), exist_ok=True)
            for f in files:
                with open(os.path.join(media, sub, f), 'wb') as fh:
                    fh.write(f.encode() * 4000)
        library.SETTLE = 0                    # the demo files were written a moment ago
        server.lan_ips = lambda: ['192.168.1.20']  # a public image must not show this PC's real address
        qa = QApplication([])
        qa.setFont(QFont('Segoe UI', 9))      # offscreen Qt has no system font of its own
        lib = library.Library(os.path.join(tmp, 'store'))
        lib.set_folders([{'path': os.path.join(media, 'Movies')}, {'path': os.path.join(media, 'Shows')}])
        pa.apply_skin(qa, 'Blue')
        lib.scan()
        lib.set_items([i['id'] for i in lib.current() if i['title'] in KIDS or i.get('show') in KIDS], kids=True)
        # the placeholders are a few KB: show video-like sizes in the picture (display only; nothing is written)
        for n, i in enumerate(lib._items):
            i['size'] = ((380 + 37 * n) if i['kind'] == 'episode' else (900 + 611 * n % 2100)) * 2 ** 20
        w = pa.Window(lib, DemoServer(), pa.Scanner(lib))
        w.timer.stop()
        w.refresh()
        w._reselect({i['id'] for i in lib.items(3600) if i['title'] == 'Lake House Weekend'})
        w._sel_changed()
        w.resize(1360, 640)
        w.show()
        out = os.path.join(ROOT, 'docs', 'library.png')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        QTimer.singleShot(600, lambda: (w.grab().save(out), qa.quit()))
        qa.exec()
        print('wrote', out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    main()
