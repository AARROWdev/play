"""The PLAY library: what's in the media folders, and what you told PLAY about it.

Media files are only ever read. Everything you set in the app (Kids, title, movie/show, hidden, collections)
lives in library.json in the settings folder, keyed by a fingerprint of each file's contents, so it follows a
file that is moved or renamed.

Two kinds of work, kept apart so streaming never waits on the disks:
- scan() walks the folders (slow: directories are listed, new files fingerprinted). The walk holds no lock that a
  reader needs; only the swap of its result does.
- _rebuild() turns the last walk plus your settings into items (fast, in memory). Every edit uses only this.
Lock order: _scan_lock, then lock. Nothing ever asks for a walk while holding lock.
"""
import hashlib
import json
import math
import os
import re
import shutil
import stat as statmod
import threading
import time
import uuid

from . import names, skins

SAMPLE = 64 * 1024
OVERRIDABLE = ('title', 'year', 'kind', 'show', 'season', 'episode', 'kids', 'hidden')
DEFAULT_PORT, PORT_MIN, PORT_MAX = 8600, 1024, 65535
ERROR_GRACE = 24 * 3600   # a folder or file unreadable for longer is how it is now, not a hiccup
SETTLE = 10.0       # a new file must look the same for this long (by this PC's clock) before it is fingerprinted
STARTUP_AGE = 60.0  # at startup, files last written more than a minute ago are taken at once
STOP_WORDS = {'the', 'and', 'of', 'a', 'an', 'movies', 'movie', 'films', 'film', 'collection', 'series', 'set',
              'box', 'complete', 'best', 'all'}
_OLD_EP_TITLE = re.compile(r'^.+ S\d{2}E\d{2,3}$')


def default_store_dir():
    return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'PLAY')


def fingerprint(path, size):
    """16 hex chars from the size and the first and last 64 KB. Same file anywhere = same id."""
    h = hashlib.sha1(str(size).encode())
    with open(path, 'rb') as f:
        h.update(f.read(SAMPLE))
        if size > 2 * SAMPLE:
            f.seek(size - SAMPLE)
            h.update(f.read(SAMPLE))
    return h.hexdigest()[:16]


def valid(key, v):
    """Is v an allowed value for an item field? Guards both edits and hand-edited library.json."""
    if key in ('title', 'show'):
        return isinstance(v, str) and v.strip() != '' or (key == 'show' and v is None)
    if key in ('year', 'season', 'episode'):
        return v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9999)
    if key in ('kids', 'hidden'):
        return isinstance(v, bool)
    if key == 'kind':
        return v in ('movie', 'episode')
    return False


def parse_port(v):
    """A port from settings: an int, or text of plain digits ("8700"), in range. None otherwise."""
    if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        v = int(v.strip())
    if isinstance(v, int) and not isinstance(v, bool) and PORT_MIN <= v <= PORT_MAX:
        return v
    return None


def valid_port(v):
    return isinstance(v, int) and parse_port(v) is not None


def release_key(item):
    """Release order: by year (no year last), then title. The one rule for collections everywhere."""
    return item.get('year') or 9999, names.sort_key(item['title'])


def _dict(v):
    return v if isinstance(v, dict) else {}


def _write_json(path, data):
    """Atomic write. ensure_ascii: a file name with a lone surrogate (legal on Windows) can't break the save."""
    _write_text(path, json.dumps(data, indent=1, ensure_ascii=True))


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def _parse_file(path):
    """The JSON in path, as UTF-8 (with or without a BOM) or UTF-16 (PowerShell's > writes that).
    Raises OSError if it can't be read, ValueError if it isn't JSON."""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-16'):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError('not JSON')


def _read_json(path, default):
    try:
        d = _parse_file(path)
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _good_backup(path):
    """The settings in library.json.bak, or None if there is no usable copy."""
    try:
        d = _parse_file(path + '.bak')
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d else None


def load_store(path):
    """(settings, problem message or None, read_only).

    - Missing library.json: a fresh start, unless library.json.bak holds settings (then they are restored).
    - Can't be opened (a virus scanner or backup holding it): retried for 2 s; then PLAY runs read-only on the
      backup (or on nothing), so it can never write over the file it couldn't read.
    - Not valid JSON (a bad hand edit): kept as library.json.unreadable-<hash> and the backup is used.
    library.json.bak is always a copy of the last good save (see Library.save)."""
    if not os.path.exists(path):
        bak = _good_backup(path)
        if bak is not None:
            return bak, 'PLAY\'s settings file was missing, so the backup copy was restored.', False
        return {}, None, False
    err, locked = None, False
    for _ in range(10):
        try:
            d = _parse_file(path)
            if isinstance(d, dict):
                return d, None, False
            err = 'is not a settings object'
            break
        except OSError as e:
            err, locked = str(e), True
            time.sleep(0.2)
        except ValueError:
            err, locked = 'is not valid JSON', False
            break
    bak = _good_backup(path)
    if locked:
        msg = (f'PLAY could not open its settings file ({err}), so it won\'t save changes until it is restarted '
               'once whatever holds the file lets go.')
        return (bak or {}), msg + (' Your last saved settings are shown.' if bak else ''), True
    kept = None
    try:
        with open(path, 'rb') as f:
            tag = hashlib.sha1(f.read()).hexdigest()[:8]
        kept = f'{path}.unreadable-{tag}'
        if not os.path.exists(kept):               # the same broken file is kept once, not on every start
            shutil.copy2(path, kept)
    except OSError:
        kept = None
    msg = f'PLAY could not read its settings file ({err}).'
    if kept:
        msg += f' It was kept as {os.path.basename(kept)}.'
    if bak is not None:
        return bak, msg + ' The last good copy was loaded instead.', False
    return {}, msg + ' PLAY started with empty settings.', False


