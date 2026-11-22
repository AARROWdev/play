"""The PLAY library: what's in the media folders, and what you told PLAY about it.

Media files are only ever read. Everything you set in the app (Kids, title, movie/show, hidden, collections)
lives in library.json in the settings folder, keyed by a fingerprint of each file's contents, so it follows a
file that is moved or renamed.

Two kinds of work, kept apart so streaming never waits on the disks:
- scan() walks the folders (slow: every file is stat'ed, new ones fingerprinted). It runs without holding the
  library lock, then swaps its result in.
- _rebuild() turns the last walk plus your settings into items (fast, in memory). Every edit uses only this.
"""
import datetime
import hashlib
import json
import os
import shutil
import stat as statmod
import threading
import time
import uuid

from . import names, skins

SAMPLE = 64 * 1024
OVERRIDABLE = ('title', 'year', 'kind', 'show', 'season', 'episode', 'kids', 'hidden')
SETTLE = 10.0       # a file created or changed in the last 10 s (still copying?) waits for the next scan
STOP_WORDS = {'the', 'and', 'of', 'a', 'an', 'movies', 'movie', 'films', 'film', 'collection', 'series', 'set',
              'box', 'complete', 'best', 'all'}


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


def valid_port(v):
    return isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65535


def release_key(item):
    """Release order: by year (no year last), then title. The one rule for collections everywhere."""
    return item.get('year') or 9999, names.sort_key(item['title'])


def _dict(v):
    return v if isinstance(v, dict) else {}


