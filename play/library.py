"""The PLAY library: what's in the media folders, and what you told PLAY about it.

Media files are only ever read. Everything you set in the app (Kids, title, movie/show, hidden, collections)
lives in library.json in the settings folder, keyed by a fingerprint of each file's contents, so it follows a
file that is moved or renamed.
"""
import hashlib
import json
import os
import threading
import time
import uuid

from . import names

SAMPLE = 64 * 1024
OVERRIDABLE = ('title', 'year', 'kind', 'show', 'season', 'episode', 'kids', 'hidden')


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


def _dict(v):
    return v if isinstance(v, dict) else {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8-sig') as f:     # -sig: a file saved with a BOM (Notepad, PowerShell)
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


class Library:
    def __init__(self, store_dir=None):
        self.dir = store_dir or default_store_dir()
        self.path = os.path.join(self.dir, 'library.json')
        self.cache_path = os.path.join(self.dir, 'fingerprints.json')
        self.lock = threading.RLock()
        d = _read_json(self.path, {})
        self.data = {
            'version': 1,
            'folders': [f for f in (d.get('folders') if isinstance(d.get('folders'), list) else [])
                        if isinstance(f, dict) and isinstance(f.get('path'), str) and f['path'].strip()],
            'port': int(d.get('port', 8600)) if str(d.get('port', '')).isdigit() else 8600,
            'skin': d.get('skin', 'Blue'),
            'items': {k: v for k, v in _dict(d.get('items')).items() if isinstance(v, dict)},
            'collections': {k: v for k, v in _dict(d.get('collections')).items()
                            if isinstance(v, dict) and isinstance(v.get('name'), str)
                            and isinstance(v.get('members'), list)
                            and all(isinstance(m, str) for m in v['members'])},
            'dismissed': [x for x in d.get('dismissed', []) if isinstance(x, str)]
                         if isinstance(d.get('dismissed'), list) else [],
        }
        for f in self.data['folders']:          # a hand-edited "kids": "false" must not tag the folder
            f['kids'] = f.get('kids') is True
        self.fpcache = {k: v for k, v in _read_json(self.cache_path, {}).items()
                        if isinstance(v, list) and len(v) == 3 and isinstance(v[2], str)}
        self._items = []
        self._scanned = 0
        self._last_digest = None
        self.version = 0          # bumps on every scan or change; the UI and the server compare it

    # ------------------------------------------------------------------ settings
    def save(self):
        with self.lock:
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
                    out.append({'path': p, 'kids': bool(f.get('kids'))})
            self.data['folders'] = out
            self.save()
            self._scanned = 0

    def set_value(self, key, value):
        with self.lock:
            self.data[key] = value
            self.save()

    # ------------------------------------------------------------------ scanning
    def scan(self):
        with self.lock:
            found, cache_dirty, live, tagged = {}, False, set(), False
            for folder in self.data['folders']:
                root = folder['path']
                for dirpath, dirs, files in os.walk(root):
                    dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() != '$recycle.bin')
                    for fn in sorted(files):
                        ext = os.path.splitext(fn)[1].lower()
                        if ext not in names.VIDEO_EXTS:
                            continue
                        p = os.path.join(dirpath, fn)
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        key = os.path.normcase(p)
                        live.add(key)
                        c = self.fpcache.get(key)
                        if c and c[0] == st.st_size and c[1] == st.st_mtime_ns:
                            fp = c[2]
                        else:
                            try:
                                fp = fingerprint(p, st.st_size)
                            except OSError:
                                continue
                            self.fpcache[key] = [st.st_size, st.st_mtime_ns, fp]
                            cache_dirty = True
                        if folder.get('kids') and self._tag_kids_once(fp):
                            tagged = True
                            if fp in found:
                                found[fp]['kids'] = True
                        if fp in found:            # the same file twice (a copy): list it once
                            found[fp]['copies'] = found[fp].get('copies', 1) + 1
                            continue
                        found[fp] = self._item(fp, p, root, ext, st.st_size)
            stale = [k for k in self.fpcache if k not in live]
            for k in stale:
                del self.fpcache[k]
            if cache_dirty or stale:
                _write_json(self.cache_path, self.fpcache)
            if tagged:
                self.save()
            items = sorted(found.values(), key=lambda i: (names.sort_key(i['title']), i.get('season') or 0,
                                                          i.get('episode') or 0))
            _mark_problems(items)              # before publishing: readers on other threads see finished items
            self._items = items
            self._scanned = time.monotonic()
            digest = self._digest()
            if digest != self._last_digest:    # only a real change bumps the version (the window rebuilds on it)
                self._last_digest = digest
                self.version += 1
            return self._items

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

    def items(self, max_age=5.0):
        """The current item list, rescanning when older than max_age seconds."""
        with self.lock:
            if not self._scanned or time.monotonic() - self._scanned > max_age:
                self.scan()
            return self._items

    def by_id(self, fp, max_age=5.0):
        with self.lock:
            for i in self.items(max_age):
                if i['id'] == fp:
                    return i
        return None

    def path_for(self, fp, rescan=True):
        """The file to stream for an id, from the last scan. An unknown id rescans at most every 10 seconds, so
        requests for made-up ids can't keep the disks busy."""
        with self.lock:
            it = self.by_id(fp, max_age=3600)
            if not it and rescan and self.rescan_throttled():
                it = self.by_id(fp, max_age=3600)
            return it['path'] if it else None

    def rescan_throttled(self, every=10.0):
        """Rescan unless the last scan is under `every` seconds old. True if it scanned."""
        with self.lock:
            if self._scanned and time.monotonic() - self._scanned < every:
                return False
            self.scan()
            return True

    def _item(self, fp, path, root, ext, size):
        fmt = names.VIDEO_EXTS[ext]
        it = {'id': fp, 'path': path, 'file': os.path.basename(path), 'folder': root, 'format': fmt, 'size': size,
              'roku_ok': fmt in names.ROKU_OK, 'kids': False, 'hidden': False}
        ep = names.episode(path, root)
        if ep:
            it.update(kind='episode', show=ep[0], season=ep[1], episode=ep[2], year=None,
                      title=f'{ep[0]} S{ep[1]:02d}E{ep[2]:02d}')
        else:
            t, y = names.movie(os.path.splitext(os.path.basename(path))[0])
            it.update(kind='movie', title=t, year=y, show=None, season=None, episode=None)
        it['auto'] = {k: it.get(k) for k in OVERRIDABLE}
        ov = self.data['items'].get(fp, {})
        for k in OVERRIDABLE:
            if k in ov and valid(k, ov[k]):       # a bad hand-edited value is ignored, not fatal
                it[k] = ov[k]
        if it['kind'] == 'episode' and not (it.get('show') and isinstance(it.get('season'), int)
                                             and isinstance(it.get('episode'), int)):
            it['kind'] = 'movie'                 # an override that turned it into an episode without the details
        if it['kind'] == 'movie':
            it['show'] = it['season'] = it['episode'] = None
        elif 'title' not in ov:                  # the TV title follows an edited show / season / episode
            it['title'] = f"{it['show']} S{it['season']:02d}E{it['episode']:02d}"
        it['kids'] = bool(it['kids'])
        it['hidden'] = bool(it['hidden'])
        it['collection'] = self._collection_of(fp)
        return it

    def _collection_of(self, fp):
        for cid, c in self.data['collections'].items():
            if fp in c.get('members', []):
                return cid
        return None

    # ------------------------------------------------------------------ edits
    def set_item(self, fp, **fields):
        """Override what PLAY read from the name. A value equal to the automatic one removes the override."""
        self.set_items([fp], **fields)

    def set_items(self, fps, **fields):
        """The same edit on several titles: checked first, then saved and rescanned once."""
        for k, v in fields.items():
            if k not in OVERRIDABLE:
                raise KeyError(k)
            if not valid(k, v):
                raise ValueError(f'{k} cannot be {v!r}')
        with self.lock:
            for fp in fps:
                self._override(fp, fields)
            self.save()
            self.scan()

    def _override(self, fp, fields):
        cur = self.by_id(fp, max_age=3600)
        ov = dict(self.data['items'].get(fp, {}))
        for k, v in fields.items():
            if k not in OVERRIDABLE:
                raise KeyError(k)
            if not valid(k, v):
                raise ValueError(f'{k} cannot be {v!r}')
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

    def make_collection(self, name, members, order='year'):
        with self.lock:
            if not members:
                return None
            cid = uuid.uuid4().hex[:8]
            self._take(members)
            self.data['collections'][cid] = {'name': name.strip() or 'Collection', 'order': order,
                                             'members': list(dict.fromkeys(members))}
            self.save()
            self.scan()
            return cid

    def update_collection(self, cid, name=None, order=None, members=None):
        with self.lock:
            c = self.data['collections'][cid]
            if name is not None:
                c['name'] = name.strip() or c['name']
            if order in ('year', 'custom'):
                c['order'] = order
            if members is not None:
                self._take(members, keep=cid)
                c['members'] = list(dict.fromkeys(members))
            if not c['members']:
                del self.data['collections'][cid]
            self.save()
            self.scan()

    def delete_collection(self, cid):
        with self.lock:
            self.data['collections'].pop(cid, None)
            self.save()
            self.scan()

    def _take(self, members, keep=None):
        """A movie is in one collection at a time: remove these members from any other collection."""
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

    # ------------------------------------------------------------------ derived views
    def collection_members(self, cid):
        """The collection's movies in play order (release year, or your custom order)."""
        with self.lock:
            c = self.data['collections'].get(cid)
            if not c:
                return []
            byid = {i['id']: i for i in self.items()}
            ms = [byid[m] for m in c['members'] if m in byid]
            if c.get('order', 'year') == 'year':
                ms.sort(key=lambda i: (i.get('year') or 9999, names.sort_key(i['title'])))
            return ms

    def suggestions(self):
        """Groups of 2+ loose movies sharing a first word ('Jungle ...'), not yet dismissed."""
        with self.lock:
            loose = [i for i in self.items() if i['kind'] == 'movie' and not i['collection'] and not i['hidden']]
            out, covered = [], set()
            # a subfolder holding 2+ movies ('Jungle Park/...') is offered as a collection named after the folder
            by_dir = {}
            for i in loose:
                d = os.path.dirname(i['path'])
                if os.path.normcase(d) != os.path.normcase(i['folder']):
                    by_dir.setdefault(d, []).append(i)
            for d, g in sorted(by_dir.items()):
                key = 'folder:' + os.path.normcase(d)
                if len(g) < 2 or key in self.data['dismissed']:
                    continue
                # 'Alien Saga/' with Alien films, yes; a plain 'Movies/' folder of unrelated films, no
                words = {w for w in names.clean(os.path.basename(d)).lower().split() if len(w) >= 3}
                if sum(any(t.startswith(w) for w in words for t in i['title'].lower().split()) for i in g) < 2:
                    continue
                g.sort(key=lambda i: (i.get('year') or 9999, names.sort_key(i['title'])))
                out.append({'key': key, 'name': names.clean(os.path.basename(d)) or g[0]['title'],
                            'members': [i['id'] for i in g]})
                covered.update(i['id'] for i in g)
            groups = {}
            for i in loose:
                w = names.family_word(i['title'])
                if w and i['id'] not in covered:
                    groups.setdefault(w, []).append(i)
            for w, g in sorted(groups.items()):
                if len(g) < 2 or w in self.data['dismissed']:
                    continue
                g.sort(key=lambda i: (i.get('year') or 9999, len(i['title'])))
                out.append({'key': w, 'name': g[0]['title'], 'members': [i['id'] for i in g]})
            return out

    def tv_view(self, kids=False):
        """What one side of the TV app shows: movies (with collections as single entries) and shows."""
        with self.lock:
            vis = [i for i in self.items() if not i['hidden'] and (i['kids'] or not kids)]
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


