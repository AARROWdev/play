"""Adding media: move files the user picked into PLAY's default folder.

A move never leaves a half file where PLAY (or the TV) would list it: across drives (or when Windows won't just
rename the file) it is copied to a hidden ".playpart" name the scan ignores, renamed into place only once the copy
is whole, and the original is removed last. If anything fails on the way, the copy is removed again and the
original stays where it was.
"""
import os
import shutil
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


def _same_place(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _inside(path, roots):
    """Is path inside one of the media folders (however they are spelled: drive letter, UNC, junction)?"""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if any(_same_place(d, r) for r in roots):
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
    season = 'Specials' if info['season'] == 0 else f'Season {info["season"]}'
    return os.path.join(folder, show, season)


def _move_one(src, dest_dir):
    """Move src into dest_dir under a free name; returns the new path. Raises OSError, leaving nothing behind."""
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(src)
    try:                                         # same drive: a rename (it never overwrites on Windows)
        final = free_name(dest_dir, name)
        os.rename(src, final)
        return final
    except OSError:
        pass
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
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    try:
        os.remove(src)                           # last: only once the copy is in place
    except OSError:
        try:
            os.remove(final)                     # the original stays: don't leave two copies
        except OSError:
            pass
        raise
    return final


def move_files(paths, folder, roots=(), progress=None, fingerprints=False):
    """Move each video file into folder (episodes into <Show>\\Season N). Files already inside a media folder
    (roots) and anything that isn't a video file are left where they are. progress(i, total, name) is called
    before each. Returns ([(new path, fingerprint or None)], [(path, why it wasn't moved)])."""
    roots = list(roots) or [folder]
    moved, failed = [], []
    for i, src in enumerate(paths):
        if progress:
            progress(i, len(paths), os.path.basename(src))
        if os.path.splitext(src)[1].lower() not in names.VIDEO_EXTS:
            failed.append((src, 'not a video file PLAY plays'))
            continue
        if _inside(src, roots):
            dest = src                           # already in a media folder: PLAY lists it where it is
        else:
            try:
                dest = _move_one(src, destination(src, folder))
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
