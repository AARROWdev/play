"""Adding media: move files the user picked into PLAY's default folder."""
import os
import shutil

from . import library


def free_name(folder, name):
    """name in folder, or "name (2)", "name (3)" ... if it's taken: nothing already there is overwritten."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.lexists(os.path.join(folder, candidate)):
        n += 1
        candidate = f'{stem} ({n}){ext}'
    return os.path.join(folder, candidate)


def move_files(paths, folder, progress=None):
    """Move each file into folder (across drives: copied, then the original removed). progress(i, total, name) is
    called before each. Returns ([(new path, fingerprint)], [(path, why it failed)])."""
    moved, failed = [], []
    for i, src in enumerate(paths):
        if progress:
            progress(i, len(paths), os.path.basename(src))
        try:
            if os.path.normcase(os.path.dirname(os.path.abspath(src))) == os.path.normcase(os.path.abspath(folder)):
                dest = src                               # already there
            else:
                dest = free_name(folder, os.path.basename(src))
                shutil.move(src, dest)
            moved.append((dest, library.fingerprint(dest, os.path.getsize(dest))))
        except OSError as e:
            failed.append((src, e.strerror or str(e)))
    return moved, failed
