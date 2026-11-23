"""Adding media: move files the user picked into PLAY's default folder.

A move never leaves a half file where PLAY (or the TV) would list it: across drives (or when Windows won't just
rename the file) it is copied to a hidden ".playpart" name the scan ignores, renamed into place only once the copy
is whole, and the original is removed last. If anything fails on the way, the copy is removed again and the
original stays where it was.
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


STALE_PART = 6 * 3600     # a part file older than this was left by a move that stopped


def _sweep(dest_dir):
    """Copies left half-made by a move that was stopped (PLAY closed mid-copy, say): old ones only, so a copy
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


def _move_one(src, dest_dir):
    """Move src into dest_dir under a free name; returns the new path. Raises OSError, leaving nothing behind
    (no copy, no file of its own, no new empty folder)."""
    if not os.path.isfile(src):
        raise FileNotFoundError(2, 'the file is not there any more')
    made = _made_dirs(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        return _move_into(src, dest_dir)
    except OSError:
        for d in reversed(made):                    # folders made for it, now empty again
            try:
                os.rmdir(d)
            except OSError:
                break
        raise


def _move_into(src, dest_dir):
    name = os.path.basename(src)
    try:                                         # same drive: a rename (it never overwrites on Windows)
        final = free_name(dest_dir, name)
        os.rename(src, final)
        return final
    except OSError:
        pass
    if not os.access(src, os.W_OK):              # read-only (a disc, a card, a share): it can't be moved away
        raise PermissionError(13, 'read-only: it can be copied, not moved')
    part = os.path.join(dest_dir, f'.{uuid.uuid4().hex}{PART}')
    try:
        shutil.copy2(src, part)
        while True:                              # into place under a name nobody took meanwhile
            final = free_name(dest_dir, name)
            try:
                os.rename(part, final)
                break
            except FileExistsError:
                continue
    except OSError:
        _remove(part)
        raise
    if not _remove(src):                         # last: only once the copy is in place
        if not _remove(final):                   # the original stays: don't leave two copies
            raise OSError(f'could not move it, and the copy at {final} could not be removed')
        raise PermissionError(13, 'the original could not be removed, so it was not moved')
    return final


def move_files(paths, folder, roots=(), progress=None, fingerprints=False):
    """Move each video file into folder (episodes into <Show>\\Season N). Files already inside a media folder
    (roots) and anything that isn't a video file are left where they are. progress(i, total, name) is called
    before each. Returns ([(new path, fingerprint or None)], [(path, why it wasn't moved)])."""
    ids = {_identity(r) for r in (list(roots) or [folder])}
    swept, moved, failed = set(), [], []
    for i, src in enumerate(paths):
        if progress:
            progress(i, len(paths), os.path.basename(src))
        if os.path.splitext(src)[1].lower() not in names.VIDEO_EXTS:
            failed.append((src, 'not a video file PLAY plays'))
            continue
        if _inside(src, ids):
            dest = src                           # already in a media folder: PLAY lists it where it is
        else:
            try:
                where = destination(src, folder)
                if where not in swept:
                    swept.add(where)
                    _sweep(where)
                dest = _move_one(src, where)
            except OSError as e:
                failed.append((src, e.strerror or str(e)))
                continue
        fp = None
        if fingerprints:                         # (only when it's needed: marking them Kids)
            try:
                fp = library.fingerprint(dest, os.path.getsize(dest))
            except OSError:
                pass                             # moved all the same; the mark waits for a later scan
        moved.append((dest, fp))
    return moved, failed
