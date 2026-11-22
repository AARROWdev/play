"""Write assets/play.ico with 16, 20, 24, 32, 40, 48, 64, 128 and 256 px images, each drawn at its own size from the
same drawing the app uses for its window and tray icon (PNG-compressed entries, which Windows Vista+ reads)."""
import os
import struct
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from play.app import play_pixmap  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(size):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    play_pixmap(None, size).toImage().save(buf, 'PNG')
    buf.close()
    return bytes(data)


def ico(images):
    """ICONDIR + one ICONDIRENTRY per image + the PNG data."""
    out = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    body = b''
    for size, png in images:
        dim = 0 if size >= 256 else size           # 0 means 256 in an ICONDIRENTRY
        out += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(png), offset + len(body))
        body += png
    return out + body


if __name__ == '__main__':
    app = QGuiApplication([])
    out = os.path.join(ROOT, 'assets', 'play.ico')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(ico([(s, png_bytes(s)) for s in SIZES]))
    print('wrote', out, len(SIZES), 'sizes')