def _ep_title(show, season, episode, last=None):
    if last and last > episode:
        return f'{show} S{season:02d}E{episode:02d}-E{last:02d}'
    return f'{show} S{season:02d}E{episode:02d}'


class Library:
    def __init__(self, store_dir=None):
        self.dir = store_dir or default_store_dir()
        self.path = os.path.join(self.dir, 'library.json')
        self.cache_path = os.path.join(self.dir, 'fingerprints.json')
        self.lock = threading.RLock()          # guards data and items; held only for in-memory work
        self._scan_lock = threading.Lock()     # one disk walk at a time; taken before lock, never inside it
        self._write_lock = threading.Lock()    # one settings write at a time (taken without lock held)
        self._save_seq = self._saved_seq = 0
        existed = os.path.exists(self.path) or os.path.exists(self.path + '.bak')
        d, self.load_problem, self.read_only = load_store(self.path)
        folders, seen = [], set()
        for f in (d.get('folders') if isinstance(d.get('folders'), list) else []):
            if isinstance(f, dict) and isinstance(f.get('path'), str) and f['path'].strip():
                key = os.path.normcase(os.path.abspath(f['path']))
                if key not in seen:                   # 'N:\Media' and 'N:\MEDIA\' are one folder
                    seen.add(key)
                    folders.append({'path': os.path.abspath(f['path']), 'kids': f.get('kids') is True})
        self.data = {
            'version': 1,
            'folders': folders,
            'port': parse_port(d.get('port')) or DEFAULT_PORT,
            'skin': skins.name(d.get('skin')),
            'items': {k: v for k, v in _dict(d.get('items')).items() if isinstance(v, dict)},
            'collections': {k: v for k, v in _dict(d.get('collections')).items()
                            if isinstance(v, dict) and isinstance(v.get('name'), str)
                            and isinstance(v.get('members'), list)
                            and all(isinstance(m, str) for m in v['members'])},
            'dismissed': [x for x in d.get('dismissed', []) if isinstance(x, str)]
                         if isinstance(d.get('dismissed'), list) else [],
            # normcased path -> when it was first found unreadable (wall clock): see ERROR_GRACE
            'unreadable_since': {k: t for k, v in _dict(d.get('unreadable_since')).items()
                                 if isinstance(k, str) and (t := _finite(v)) is not None},
            # set once the installed PLAY has turned on "Start with Windows" (switching it off later sticks)
            # (v1.0 is the first public build, so a library.json without the flag comes from before the flag;
            # its user already chose, and a broken file still means "not a first start")
            'autostart_offered': d.get('autostart_offered') is True or (existed and 'autostart_offered' not in d),
        }
        self._migrate_old_episode_titles()
        self.fpcache = {k: v for k, v in _read_json(self.cache_path, {}).items()
                        if isinstance(v, list) and len(v) == 3 and isinstance(v[2], str)}
        self._raw = {}             # normcased root -> [(fp, path, ext, size, real)] from the last walk reaching it
        self._into = {}            # normcased root -> real folders of other media folders it reaches (links,
                                   # or one nested in it): files there are Kids when this folder is
        self._incomplete = False   # the last scan couldn't read everything (folders, links or subfolders)
        self._live_roots = set()   # roots the last walk reached (their files win over kept ones)
        self._raw_sig = None
        self._pending = {}         # normcased path -> [size, mtime_ns, first seen (monotonic)] of an unsettled file
        self._items = []
        self._by_id = {}
        self._col_of = {}
        self._scanned = 0          # monotonic time the last scan finished (0: never)
        self._gen = 0              # number of finished scans
        self._last_digest = None
        self._save_after_scan = False
        self.version = 0           # bumps when the items really change; the window rebuilds on it
        self.unreachable = []      # folders the last scan couldn't open (their titles are kept from before)
        if self.load_problem and not self.read_only and d:
            try:
                self.save()        # write the recovered settings back, so the next start is clean
            except OSError:
                pass

    def _migrate_old_episode_titles(self):
        """Early builds stored "<Show> SxxEyy" as a title override whenever an episode was set. That froze the
        title; drop such overrides so the title follows the numbers again (a title you typed yourself is kept)."""
        for ov in self.data['items'].values():
            t = ov.get('title')
            if isinstance(t, str) and _OLD_EP_TITLE.match(t) and any(k in ov for k in ('show', 'season', 'episode')):
                del ov['title']

    # ------------------------------------------------------------------ settings
    def save(self):
        """Write library.json, then copy it to library.json.bak, so the backup is always the last good save (and a
        broken file is never copied over it). Read-only after a load that couldn't open the file: no writes.
        The settings are copied under the lock; the disk write happens after it (callers save after their lock
        block), so the server and the window never wait on it. Writes are serialised, and an older snapshot never
        lands after a newer one."""
        if self.read_only:
            return
        with self.lock:
            blob = json.dumps(self.data, indent=1, ensure_ascii=True)
            self._save_seq += 1
            seq = self._save_seq
        with self._write_lock:
            if seq < self._saved_seq:
                return
            self._saved_seq = seq
            _write_text(self.path, blob)
            try:
                shutil.copy2(self.path, self.path + '.bak')
            except OSError:
                pass

    @property
    def folders(self):
        return list(self.data['folders'])

    def set_folders(self, folders):
        """folders: [{'path': str, 'kids': bool}]. Files in a Kids folder start out tagged Kids. The next scan
        (the window starts one) picks the change up; nothing here touches the disks."""
        with self.lock:
            seen, out = set(), []
            for f in folders:
                if not isinstance(f.get('path'), str) or not f['path'].strip():
                    raise ValueError(f'folder path cannot be {f.get("path")!r}')
                p = os.path.abspath(f['path'])
                if os.path.normcase(p) not in seen:
                    seen.add(os.path.normcase(p))
                    out.append({'path': p, 'kids': f.get('kids') is True})
            changed = out != self.data['folders']
            self.data['folders'] = out
            if changed:
                self._rebuild()                      # a removed folder's titles go at once
        self.save()

    def set_value(self, key, value):
        if key == 'port' and not valid_port(value):
            raise ValueError(f'port cannot be {value!r}')
        if key == 'skin' and not (isinstance(value, str) and value in skins.SKINS):
            raise ValueError(f'skin cannot be {value!r}')
        with self.lock:
            self.data[key] = value
        self.save()

    # ------------------------------------------------------------------ scanning (disk)
    def scan(self, after_gen=None):
        """Walk the media folders, then rebuild the items. Returns the items.
        after_gen: the scan count the caller saw. If a scan finished since then (another caller's), its result is
        used instead of walking again, so a burst of requests costs one walk."""
        with self._scan_lock:
            if after_gen is not None and self._gen > after_gen:
                return self._items
            with self.lock:
                folders = [dict(f) for f in self.data['folders']]
                cache = dict(self.fpcache)
                pending = dict(self._pending)
                first = self._gen == 0
                prev_raw = dict(self._raw)
            roots = [os.path.normcase(os.path.abspath(f['path'])) for f in folders]
            real_of = {}
            for f, root in zip(folders, roots):      # once per scan (a sleeping NAS answers realpath slowly)
                try:
                    real_of[root] = os.path.normcase(os.path.realpath(f['path']))
                except OSError:
                    pass
            real_roots = set(real_of.values())
            raw, reached, new_pending, dirty, errored, links_off, into = {}, set(), {}, False, [], [], {}
            by_real, listings, all_errs = {}, {}, []
            now_w = time.time()
            with self.lock:
                since = dict(self.data['unreadable_since'])
            # Titles that can't be read now are kept from the last scans, from one pool (whichever folder's walk
            # saw them then), each owned by the deepest media folder that holds its path now. A reached folder
            # keeps those under what it couldn't read (an offline link, a locked subfolder); a folder that wasn't
            # reached keeps all of its own. So nothing is lost or credited to the wrong folder when folders are
            # added, removed or changed while something is offline.
            deepest_first = sorted(((r, r.rstrip(os.sep) + os.sep) for r in set(roots)), key=lambda x: -len(x[0]))

            def owner(path):
                for r, pre in deepest_first:
                    if path == r or path.startswith(pre):
                        return r
                return None
            pool = {}
            for files in prev_raw.values():
                for e in files:
                    k = os.path.normcase(e[1])
                    pool.setdefault(owner(k), {}).setdefault(k, e)
            for f, root in zip(folders, roots):
                # media folders inside this one, with where they really are (None: not known, it's offline)
                others = {r: real_of.get(r) for r in roots
                          if r != root and r.startswith(root.rstrip(os.sep) + os.sep)}
                files, ok, changed, errs, off, into[root] = _walk(
                    f['path'], others, cache, pending, new_pending, first, real_roots, real_of.get(root), by_real, listings)
                dirty = dirty or changed
                if ok:
                    # a subfolder or link that couldn't be read keeps what it had: its titles don't vanish
                    lost = errs + [os.path.normcase(o) for o in off]
                    have = {os.path.normcase(e[1]) for e in files}
                    lost_pre = tuple(x.rstrip(os.sep) + os.sep for x in lost)
                    raw[root] = files + [e for k, e in pool.get(root, {}).items()
                                         if k not in have and lost and (k in lost or k.startswith(lost_pre))]
                    reached.add(root)
                    all_errs += lost
                    # a hiccup holds collections back; something unreadable for over a day is how it is now
                    for p in lost:                   # a time in the future (the clock was set back) starts now
                        since[p] = min(since.get(p, now_w), now_w)
                    errored += [p for p in lost if now_w - since[p] < ERROR_GRACE]
                    links_off += off
            unreachable = []
            for f, root in zip(folders, roots):
                if root not in reached:              # NAS asleep, drive not mounted yet: keep what we had
                    raw[root] = list(pool.get(root, {}).values())
                    unreachable.append(f['path'])
            live = {os.path.normcase(e[1]) for files in raw.values() for e in files}
            stale = [k for k in cache if k not in live and not _under(k, errored)
                     and (_under(k, reached) or not _under(k, roots))]
            for k in stale:
                del cache[k]
            for root in roots:
                if root not in reached:              # its links weren't looked at: keep what they reached
                    into[root] = self._into.get(root, [])
            sig = hash(tuple((r, tuple(raw[r])) for r in sorted(raw))) ^ hash(tuple(sorted(reached))) ^ \
                hash(tuple((r, tuple(sorted(into[r]))) for r in sorted(into)))
            # forget a path once it reads again. Kept: one still failing, one inside something this scan couldn't
            # read (an offline link, an unlistable folder), and one whose media folder wasn't reached (NAS asleep);
            # dropped: one in no media folder any more.
            lost_set = set(all_errs)
            lost_pre = tuple(x.rstrip(os.sep) + os.sep for x in lost_set)
            def keep_clock(p):
                if p in lost_set or p.startswith(lost_pre):
                    return True
                for r, pre in deepest_first:         # the media folder that holds it
                    if p == r or p.startswith(pre):
                        return r not in reached
                return False
            since = {p: t for p, t in since.items() if keep_clock(p)}
            with self.lock:
                self.fpcache = cache
                self._pending = new_pending
                self._raw = raw
                self._live_roots = reached
                self._into = into
                if since != self.data['unreadable_since']:
                    self.data['unreadable_since'] = since
                    self._save_after_scan = True
                # media folders and links that are offline (a subfolder Windows won't list isn't news) ...
                self.unreachable = unreachable + sorted(set(links_off))
                # ... but anything not fully read means "missing" can't be told from "gone" (collections keep it)
                self._incomplete = bool(unreachable or errored)
                if sig != self._raw_sig or first:
                    self._raw_sig = sig
                    self._rebuild()                  # nothing new on disk: the items stand as they are
                self._gen += 1
                self._scanned = time.monotonic()     # after the rebuild: readers never see "scanned" but empty
                save, self._save_after_scan = self._save_after_scan, False
                items = self._items
            if dirty or stale:                       # disk writes after the lock is released
                try:
                    _write_json(self.cache_path, cache)
                except OSError:
                    pass
            if save:
                try:
                    self.save()
                except OSError:
                    pass
            return items

    def _ensure_scanned(self):
        """The first scan, if there has never been one. Never called with self.lock held."""
        if not self._scanned:
            self.scan(after_gen=0)

    def items(self, max_age=5.0):
        """The current item list, rescanning when older than max_age seconds (or never scanned)."""
        gen = self._gen
        if not self._scanned or time.monotonic() - self._scanned > max_age:
            self.scan(after_gen=gen)
        return self._items

    def current(self):
        """The items as they are now, without touching the disks (scans once if there has never been a scan)."""
        self._ensure_scanned()
        return self._items

    def by_id(self, fp, max_age=float('inf')):
        gen = self._gen
        if not self._scanned or time.monotonic() - self._scanned > max_age:
            self.scan(after_gen=gen)
        return self._by_id.get(fp)

    def path_for(self, fp, rescan=True):
        """The file to stream for an id, from the last scan. An unknown id rescans at most every 10 seconds, so
        requests for made-up ids can't keep the disks busy."""
        it = self.by_id(fp)
        if not it and rescan and self.rescan_throttled():
            it = self.by_id(fp)
        return it['path'] if it else None

    def rescan_throttled(self, every=10.0):
        """Rescan unless the last scan is under `every` seconds old. True if it scanned (or one just finished)."""
        gen = self._gen
        if self._scanned and time.monotonic() - self._scanned < every:
            return False
        self.scan(after_gen=gen)
        return True

    # ------------------------------------------------------------------ items (memory)
    def _rebuild(self):
        """Items from the last walk plus the settings. In memory only; call with self.lock held."""
        self._col_of = {m: cid for cid, c in self.data['collections'].items() for m in c['members']}
        found, reals = {}, {}
        folders = [(f, os.path.normcase(os.path.abspath(f['path']))) for f in self.data['folders']]
        # what a Kids folder reaches inside another media folder (Kids\Cartoons -> Movies\Animated) is Kids too,
        # though the other folder's walk lists it; the Kids switches as they are now
        kids_into = {t for f, root in folders if f.get('kids') for t in self._into.get(root, ())}
        kids_pre = tuple(t.rstrip(os.sep) + os.sep for t in kids_into)          # one startswith per file
        # folders the last walk reached first: a live copy of a file beats a remembered one on a missing drive
        for live in (True, False):
            for f, root in folders:
                if (root in self._live_roots) != live:
                    continue
                for fp, path, ext, size, real in self._raw.get(root, []):
                    kids = f.get('kids') or (kids_into and (real in kids_into or real.startswith(kids_pre)))
                    if kids and self._tag_kids_once(fp):
                        self._save_after_scan = True
                        if fp in found:
                            found[fp]['kids'] = True
                    if fp in found:
                        if live and real not in reals[fp]:  # the same file twice (a real copy): list it once
                            reals[fp].add(real)
                            found[fp]['copies'] = len(reals[fp])
                        continue
                    found[fp] = self._item(fp, path, f['path'], ext, size)
                    reals[fp] = {real}
        items = sorted(found.values(), key=lambda i: (names.sort_key(i['title']), i.get('season') or 0,
                                                      i.get('episode') or 0))
        _mark_problems(items)                  # before publishing: readers on other threads see finished items
        self._by_id = {i['id']: i for i in items}
        self._items = items
        digest = self._digest()
        if digest != self._last_digest:
            self._last_digest = digest
            self.version += 1
        return items

    def _digest(self):
        keys = ('id', 'path', 'title', 'year', 'kind', 'show', 'season', 'episode', 'kids', 'hidden', 'collection',
                'problems', 'copies', 'size', 'format')
        rows = [[i.get(k) for k in keys] for i in self._items]
        return hashlib.sha1(json.dumps([rows, self.data['collections'], self.data['dismissed']],
                                       sort_keys=True, default=str).encode()).hexdigest()

    def _tag_kids_once(self, fp):
        """A file first seen in a Kids folder gets a stored Kids tag, so it stays Kids if moved. Untagging it in the
        app sticks: the 'seen' mark stops it being tagged again."""
        ov = self.data['items'].setdefault(fp, {})
        if ov.get('kids_folder_seen'):
            return False
        ov['kids_folder_seen'] = True
        ov['kids'] = True
        return True

    def _item(self, fp, path, root, ext, size):
        fmt = names.VIDEO_EXTS[ext]
        it = {'id': fp, 'path': path, 'file': os.path.basename(path), 'folder': root, 'format': fmt, 'size': size,
              'roku_ok': fmt in names.ROKU_OK, 'kids': False, 'hidden': False, 'episode_last': None,
              'near_miss': None, 'untitled': False}
        ep = names.episode_info(path, root)
        stem = os.path.splitext(os.path.basename(path))[0]
        if ep:
            it.update(kind='episode', show=ep['show'], season=ep['season'], episode=ep['episode'], year=None)
            if ep['last'] > ep['episode']:
                it['episode_last'] = ep['last']
            it['title'] = _ep_title(ep['show'], ep['season'], ep['episode'], it['episode_last'])
        else:
            t, y = names.movie(stem)
            if not t:                            # '1080p.mkv': nothing left to call it
                t, it['untitled'] = stem, True
            in_season = (names.season_of_dir(os.path.basename(os.path.dirname(path))) or 0) > 0
            it.update(kind='movie', title=t, year=y, show=None, season=None, episode=None,
                      near_miss=names.looks_like_episode(stem, in_season))
        it['auto'] = {k: it.get(k) for k in OVERRIDABLE}
        it['auto_near_miss'] = it['near_miss']
        ov = self.data['items'].get(fp, {})
        for k in OVERRIDABLE:
            if k in ov and valid(k, ov[k]):       # a bad hand-edited value is ignored, not fatal
                it[k] = ov[k]
        if 'episode' in ov:                       # renumbered: the file name's second episode no longer applies
            it['episode_last'] = None
        if it['kind'] == 'episode' and not (it.get('show') and isinstance(it.get('season'), int)
                                             and isinstance(it.get('episode'), int)):
            it['kind'] = 'movie'                 # an override that turned it into an episode without the details
        if it['kind'] == 'movie':
            it['show'] = it['season'] = it['episode'] = it['episode_last'] = None
            if 'title' in ov or 'kind' in ov:     # you named it or said "movie": no more episode hints
                it['near_miss'] = None
        else:
            it['near_miss'] = None
            if 'title' not in ov:                # the TV title follows an edited show / season / episode
                it['title'] = _ep_title(it['show'], it['season'], it['episode'], it['episode_last'])
        if 'title' in ov:
            it['untitled'] = False
        # only a movie is in a collection: one turned into an episode drops out of it (and can't be stuck there)
        it['collection'] = self._col_of.get(fp) if it['kind'] == 'movie' else None
        return it

    # ------------------------------------------------------------------ edits
    def set_item(self, fp, **fields):
        """Override what PLAY read from the name. A value equal to the automatic one removes the override."""
        self.set_items([fp], **fields)

    def set_items(self, fps, **fields):
        """The same edit on several titles: checked first, then saved and rebuilt once (no disk walk)."""
        for k, v in fields.items():
            if k not in OVERRIDABLE:
                raise KeyError(k)
            if not valid(k, v):
                raise ValueError(f'{k} cannot be {v!r}')
        self._ensure_scanned()
        with self.lock:
            for fp in fps:
                self._override(fp, fields)
            self._rebuild()
        self.save()

    def _override(self, fp, fields):
        cur = self._by_id.get(fp)
        ov = dict(self.data['items'].get(fp, {}))
        for k, v in fields.items():
            if isinstance(v, str):
                v = v.strip()
            # kind=movie is kept even when it is the automatic value, for a name flagged as "looks like an episode":
            # it is the answer to that hint
            keep = k == 'kind' and v == 'movie' and cur is not None and bool(cur.get('auto_near_miss'))
            if cur is not None and cur['auto'].get(k) == v and not keep:
                ov.pop(k, None)
            else:
                ov[k] = v
        if ov:                    # may hold only the kids_folder_seen mark: kept so Kids isn't re-added
            self.data['items'][fp] = ov
        else:
            self.data['items'].pop(fp, None)

    def make_collection(self, name, members, order='year'):
        self._ensure_scanned()
        with self.lock:
            if not members:
                return None
            cid = uuid.uuid4().hex[:8]
            self._take(members)
            self.data['collections'][cid] = {'name': name.strip() or 'Collection', 'order': order,
                                             'members': list(dict.fromkeys(members))}
            self._rebuild()
        self.save()
        return cid

    def update_collection(self, cid, name=None, order=None, members=None, shown=None):
        """members, when given, is the new order of the films the dialog showed (shown: the ids it listed when it
        opened). Films it didn't show (offline then) are kept at their places, unless they are gone for good: not
        found anywhere while every media folder could be read."""
        self._ensure_scanned()
        with self.lock:
            c = self.data['collections'][cid]
            if name is not None:
                c['name'] = name.strip() or c['name']
            if order in ('year', 'custom'):
                c['order'] = order
            if members is not None:
                # with no dialog behind the call (shown is None), members is the whole new list
                shown = set(shown) if shown is not None else None
                hidden = [] if shown is None else [
                    (i, m) for i, m in enumerate(c['members']) if m not in shown and m not in members
                    and (m in self._by_id or self._incomplete)]
                new = list(dict.fromkeys(members))
                for i, m in hidden:                  # back where it was
                    new.insert(min(i, len(new)), m)
                self._take(new, keep=cid)
                c['members'] = list(dict.fromkeys(new))
            if not c['members']:
                del self.data['collections'][cid]
            self._rebuild()
        self.save()

    def remove_from_collections(self, fps):
        """Take these titles out of whatever collection they are in (one save, one rebuild)."""
        self._ensure_scanned()
        with self.lock:
            self._take(set(fps))
            self._rebuild()
        self.save()

    def delete_collection(self, cid):
        self._ensure_scanned()
        with self.lock:
            self.data['collections'].pop(cid, None)
            self._rebuild()
        self.save()

    def _take(self, members, keep=None):
        """A movie is in one collection at a time: remove these members from any other collection."""
        members = set(members)
        for cid, c in list(self.data['collections'].items()):
            if cid == keep:
                continue
            c['members'] = [m for m in c.get('members', []) if m not in members]
            if not c['members']:
                del self.data['collections'][cid]

    def dismiss(self, key):
        with self.lock:
            if key in self.data['dismissed']:
                return
            self.data['dismissed'].append(key)
            self._rebuild()
        self.save()

    # ------------------------------------------------------------------ derived views
    def _members_locked(self, cid):
        c = self.data['collections'].get(cid)
        if not c:
            return []
        ms = [self._by_id[m] for m in c['members'] if m in self._by_id and self._by_id[m]['kind'] == 'movie']
        if c.get('order', 'year') == 'year':
            ms.sort(key=release_key)
        return ms

    def collection_members(self, cid):
        """The collection's movies in play order (release year, or your custom order)."""
        self._ensure_scanned()
        with self.lock:
            return self._members_locked(cid)

    def _dismissed(self, key, title):
        legacy = names.legacy_family_word(title)
        return key in self.data['dismissed'] or bool(legacy and legacy in self.data['dismissed'])

    def suggestions(self):
        """Groups of 2+ loose movies that look like one franchise, not yet dismissed: a subfolder named after them
        ('Alien Saga/'), or a shared first word ('Jungle ...')."""
        self._ensure_scanned()
        with self.lock:
            loose = [i for i in self._items if i['kind'] == 'movie' and not i['collection'] and not i['hidden']]
            out, covered = [], set()
            by_dir = {}
            for i in loose:
                d = os.path.dirname(i['path'])
                if os.path.normcase(d) != os.path.normcase(i['folder']):
                    by_dir.setdefault(d, []).append(i)
            for d, g in sorted(by_dir.items()):
                key = 'folder:' + os.path.normcase(d)
                if len(g) < 2:
                    continue
                # 'Alien Saga/' with Alien films, yes; a plain 'Movies/' or 'The Best/' folder of unrelated films, no
                words = {w for w in names.clean(os.path.basename(d)).lower().split()
                         if len(w) >= 3 and w not in STOP_WORDS}
                if sum(any(t.startswith(w) for w in words for t in i['title'].lower().split()) for i in g) < 2:
                    continue
                covered.update(i['id'] for i in g)     # dismissed or not: these films are this folder's question
                if key in self.data['dismissed']:
                    continue
                g.sort(key=release_key)
                out.append({'key': key, 'name': names.clean(os.path.basename(d)) or g[0]['title'],
                            'label': names.clean(os.path.basename(d)), 'members': [i['id'] for i in g]})
            groups = {}
            for i in loose:
                w = names.family_word(i['title'])
                if w and i['id'] not in covered:
                    groups.setdefault(w, []).append(i)
            for w, g in sorted(groups.items()):
                g.sort(key=release_key)
                if len(g) < 2 or self._dismissed(w, g[0]['title']):
                    continue
                out.append({'key': w, 'name': g[0]['title'], 'label': names.family_word(g[0]['title'], display=True),
                            'members': [i['id'] for i in g]})
            return out

    def tv_view(self, kids=False):
        """What one side of the TV app shows: movies (with collections as single entries) and shows. One consistent
        snapshot: items, collections and members are all read under the lock."""
        self._ensure_scanned()
        with self.lock:
            vis = [i for i in self._items if not i['hidden'] and (i['kids'] or not kids)]
            movies, shows, seen_cols = [], {}, set()
            for i in vis:
                if i['kind'] == 'episode':
                    s = shows.setdefault(names.show_key(i['show']), {'name': i['show'], 'seasons': {}})
                    s['seasons'].setdefault(i['season'], []).append(_tv(i))
                elif i['collection']:
                    cid = i['collection']
                    if cid in seen_cols:
                        continue
                    seen_cols.add(cid)
                    ms = [_tv(m) for m in self._members_locked(cid) if not m['hidden'] and (m['kids'] or not kids)]
                    if ms:
                        movies.append({'kind': 'collection', 'id': cid, 'title': self.data['collections'][cid]['name'],
                                       'items': ms})
                else:
                    movies.append(_tv(i))
            movies.sort(key=lambda m: names.sort_key(m['title']))
            show_list = []
            for s in sorted(shows.values(), key=lambda s: names.sort_key(s['name'])):
                seasons = [{'season': n, 'episodes': sorted(e, key=lambda x: x['episode'])}
                           for n, e in sorted(s['seasons'].items())]
                show_list.append({'name': s['name'], 'seasons': seasons})
            return {'movies': movies, 'shows': show_list}