def _write_json(path, data):
    """Atomic write. ensure_ascii: a file name with a lone surrogate (legal on Windows) can't break the save."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=True)
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


def load_store(path):
    """(settings dict, problem message or None). A missing file is a fresh start. A file that can't be read is
    retried (a virus scanner or backup may hold it for a moment); one that isn't valid JSON is set aside as
    library.json.unreadable-<time> and the last good copy (library.json.bak) is used, so a bad hand edit never
    turns into an empty library that the next save would make permanent."""
    if not os.path.exists(path):
        return {}, None
    err = None
    for _ in range(10):
        try:
            d = _parse_file(path)
            if isinstance(d, dict):
                return d, None
            err = 'is not a settings object'
            break
        except OSError as e:
            err = str(e)
            time.sleep(0.2)
        except ValueError:
            err = 'is not valid JSON'
            break
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    kept = f'{path}.unreadable-{stamp}'
    try:
        shutil.copy2(path, kept)
    except OSError:
        kept = None
    bak = _read_json(path + '.bak', {}) if os.path.exists(path + '.bak') else None
    msg = f'PLAY could not read its settings file ({err}).'
    if kept:
        msg += f' It was kept as {os.path.basename(kept)}.'
    if isinstance(bak, dict):
        return bak, msg + ' The last good copy was loaded instead.'
    return {}, msg + ' PLAY started with empty settings.'


class Library:
    def __init__(self, store_dir=None):
        self.dir = store_dir or default_store_dir()
        self.path = os.path.join(self.dir, 'library.json')
        self.cache_path = os.path.join(self.dir, 'fingerprints.json')
        self.lock = threading.RLock()          # guards data and items; held only for in-memory work
        self._scan_lock = threading.Lock()     # one disk walk at a time
        d, self.load_problem = load_store(self.path)
        port = d.get('port', 8600)
        skin = d.get('skin', skins.DEFAULT)
        self.data = {
            'version': 1,
            'folders': [{'path': f['path'], 'kids': f.get('kids') is True}
                        for f in (d.get('folders') if isinstance(d.get('folders'), list) else [])
                        if isinstance(f, dict) and isinstance(f.get('path'), str) and f['path'].strip()],
            'port': port if valid_port(port) else 8600,
            'skin': skin if isinstance(skin, str) and skin in skins.SKINS else skins.DEFAULT,
            'items': {k: v for k, v in _dict(d.get('items')).items() if isinstance(v, dict)},
            'collections': {k: v for k, v in _dict(d.get('collections')).items()
                            if isinstance(v, dict) and isinstance(v.get('name'), str)
                            and isinstance(v.get('members'), list)
                            and all(isinstance(m, str) for m in v['members'])},
            'dismissed': [x for x in d.get('dismissed', []) if isinstance(x, str)]
                         if isinstance(d.get('dismissed'), list) else [],
        }
        self.fpcache = {k: v for k, v in _read_json(self.cache_path, {}).items()
                        if isinstance(v, list) and len(v) == 3 and isinstance(v[2], str)}
        self._raw = {}             # normcased root -> [(fp, path, ext, size)] from the last walk that reached it
        self._pending = {}         # normcased path -> (size, mtime) of a file not settled yet
        self._items = []
        self._by_id = {}
        self._col_of = {}
        self._scanned = 0
        self._last_digest = None
        self.version = 0           # bumps when the items really change; the window rebuilds on it
        self.unreachable = []      # folders the last scan couldn't open (their files are kept from before)
        self._index_collections()

    # ------------------------------------------------------------------ settings
    def save(self):
        with self.lock:
            if os.path.exists(self.path):
                try:
                    shutil.copy2(self.path, self.path + '.bak')
                except OSError:
                    pass
            _write_json(self.path, self.data)

    @property
    def folders(self):
        return list(self.data['folders'])

    def set_folders(self, folders):
        """folders: [{'path': str, 'kids': bool}]. Files in a Kids folder start out tagged Kids."""
        with self.lock:
            seen, out = set(), []
            for f in folders:
                if not isinstance(f.get('path'), str) or not f['path'].strip():
                    raise ValueError(f'folder path cannot be {f.get("path")!r}')
                p = os.path.abspath(f['path'])
                if os.path.normcase(p) not in seen:
                    seen.add(os.path.normcase(p))
                    out.append({'path': p, 'kids': f.get('kids') is True})
            self.data['folders'] = out
            self.save()
            self._scanned = 0

    def set_value(self, key, value):
        if key == 'port' and not valid_port(value):
            raise ValueError(f'port cannot be {value!r}')
        if key == 'skin' and value not in skins.SKINS:
            raise ValueError(f'skin cannot be {value!r}')
        with self.lock:
            self.data[key] = value
            self.save()

    # ------------------------------------------------------------------ scanning (disk)
    def scan(self):
        """Walk the media folders, then rebuild the items. The walk holds no library lock, so the server and the
        window keep working while it runs. Returns the items."""
        with self._scan_lock:
            with self.lock:
                folders = [dict(f) for f in self.data['folders']]
                cache = dict(self.fpcache)
                pending = dict(self._pending)
                first = not self._raw
            roots = [os.path.normcase(os.path.abspath(f['path'])) for f in folders]
            raw, reached, new_pending, dirty = {}, set(), {}, False
            for f, root in zip(folders, roots):
                others = [r for r in roots if r != root and r.startswith(root.rstrip(os.sep) + os.sep)]
                files, ok, changed = _walk(f['path'], others, cache, pending, new_pending, first)
                dirty = dirty or changed
                if ok:
                    raw[root] = files
                    reached.add(root)
            with self.lock:
                unreachable = []
                for f, root in zip(folders, roots):
                    if root not in reached:        # NAS asleep, drive not mounted yet: keep what we had
                        raw[root] = self._raw.get(root, [])
                        unreachable.append(f['path'])
                live = {os.path.normcase(p) for files in raw.values() for _, p, _, _ in files}
                stale = [k for k in cache if k not in live and _under(k, reached)]
                for k in stale:
                    del cache[k]
                self.fpcache = cache
                self._pending = new_pending
                self._raw = raw
                self.unreachable = unreachable
                if dirty or stale:
                    try:
                        _write_json(self.cache_path, cache)
                    except OSError:
                        pass
                self._scanned = time.monotonic()
                return self._rebuild()

    def items(self, max_age=5.0):
        """The current item list, rescanning when older than max_age seconds (or never scanned)."""
        if not self._scanned or time.monotonic() - self._scanned > max_age:
            self.scan()
        return self._items

    def current(self):
        """The items as they are now, without touching the disks (scans once if there has never been a scan)."""
        return self.items(max_age=float('inf'))

    def by_id(self, fp, max_age=float('inf')):
        if not self._scanned or time.monotonic() - self._scanned > max_age:
            self.scan()
        return self._by_id.get(fp)

    def path_for(self, fp, rescan=True):
        """The file to stream for an id, from the last scan. An unknown id rescans at most every 10 seconds, so
        requests for made-up ids can't keep the disks busy."""
        it = self.by_id(fp)
        if not it and rescan and self.rescan_throttled():
            it = self.by_id(fp)
        return it['path'] if it else None

    def rescan_throttled(self, every=10.0):
        """Rescan unless the last scan is under `every` seconds old. True if it scanned."""
        if self._scanned and time.monotonic() - self._scanned < every:
            return False
        self.scan()
        return True

    # ------------------------------------------------------------------ items (memory)
    def _rebuild(self):
        """Items from the last walk plus the settings. In memory only; call with self.lock held."""
        self._index_collections()
        found, tagged = {}, False
        for f in self.data['folders']:
            root = os.path.normcase(os.path.abspath(f['path']))
            for fp, path, ext, size in self._raw.get(root, []):
                if f.get('kids') and self._tag_kids_once(fp):
                    tagged = True
                    if fp in found:
                        found[fp]['kids'] = True
                if fp in found:                # the same file twice (a copy): list it once
                    found[fp]['copies'] = found[fp].get('copies', 1) + 1
                    continue
                found[fp] = self._item(fp, path, f['path'], ext, size)
        if tagged:
            try:
                self.save()
            except OSError:
                pass
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

    def _refresh(self):
        """After an edit: rebuild from the last walk (or walk once, if there hasn't been one)."""
        if not self._scanned:
            return self.scan()
        with self.lock:
            return self._rebuild()

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
              'roku_ok': fmt in names.ROKU_OK, 'kids': False, 'hidden': False, 'episode_last': None}
        ep = names.episode_info(path, root)
        stem = os.path.splitext(os.path.basename(path))[0]
        if ep:
            it.update(kind='episode', show=ep['show'], season=ep['season'], episode=ep['episode'], year=None,
                      title=_ep_title(ep['show'], ep['season'], ep['episode']))
            if ep['last'] > ep['episode']:
                it['episode_last'] = ep['last']
        else:
            t, y = names.movie(stem)
            it.update(kind='movie', title=t, year=y, show=None, season=None, episode=None)
            it['near_miss'] = names.looks_like_episode(stem)
        it['auto'] = {k: it.get(k) for k in OVERRIDABLE}
        ov = self.data['items'].get(fp, {})
        for k in OVERRIDABLE:
            if k in ov and valid(k, ov[k]):       # a bad hand-edited value is ignored, not fatal
                it[k] = ov[k]
        if it['kind'] == 'episode' and not (it.get('show') and isinstance(it.get('season'), int)
                                             and isinstance(it.get('episode'), int)):
            it['kind'] = 'movie'                 # an override that turned it into an episode without the details
        if it['kind'] == 'movie':
            it['show'] = it['season'] = it['episode'] = it['episode_last'] = None
        else:
            it['near_miss'] = None
            if 'title' not in ov:                # the TV title follows an edited show / season / episode
                it['title'] = _ep_title(it['show'], it['season'], it['episode'])
        # only a movie is in a collection: one turned into an episode drops out of it (and can't be stuck there)
        it['collection'] = self._col_of.get(fp) if it['kind'] == 'movie' else None
        return it

    def _index_collections(self):
        self._col_of = {m: cid for cid, c in self.data['collections'].items() for m in c['members']}

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
        self.current()
        with self.lock:
            for fp in fps:
                self._override(fp, fields)
            self.save()
            self._rebuild()

    def _override(self, fp, fields):
        cur = self._by_id.get(fp)
        ov = dict(self.data['items'].get(fp, {}))
        for k, v in fields.items():
            if isinstance(v, str):
                v = v.strip()
            if cur is not None and cur['auto'].get(k) == v:
                ov.pop(k, None)
            else:
                ov[k] = v
        if ov:                    # may hold only the kids_folder_seen mark: kept so Kids isn't re-added
            self.data['items'][fp] = ov
        else:
            self.data['items'].pop(fp, None)

    def clear_overrides(self, fps, keys):
        """Go back to what the file name says for these fields (e.g. the title after an episode edit)."""
        self.current()
        with self.lock:
            for fp in fps:
                ov = self.data['items'].get(fp)
                if ov:
                    for k in keys:
                        ov.pop(k, None)
                    if not ov:
                        self.data['items'].pop(fp, None)
            self.save()
            self._rebuild()

    def make_collection(self, name, members, order='year'):
        self.current()
        with self.lock:
            if not members:
                return None
            cid = uuid.uuid4().hex[:8]
            self._take(members)
            self.data['collections'][cid] = {'name': name.strip() or 'Collection', 'order': order,
                                             'members': list(dict.fromkeys(members))}
            self._index_collections()
            self.save()
            self._rebuild()
            return cid

    def update_collection(self, cid, name=None, order=None, members=None):
        """members, when given, is the new order of the members you can see. Members that are offline right now
        (a sleeping NAS, an unplugged drive) are kept, after them, so editing a collection never drops a film."""
        self.current()
        with self.lock:
            c = self.data['collections'][cid]
            if name is not None:
                c['name'] = name.strip() or c['name']
            if order in ('year', 'custom'):
                c['order'] = order
            if members is not None:
                offline = [m for m in c['members'] if m not in self._by_id and m not in members]
                new = list(dict.fromkeys(list(members) + offline))
                self._take(new, keep=cid)
                c['members'] = new
            if not c['members']:
                del self.data['collections'][cid]
            self._index_collections()
            self.save()
            self._rebuild()

    def remove_from_collections(self, fps):
        """Take these titles out of whatever collection they are in (one save, one rebuild)."""
        self.current()
        with self.lock:
            self._take(set(fps))
            self._index_collections()
            self.save()
            self._rebuild()

    def delete_collection(self, cid):
        self.current()
        with self.lock:
            self.data['collections'].pop(cid, None)
            self._index_collections()
            self.save()
            self._rebuild()

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
            if key not in self.data['dismissed']:
                self.data['dismissed'].append(key)
                self.save()
                self._rebuild()

    # ------------------------------------------------------------------ derived views
    def collection_members(self, cid):
        """The collection's movies in play order (release year, or your custom order)."""
        items = self.current()
        with self.lock:
            c = self.data['collections'].get(cid)
            if not c:
                return []
            byid = self._by_id if self._items is items else {i['id']: i for i in items}
            ms = [byid[m] for m in c['members'] if m in byid and byid[m]['kind'] == 'movie']
            if c.get('order', 'year') == 'year':
                ms.sort(key=release_key)
            return ms

    def suggestions(self):
        """Groups of 2+ loose movies that look like one franchise, not yet dismissed: a subfolder named after them
        ('Alien Saga/'), or a shared first word ('Jungle ...')."""
        items = self.current()
        with self.lock:
            loose = [i for i in items if i['kind'] == 'movie' and not i['collection'] and not i['hidden']]
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
                if len(g) < 2 or w in self.data['dismissed']:
                    continue
                g.sort(key=lambda i: (i.get('year') or 9999, len(i['title'])))
                out.append({'key': w, 'name': g[0]['title'], 'label': names.family_word(g[0]['title'], display=True),
                            'members': [i['id'] for i in g]})
            return out

    def tv_view(self, kids=False):
        """What one side of the TV app shows: movies (with collections as single entries) and shows."""
        items = self.current()
        with self.lock:
            vis = [i for i in items if not i['hidden'] and (i['kids'] or not kids)]
            movies, shows, seen_cols = [], {}, set()
            for i in vis:
                if i['kind'] == 'episode':
                    s = shows.setdefault(i['show'].lower(), {'name': i['show'], 'seasons': {}})
                    s['seasons'].setdefault(i['season'], []).append(_tv(i))
                elif i['collection']:
                    cid = i['collection']
                    if cid in seen_cols:
                        continue
                    seen_cols.add(cid)
                    ms = [_tv(m) for m in self.collection_members(cid)
                          if not m['hidden'] and (m['kids'] or not kids)]
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


