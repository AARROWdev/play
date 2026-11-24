"""Adding media: copy files the user picked into PLAY's default folder. The originals are never touched.

A copy never leaves a half file where PLAY (or the TV) would list it: it is written to a hidden ".playpart" name
the scan ignores and renamed into place only once it is whole. If anything fails on the way, the part is removed
again. A file already in the library (the same fingerprint) isn't copied a second time.
"""
import os
import shutil
import stat
import uuid

from . import library, names

PART = '.playpart'                       # a copy still on its way (not a video extension: the scan skips it)


def free_name(folder, name):
    """A path in folder for name that isn't taken: "name", then "name [2]", "name [3]" ... (PLAY reads a
    bracketed tag as no part of the title, so the TV still says "Up", not "Up (2)")."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.lexists(os.path.join(folder, candidate)):
        n += 1
        candidate = f'{stem} [{n}]{ext}'
    return os.path.join(folder, candidate)


_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def _identity(path):
    """What identifies a folder however it is spelled (drive letter, UNC, junction): (device, file id), or its
    normcased path where the file system has no file ids."""
    try:
        st = os.stat(path)
        if st.st_ino:
            return (st.st_dev, st.st_ino)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(path))


def _inside(path, ids):
    """Is path inside one of the media folders (ids: their _identity, worked out once per call)?"""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if _identity(d) in ids:
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def destination(src, folder):
    """Where src goes in folder: an episode goes in <Show>\\Season N (Specials for season 0), as PLAY files
    shows, whether its show and season come from its name or from the folders it is in now; a movie goes in the
    folder itself."""
    anchor = os.path.splitdrive(os.path.abspath(src))[0] + os.sep
    info = names.episode_info(src, anchor)
    if not info:
        return folder
    show = ''.join(c for c in info['show'] if c not in '<>:"/\\|?*').strip(' .') or 'Show'
    if show.split('.')[0].upper() in _RESERVED:       # a name Windows won't give a folder (CON, AUX ...)
        show += '_'
    season = 'Specials' if info['season'] == 0 else f'Season {info["season"]}'
    return os.path.join(folder, show, season)


def _remove(path):
    """Remove a file, read-only or not. True when Windows took the delete (or it was gone already). A file it
    took may stay listed until another program (a thumbnailer, an antivirus) lets go of it: it still goes."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _made_dirs(dest_dir):
    """The folders os.makedirs(dest_dir) would make (deepest last)."""
    out, d = [], os.path.abspath(dest_dir)
    while not os.path.isdir(d):
        out.insert(0, d)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return out


STALE_PART = 6 * 3600     # a part file older than this was left by a copy that stopped


def _sweep(dest_dir):
    """Copies left half-made by a copy that was stopped (PLAY closed mid-copy, say): old ones only, so a copy
    another PLAY is making right now is left alone."""
    import time
    try:
        for e in os.scandir(dest_dir):
            if e.name.endswith(PART):
                try:
                    if time.time() - e.stat().st_mtime > STALE_PART:
                        _remove(e.path)
                except OSError:
                    pass
    except OSError:
        pass


def _copy_one(src, dest_dir):
    """Copy src into dest_dir under a free name; returns the new path. Raises OSError, leaving nothing behind
    (no part file, no new empty folder). The original is only read."""
    if not os.path.isfile(src):
        raise FileNotFoundError(2, 'the file is not there any more')
    made = _made_dirs(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    part = os.path.join(dest_dir, f'.{uuid.uuid4().hex}{PART}')
    try:
        shutil.copy2(src, part)
        try:                                     # (a copy from a disc or card comes read-only: make it writable)
            os.chmod(part, stat.S_IMODE(os.stat(part).st_mode) | stat.S_IWRITE)
        except OSError:
            pass
        while True:                              # into place under a name nobody took meanwhile
            final = free_name(dest_dir, os.path.basename(src))
            try:
                os.rename(part, final)
                return final
            except FileExistsError:
                continue
    except OSError:
        _remove(part)
        for d in reversed(made):                 # folders made for it, now empty again
            try:
                os.rmdir(d)
            except OSError:
                break
        raise


def needed_space(paths, roots=()):
    """Bytes the copies of these files would take: video files outside the media folders (roots), which are the
    ones that get copied (files that can't be read count as nothing)."""
    ids = {_identity(r) for r in roots}
    total, seen = 0, set()
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue                             # (the same path picked twice is copied once)
        seen.add(key)
        if os.path.splitext(p)[1].lower() not in names.VIDEO_EXTS or (ids and _inside(p, ids)):
            continue
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def copy_files(paths, folder, roots=(), progress=None, fingerprints=False, known=(), hidden=()):
    """Copy each video file into folder (episodes into <Show>\\Season N); the originals stay where they are.
    Files already inside a media folder (roots), files already in the library (known: its fingerprints) and
    anything that isn't a video file aren't copied. progress(i, total, name) is called before each. Returns
    ([(new path, fingerprint or None)], [(path, why it wasn't copied)])."""
    ids = {_identity(r) for r in (list(roots) or [folder])}
    known = set(known)
    picked = set()                               # (the same path picked twice)
    swept, added, failed = set(), [], []
    for i, src in enumerate(paths):
        if progress:
            progress(i, len(paths), os.path.basename(src))
        if os.path.splitext(src)[1].lower() not in names.VIDEO_EXTS:
            failed.append((src, 'not a video file PLAY plays'))
            continue
        key = os.path.normcase(os.path.abspath(src))
        if key in picked:
            continue
        picked.add(key)
        fp = None
        try:                                     # (first and last 64 KB: cheap, and it catches the same film twice)
            fp = library.fingerprint(src, os.path.getsize(src))
        except OSError:
            pass                                 # (copied all the same; a Kids mark waits for a later scan)
        if _inside(src, ids):
            dest = src                           # already in a media folder: PLAY lists it where it is (and marks it)
        elif fp is not None and fp in known:
            failed.append((src, 'already in your library' + (' (hidden: unhide it in the Hidden view)' if fp in hidden else '')))
            continue
        else:
            try:
                where = destination(src, folder)
                if where not in swept:
                    swept.add(where)
                    _sweep(where)
                dest = _copy_one(src, where)
            except OSError as e:
                failed.append((src, e.strerror or str(e)))
                continue
        if fp is not None:
            known.add(fp)                        # (the same file picked twice is copied once)
        added.append((dest, fp if fingerprints else None))
    return added, failed