def _finite(v):
    """v as a finite float, or None (a bool, a string, Infinity, or a number too big for a float)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        v = float(v)
    except (OverflowError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _under(key, roots):
    return any(key == r or key.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _is_junction(entry):
    try:
        return entry.is_junction()                       # Python 3.12+
    except AttributeError:
        return bool(getattr(entry.stat(follow_symlinks=False), 'st_reparse_tag', 0))


FILE_ATTRIBUTE_DIRECTORY = 0x10


def _dir_link(entry):
    """Is this a junction, mount point or directory symlink (followed or not)? A broken one counts too."""
    try:
        if not (entry.is_symlink() or _is_junction(entry)):
            return False
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_DIRECTORY)
    except (OSError, AttributeError):
        return False


def _walk(root, skip_roots, cache, pending, new_pending, first, real_roots=frozenset(), root_real=None,
          by_real=None, listings=None):
    """One media folder. Returns ([(fp, path, ext, size, real)], could the folder be listed, did the cache change,
    [normcased folders that couldn't be listed], [links whose target is offline], [real folders of other media
    folders that a link or subfolder here leads into]). by_real and listings (shared by the walks of one scan) let a
    folder or file reached through two media folders be listed and fingerprinted once. A file that can't be read
    counts as an error (its path is in the errors list), so its titles are kept.

    Every file is named and keyed by its path inside the media folder, through any link, so show and season names
    come from the media folder and a fingerprint follows the path it was taken at. Junctions, mount points and
    directory links are followed; where they really lead (realpath) only decides whether to go in: a target this
    walk has already been through, or another media folder (which is walked on its own), is skipped, so a link
    back up the tree can't loop and nothing is listed twice by one walk. 'real' is the file's real location, so a
    file reached by two paths isn't counted as two copies. A link whose target is offline keeps its titles.
    Folders that are their own media folder (skip_roots: normcased path -> real location or None) are left
    to their own walk, links among them too (never looked at here). A new file waits until it has
    looked the same for SETTLE seconds (still copying?), except at startup when it was last written over a minute
    ago; a known file that was only re-stamped (same size) is re-read at once; a changed size or time means a fresh
    fingerprint."""
    out, changed, errors, offline, into_roots = [], False, [], [], []
    by_real = {} if by_real is None else by_real
    listings = {} if listings is None else listings
    try:
        st = os.stat(root)
        if not statmod.S_ISDIR(st.st_mode):
            return [], False, False, [], [], []
        root_real = root_real or os.path.normcase(os.path.realpath(root))
    except OSError:
        return [], False, False, [], [], []
    other_roots = {r for r in real_roots if r != root_real}
    visited = {root_real}                         # real folders this walk has been through (links only add)
    now_mono, now = time.monotonic(), time.time()
    stack = [(root, root_real)]                   # (folder as named under the media folder, its real location)
    while stack:
        d, d_real = stack.pop()
        try:
            entries = listings.get(d_real)
            if entries is None:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name)
                if d_real != os.path.normcase(d):  # reached through a link: another walk may reach it too,
                    listings[d_real] = entries     # and reuses these entries (and the stats they keep)
        except OSError:
            if d == root:
                return [], False, False, [], [], []
            errors.append(os.path.normcase(d))
            continue
        subdirs = []
        for e in entries:
            path = os.path.join(d, e.name)         # named as under this media folder (a listing may be shared)
            if _dir_link(e):
                if e.name.startswith('.') or e.name.lower() == '$recycle.bin':
                    continue                       # '.snapshots', '.backup': not media
                own = skip_roots.get(os.path.normcase(path), False)
                if own is not False:               # a media folder of its own: its walk reads (or misses) it
                    if own:
                        into_roots.append(own)
                    continue
                try:
                    real = os.path.normcase(os.path.realpath(path))
                    os.stat(path)
                except OSError:                    # an unplugged target: keep its titles (they are keyed by path)
                    offline.append(path)
                    continue
                if _under(real, other_roots):      # another media folder's own: it walks it, Kids flows through
                    into_roots.append(real)
                    continue
                if _under(real, visited):
                    continue                       # this walk has been there already
                visited.add(real)
                subdirs.append((path, real))
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                errors.append(os.path.normcase(path))
                continue
            if is_dir:
                if e.name.startswith('.') or e.name.lower() in ('$recycle.bin', 'system volume information'):
                    continue
                key = os.path.normcase(path)
                sub_real = os.path.join(d_real, os.path.normcase(e.name))
                own = skip_roots.get(key, False)
                if own is not False:               # a media folder of its own inside this one
                    into_roots.append(own or sub_real)
                    continue
                if d_real != os.path.normcase(d) and (sub_real in visited or sub_real in other_roots):
                    if sub_real in other_roots:    # under a link: another media folder, which walks it
                        into_roots.append(sub_real)
                    continue                       # ... or a folder this walk covers already
                subdirs.append((path, sub_real))
                continue
            ext = os.path.splitext(e.name)[1].lower()
            if ext not in names.VIDEO_EXTS or e.name.startswith('._'):
                continue                           # '._Heat.mkv': a macOS metadata file, not a film
            try:
                fst = e.stat()
            except OSError:
                errors.append(os.path.normcase(path))
                continue
            key = os.path.normcase(path)
            real_file = os.path.join(d_real, os.path.normcase(e.name))
            c = cache.get(key)
            if c and c[0] == fst.st_size and c[1] == fst.st_mtime_ns:
                out.append((c[2], path, ext, fst.st_size, real_file))
                by_real.setdefault(real_file, (fst.st_size, fst.st_mtime_ns, c[2]))
                continue
            known = by_real.get(real_file)
            if known and known[:2] == (fst.st_size, fst.st_mtime_ns):    # the same file, read by another walk
                cache[key] = [fst.st_size, fst.st_mtime_ns, known[2]]
                changed = True
                out.append((known[2], path, ext, fst.st_size, real_file))
                continue
            seen = pending.get(key)
            same = seen is not None and seen[0] == fst.st_size and seen[1] == fst.st_mtime_ns
            first_seen = seen[2] if same else now_mono
            settled = (c is not None and c[0] == fst.st_size)                  # re-stamped, same bytes
            settled = settled or now_mono - first_seen >= SETTLE                # unchanged long enough
            settled = settled or (first and now - fst.st_mtime > STARTUP_AGE)  # old file at startup
            if not settled:
                new_pending[key] = [fst.st_size, fst.st_mtime_ns, first_seen]
                continue
            try:
                fp = fingerprint(path, fst.st_size)
            except OSError:                        # locked for a moment: kept from before, scan incomplete
                errors.append(key)
                continue
            cache[key] = [fst.st_size, fst.st_mtime_ns, fp]
            by_real[real_file] = (fst.st_size, fst.st_mtime_ns, fp)
            changed = True
            out.append((fp, path, ext, fst.st_size, real_file))
        stack.extend(reversed(subdirs))
    return out, True, changed, errors, offline, into_roots


def _tv(i):
    out = {'kind': i['kind'], 'id': i['id'], 'title': i['title'], 'year': i.get('year'), 'format': i['format'],
           'stream': names.STREAM_FORMAT.get(i['format'], i['format']), 'url': '/media/' + i['id']}
    if i['kind'] == 'episode':
        out.update(show=i['show'], season=i['season'], episode=i['episode'], episode_last=i.get('episode_last'))
    return out


def _season_name(n):
    return 'Specials' if n == 0 else f'Season {n}'


def _mark_problems(items):
    """Flags on each item: formats Roku won't open, copies, names PLAY couldn't read, names that look like an
    episode but aren't read as one, doubled or missing episodes."""
    eps = {}
    for i in items:
        i['problems'] = []
        if not i['roku_ok']:
            i['problems'].append(f"{i['format'].upper()} files usually don't play on Roku")
        if i.get('copies'):
            i['problems'].append(f"{i['copies']} copies of this file")
        if i.get('untitled'):
            i['problems'].append("PLAY couldn't read a title from this file name: set one")
        if i.get('near_miss'):
            i['problems'].append(i['near_miss'])
        if i['kind'] == 'episode' and not i['hidden']:
            eps.setdefault((names.show_key(i['show']), i['season']), []).append(i)
    for (_, season), group in eps.items():
        nums = {}
        for i in group:
            for n in range(i['episode'], max(i['episode'], i.get('episode_last') or 0) + 1):   # E01-E02: 1 and 2
                nums.setdefault(n, []).append(i)
        for n, dup in nums.items():
            if len(dup) > 1:
                for i in dup:
                    i['problems'].append(f'Episode {n} appears {len(dup)} times')
        have = sorted(nums)
        if not have:
            continue
        missing = [n for n in range(1, have[-1] + 1) if n not in nums]
        if missing:
            last = max(group, key=lambda i: i['episode'])
            last['problems'].append(f'{_season_name(season)} is missing {names.plural_word(len(missing), "episode")} '
                                    f'{", ".join(map(str, missing))}')