def _ep_title(show, season, episode):
    return f'{show} S{season:02d}E{episode:02d}'


def _under(key, roots):
    return any(key == r or key.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _walk(root, skip_roots, cache, pending, new_pending, first):
    """One media folder: [(fp, path, ext, size)], whether the folder could be opened, whether the cache changed.
    Folders that are their own media folder (skip_roots) are left to their own walk, junctions and symlinks are
    not followed (no loops), and a file still being written waits until it has settled."""
    out, changed, seen_dirs = [], False, set()
    try:
        st = os.stat(root)
        if not statmod.S_ISDIR(st.st_mode):
            return [], False, False
    except OSError:
        return [], False, False
    now = time.time()
    for dirpath, dirs, files in os.walk(root, onerror=lambda e: None):
        try:
            ds = os.stat(dirpath)
            ident = (ds.st_dev, ds.st_ino)
            if ident in seen_dirs and ds.st_ino:
                dirs[:] = []
                continue
            seen_dirs.add(ident)
        except OSError:
            pass
        keep = []
        for d in sorted(dirs):
            full = os.path.join(dirpath, d)
            if d.startswith('.') or d.lower() == '$recycle.bin' or os.path.normcase(full) in skip_roots:
                continue
            if os.path.islink(full) or getattr(os.path, 'isjunction', lambda p: False)(full):
                continue
            keep.append(d)
        dirs[:] = keep
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in names.VIDEO_EXTS:
                continue
            p = os.path.join(dirpath, fn)
            try:
                fst = os.stat(p)
            except OSError:
                continue
            key = os.path.normcase(p)
            c = cache.get(key)
            if c and c[0] == fst.st_size and c[1] == fst.st_mtime_ns:
                out.append((c[2], p, ext, fst.st_size))
                continue
            born = getattr(fst, 'st_birthtime', fst.st_ctime)
            fresh = now - max(fst.st_mtime, born) < SETTLE
            seen = pending.get(key)
            if fresh and seen != [fst.st_size, fst.st_mtime_ns]:
                new_pending[key] = [fst.st_size, fst.st_mtime_ns]     # look again next scan
                continue
            try:
                fp = fingerprint(p, fst.st_size)
            except OSError:
                continue
            cache[key] = [fst.st_size, fst.st_mtime_ns, fp]
            changed = True
            out.append((fp, p, ext, fst.st_size))
    return out, True, changed


def _tv(i):
    out = {'kind': i['kind'], 'id': i['id'], 'title': i['title'], 'year': i.get('year'), 'format': i['format'],
           'url': '/media/' + i['id']}
    if i['kind'] == 'episode':
        out.update(show=i['show'], season=i['season'], episode=i['episode'])
    return out


def _mark_problems(items):
    """Flags on each item: formats Roku won't open, copies, names that look like an episode but aren't read as one,
    doubled or missing episodes."""
    eps = {}
    for i in items:
        i['problems'] = []
        if not i['roku_ok']:
            i['problems'].append(f"{i['format'].upper()} files usually don't play on Roku")
        if i.get('copies'):
            i['problems'].append(f"{i['copies']} copies of this file")
        if i.get('near_miss'):
            i['problems'].append(i['near_miss'])
        if i['kind'] == 'episode' and not i['hidden']:
            eps.setdefault((i['show'].lower(), i['season']), []).append(i)
    for (_, season), group in eps.items():
        nums = {}
        for i in group:
            for n in range(i['episode'], (i.get('episode_last') or i['episode']) + 1):   # S01E01-E02 covers 1 and 2
                nums.setdefault(n, []).append(i)
        for n, dup in nums.items():
            if len(dup) > 1:
                for i in dup:
                    i['problems'].append(f'Episode {n} appears {len(dup)} times')
        have = sorted(nums)
        missing = [n for n in range(1, have[-1] + 1) if n not in nums]
        if missing:
            last = max(group, key=lambda i: i['episode'])
            last['problems'].append('Season %d is missing episode %s' % (season, ', '.join(map(str, missing))))
