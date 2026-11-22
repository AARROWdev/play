"""Write assets/play.ico (16-256 px) from the same drawing the app uses for its window and tray icon."""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QGuiApplication  # noqa: E402

from play.app import play_icon  # noqa: E402

if __name__ == '__main__':
    app = QGuiApplication([])
    out = os.path.join(ROOT, 'assets', 'play.ico')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pm = play_icon().pixmap(256, 256)
    if not pm.save(out, 'ICO'):
        sys.exit('could not write ' + out)
    print('wrote', out)