def _tv(i):
    out = {'kind': i['kind'], 'id': i['id'], 'title': i['title'], 'year': i.get('year'), 'format': i['format'],
           'url': '/media/' + i['id']}
    if i['kind'] == 'episode':
        out.update(show=i['show'], season=i['season'], episode=i['episode'])
    return out


def import_old_config(path):
    """The pre-1.0 config.json ({"libraries": {"HOME": [...], "KIDS": [...]}}) as folders, KIDS ones tagged Kids."""
    cfg = _read_json(path, {})
    libs = cfg.get('libraries') or {'HOME': cfg.get('folders', [])}
    out = []
    for name, fs in libs.items():
        for f in fs:
            out.append({'path': f, 'kids': name.upper() == 'KIDS'})
    return out


def _mark_problems(items):
    """Flags on each item: formats Roku won't open, copies, doubled or missing episodes."""
    eps = {}
    for i in items:
        i['problems'] = []
        if not i['roku_ok']:
            i['problems'].append(f"{i['format'].upper()} files usually don't play on Roku")
        if i.get('copies'):
            i['problems'].append(f"{i['copies']} copies of this file")
        if i['kind'] == 'episode' and not i['hidden']:
            eps.setdefault((i['show'].lower(), i['season']), []).append(i)
    for (_, season), group in eps.items():
        nums = {}
        for i in group:
            nums.setdefault(i['episode'], []).append(i)
        for n, dup in nums.items():
            if len(dup) > 1:
                for i in dup:
                    i['problems'].append(f'Episode {n} appears {len(dup)} times')
        have = sorted(nums)
        missing = [n for n in range(1, have[-1] + 1) if n not in nums]
        if missing:
            last = max(group, key=lambda i: i['episode'])
            last['problems'].append('Season %d is missing episode %s' % (season, ', '.join(map(str, missing))))
