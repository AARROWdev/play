"""PLAY for Windows. Run: pythonw play_main.py  (--tray starts hidden in the tray)."""
import sys

from play.app import main

if __name__ == '__main__':
    sys.exit(main())
