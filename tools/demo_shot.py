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
    'Movies': ['Nosferatu (1922).mkv', 'Metropolis (1927).mkv', 'The General (1926).mp4', 'Charade (1963).mkv',
               'His Girl Friday (1940).mp4', 'Night of the Living Dead (1968).mkv', 'Old Holiday Tape.avi'],
    'Movies/The Thin Man': ['The Thin Man (1934).mkv', 'After the Thin Man (1936).mkv',
                            'Another Thin Man (1939).mkv', 'Shadow of the Thin Man (1941).mkv'],
    'Shows/Star Harbor/Season 1': ['Star Harbor S01E01.mkv', 'Star Harbor S01E02.mkv', 'Star Harbor S01E03.mkv',
                                   'Star Harbor S01E05.mkv'],
    'Kids': ["Gulliver's Travels (1939).mp4", 'Popeye the Sailor Meets Sindbad (1936).mp4'],
}


def main():
    # a neutral path, so the screenshot shows no user name (Public Videos exists on every Windows PC)
    public = os.path.join(os.environ.get('PUBLIC', r'C:\Users\Public'), 'Videos')
    tmp = tempfile.mkdtemp(prefix='PLAY demo ', dir=public)
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
    lib.set_folders([{'path': os.path.join(media, 'Movies')}, {'path': os.path.join(media, 'Shows')},
                     {'path': os.path.join(media, 'Kids'), 'kids': True}])
    pa.apply_skin(qa, 'Blue')
    srv = server.PlayServer(lib, port=0, discovery_port=0)
    srv.start()
    srv.port = 8600                       # what a real install shows
    lib.scan()
    w = pa.Window(lib, srv, pa.Scanner(lib))
    w.timer.stop()
    w.refresh()
    w._reselect({i['id'] for i in lib.items(3600) if i['title'] == 'Metropolis'})
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
