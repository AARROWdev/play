"""PLAY for Windows: the library window, settings and tray icon. The movie server runs inside this program."""
import html
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QItemSelectionModel, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QSystemTrayIcon, QTreeView, QVBoxLayout, QWidget)

import suite_style
from . import library, names, server, skins

APP = 'PLAY'
INSTANCE_KEY = 'PLAY-library-window'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
ID = Qt.UserRole + 1          # item id (file fingerprint) or collection id
KIND = Qt.UserRole + 2        # 'item' | 'collection' | 'show' | 'season'

FILTERS = [('all', 'Everything'), ('movies', 'Movies'), ('shows', 'Shows'), ('collections', 'Collections'),
           ('kids', 'Kids'), ('hidden', 'Hidden'), ('attention', 'Needs a look')]


def play_icon(accent='#2f9bff', size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#13161c'))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    tri = QPainterPath()
    tri.moveTo(size * 0.36, size * 0.26)
    tri.lineTo(size * 0.76, size * 0.5)
    tri.lineTo(size * 0.36, size * 0.74)
    tri.closeSubpath()
    p.setBrush(QColor(accent))
    p.drawPath(tri)
    p.end()
    return QIcon(pm)


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit in ('GB', 'TB') else f'{n:.0f} {unit}'
        n /= 1024


# ------------------------------------------------------------------ start with Windows
def launch_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --tray'
    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'play_main.py')
    return f'"{pyw}" "{main}" --tray'


def get_autostart():
    """True only if the Run entry starts this copy of PLAY (not an old or moved install)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v = winreg.QueryValueEx(k, APP)[0]
    except OSError:
        return False
    return isinstance(v, str) and os.path.normcase(v.strip()) == os.path.normcase(launch_command())


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ------------------------------------------------------------------ background scanning
class Scanner(QObject):
    """Scans the folders off the GUI thread (network drives can be slow) and says when the list changed."""
    changed = Signal()

    def __init__(self, lib):
        super().__init__()
        self.lib = lib
        self.busy = False
        self.seen = lib.version          # the window draws what the library has now; only later changes count

    def kick(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.lib.scan()
        finally:
            self.busy = False
        if self.lib.version != self.seen:
            self.seen = self.lib.version
            self.changed.emit()


# ------------------------------------------------------------------ dialogs
class SettingsDialog(QDialog):
    def __init__(self, parent, lib, first_run=False):
        super().__init__(parent)
        self.setWindowTitle('PLAY settings')
        self.setMinimumWidth(620)
        self.lib = lib
        v = QVBoxLayout(self)
        if first_run:
            hi = QLabel('<b>Welcome to PLAY.</b> Add the folder (or folders) where your movies and shows are. '
                        'PLAY only reads them: files are never moved, renamed or changed.')
            hi.setWordWrap(True)
            v.addWidget(hi)
        note = QLabel('Media folders. Tick Kids for a folder whose files should start out tagged Kids. The tag is '
                      'given once, when PLAY first sees a file there; unticking the folder later leaves existing '
                      'tags alone (change them in the library).')
        note.setWordWrap(True)
        v.addWidget(note)
        self.folders = QListWidget()
        self.folders.setSelectionMode(QAbstractItemView.SingleSelection)
        for f in lib.folders:
            self._add_row(f['path'], f.get('kids'))
        v.addWidget(self.folders)
        row = QHBoxLayout()
        add, rem = QPushButton('Add folder…'), QPushButton('Remove')
        add.setObjectName('chip')
        rem.setObjectName('chip')
        add.clicked.connect(self._add)
        rem.clicked.connect(lambda: self.folders.takeItem(self.folders.currentRow()))
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        v.addLayout(row)
        grid = QHBoxLayout()
        grid.addWidget(QLabel('Port'))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(lib.data['port'])
        grid.addWidget(self.port)
        grid.addSpacing(24)
        grid.addWidget(QLabel('Skin'))
        self.skin = QComboBox()
        self.skin.addItems(list(skins.SKINS))
        self.skin.setCurrentText(skins.get(lib.data['skin'])['name'])
        grid.addWidget(self.skin)
        grid.addSpacing(24)
        self.auto = QCheckBox('Start PLAY with Windows')
        self.auto.setChecked(get_autostart())
        grid.addWidget(self.auto)
        grid.addStretch()
        v.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName('primary')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_row(self, path, kids=False):
        it = QListWidgetItem(path)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if kids else Qt.Unchecked)
        it.setToolTip('Checked = Kids folder')
        self.folders.addItem(it)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, 'Add a media folder')
        if d:
            self._add_row(os.path.normpath(d))

    def values(self):
        fs = [{'path': self.folders.item(i).text(), 'kids': self.folders.item(i).checkState() == Qt.Checked}
              for i in range(self.folders.count())]
        return fs, self.port.value(), self.skin.currentText(), self.auto.isChecked()


class CollectionDialog(QDialog):
    def __init__(self, parent, lib, cid):
        super().__init__(parent)
        self.lib, self.cid = lib, cid
        c = lib.data['collections'][cid]
        self.setWindowTitle('Collection')
        self.setMinimumSize(480, 460)
        v = QVBoxLayout(self)
        self.name = QLineEdit(c['name'])
        v.addWidget(QLabel('Name on the TV'))
        v.addWidget(self.name)
        v.addWidget(QLabel('Order'))
        seg = QHBoxLayout()
        self.by_year, self.custom = QPushButton('Release year'), QPushButton('Custom (drag)')
        self.order = QButtonGroup(self)
        for b in (self.by_year, self.custom):
            b.setCheckable(True)
            b.setObjectName('chip')
            self.order.addButton(b)
            seg.addWidget(b)
        seg.addStretch()
        (self.custom if c.get('order') == 'custom' else self.by_year).setChecked(True)
        v.addLayout(seg)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        for m in lib.collection_members(cid):
            it = QListWidgetItem(f"{m['title']}   {m.get('year') or ''}".rstrip())
            it.setData(ID, m['id'])
            self.list.addItem(it)
        v.addWidget(self.list)
        self.order.buttonToggled.connect(self._order_changed)
        self._order_changed()
        row = QHBoxLayout()
        rem, dele = QPushButton('Remove from collection'), QPushButton('Delete collection')
        rem.setObjectName('chip')
        dele.setObjectName('chip')
        rem.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        dele.clicked.connect(self._delete)
        row.addWidget(rem)
        row.addWidget(dele)
        row.addStretch()
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName('primary')
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _order_changed(self, *_):
        custom = self.custom.isChecked()
        self.list.setDragEnabled(custom)
        if not custom:   # show the year order right away
            ids = [m['id'] for m in sorted(self._members(), key=lambda m: (m.get('year') or 9999, names.sort_key(m['title'])))]
            self._reorder(ids)

    def _members(self):
        out = []
        for i in range(self.list.count()):
            it = self.lib.by_id(self.list.item(i).data(ID))
            if it:
                out.append(it)
        return out

    def _reorder(self, ids):
        rows = {self.list.item(i).data(ID): self.list.item(i).text() for i in range(self.list.count())}
        self.list.clear()
        for i in ids:
            it = QListWidgetItem(rows[i])
            it.setData(ID, i)
            self.list.addItem(it)

    def _delete(self):
        self.lib.delete_collection(self.cid)
        self.accept()

    def _save(self):
        members = [self.list.item(i).data(ID) for i in range(self.list.count())]
        self.lib.update_collection(self.cid, name=self.name.text(), members=members,
                                   order='custom' if self.custom.isChecked() else 'year')
        self.accept()


# ------------------------------------------------------------------ main window
class Window(QMainWindow):
    def __init__(self, lib, srv, scanner):
        super().__init__()
        self.lib, self.srv, self.scanner = lib, srv, scanner
        self.filter = 'all'
        self.setWindowTitle('PLAY')
        self.resize(1280, 780)
        split = QSplitter()
        split.addWidget(self._rail())
        split.addWidget(self._main())
        split.addWidget(self._detail())
        split.setSizes([200, 800, 330])
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        self.setCentralWidget(split)
        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(QLabel('Files are never renamed or moved'))
        scanner.changed.connect(self._changed)
        self.timer = QTimer(self, interval=1500, timeout=self._tick)
        self.timer.start()
        self.refresh()

    # ---- layout
    def _rail(self):
        w = QWidget()
        w.setMinimumWidth(190)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 0, 6)
        self.rail = QListWidget()
        self.rail.setObjectName('rail')
        for key, label in FILTERS:
            it = QListWidgetItem(label)
            it.setData(ID, key)
            it.setSizeHint(QSize(0, 30))
            self.rail.addItem(it)
        self.rail.setCurrentRow(0)
        self.rail.currentItemChanged.connect(self._filter_changed)
        v.addWidget(self.rail)
        s = QPushButton('Settings')
        s.setObjectName('chip')
        s.clicked.connect(self.open_settings)
        v.addWidget(s)
        return w

    def _main(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        head = QHBoxLayout()
        self.heading = QLabel('Everything')
        self.heading.setObjectName('heading')
        head.addWidget(self.heading)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search titles (Ctrl+F)')
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self.refresh)
        f = QAction(self)
        f.setShortcut('Ctrl+F')
        f.triggered.connect(lambda: self.search.setFocus())
        self.addAction(f)
        head.addWidget(self.search)
        v.addLayout(head)
        self.banner = QFrame()
        self.banner.setObjectName('banner')
        b = QHBoxLayout(self.banner)
        b.setContentsMargins(10, 6, 6, 6)
        self.banner_text = QLabel()
        b.addWidget(self.banner_text, 1)
        nn, mk = QPushButton('Not now'), QPushButton('Make collection')
        nn.setObjectName('chip')
        mk.setObjectName('primary')
        nn.clicked.connect(self._dismiss)
        mk.clicked.connect(self._make_suggested)
        b.addWidget(nn)
        b.addWidget(mk)
        v.addWidget(self.banner)
        self.model = QStandardItemModel()
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(self._double)
        self.view.selectionModel().selectionChanged.connect(self._sel_changed)   # once: it outlives model.clear()
        v.addWidget(self.view, 1)
        return w

    def _detail(self):
        w = QWidget()
        w.setObjectName('detail')
        w.setMinimumWidth(280)
        v = QVBoxLayout(w)
        self.d_heading = QLabel('Nothing selected')
        self.d_heading.setObjectName('heading')
        self.d_heading.setWordWrap(True)
        v.addWidget(self.d_heading)
        v.addWidget(QLabel('Title on the TV'))
        self.d_title = QLineEdit()
        self.d_title.editingFinished.connect(self._title_done)
        v.addWidget(self.d_title)
        row = QHBoxLayout()
        self.d_movie, self.d_show = QPushButton('Movie'), QPushButton('Show'),
        self.d_kids, self.d_hide = QPushButton('Kids'), QPushButton('Hide')
        self.kind_group = QButtonGroup(self)
        for b in (self.d_movie, self.d_show):
            b.setCheckable(True)
            b.setObjectName('chip')
            self.kind_group.addButton(b)
            row.addWidget(b)
        row.addSpacing(10)
        for b in (self.d_kids, self.d_hide):
            b.setCheckable(True)
            b.setObjectName('chip')
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.d_movie.clicked.connect(lambda: self._set_kind('movie'))
        self.d_show.clicked.connect(lambda: self._set_kind('episode'))
        self.d_kids.clicked.connect(lambda on: self._set_all(kids=on))
        self.d_hide.clicked.connect(lambda on: self._set_all(hidden=on))
        self.ep_box = QWidget()
        e = QHBoxLayout(self.ep_box)
        e.setContentsMargins(0, 0, 0, 0)
        self.d_showname = QLineEdit()
        self.d_showname.setPlaceholderText('Show name')
        self.d_season, self.d_ep = QSpinBox(), QSpinBox()
        self.d_season.setRange(0, 99)          # season 0 = specials
        self.d_ep.setRange(1, 999)
        self.d_season.setPrefix('S ')
        self.d_ep.setPrefix('E ')
        e.addWidget(self.d_showname, 1)
        e.addWidget(self.d_season)
        e.addWidget(self.d_ep)
        apply_ep = QPushButton('Set')
        apply_ep.setObjectName('chip')
        apply_ep.clicked.connect(self._apply_episode)
        e.addWidget(apply_ep)
        v.addWidget(self.ep_box)
        v.addWidget(QLabel('Collection'))
        self.d_col = QComboBox()
        self.d_col.activated.connect(self._col_picked)
        v.addWidget(self.d_col)
        self.d_file = QLabel()
        self.d_file.setObjectName('muted')
        self.d_file.setWordWrap(True)
        self.d_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.d_file)
        self.d_problems = QLabel()
        self.d_problems.setObjectName('problem')
        self.d_problems.setWordWrap(True)
        v.addWidget(self.d_problems)
        self.d_open = QPushButton('Show in folder')
        self.d_open.setObjectName('chip')
        self.d_open.clicked.connect(self._open_folder)
        v.addWidget(self.d_open, 0, Qt.AlignLeft)
        v.addStretch()
        self._detail_for([])
        return w

    # ---- data -> view
    def _tick(self):
        self.scanner.kick()
        self._status()

    def _status(self):
        if self.srv.running:
            tvs = self.srv.tvs()
            t = f'●  Serving on port {self.srv.port}'
            t += f'   ·   {len(tvs)} TV{"s" if len(tvs) != 1 else ""} connected' if tvs else '   ·   waiting for a TV'
        else:
            t = f'○  Not serving. {self.srv.error or ""}'
        its = self.lib._items
        self.status.setText(f'{t}   ·   {len(its)} files')

    def _filter_changed(self, cur, _prev):
        if cur:
            self.filter = cur.data(ID)
            self.heading.setText(cur.text().split('   ')[0])
            self.refresh()

    def _matches(self, i):
        q = self.search.text().strip().lower()
        if q and q not in i['title'].lower() and q not in (i.get('show') or '').lower():
            return False
        f = self.filter
        if f == 'hidden':
            return i['hidden']
        if i['hidden'] and f != 'all':
            return False
        return (f == 'all' or (f == 'movies' and i['kind'] == 'movie') or (f == 'shows' and i['kind'] == 'episode')
                or (f == 'kids' and i['kids']) or (f == 'attention' and i['problems'])
                or (f == 'collections' and i['collection']))

    def _changed(self):
        if self.lib.version != getattr(self, 'drawn', None):   # only rebuild for something not on screen yet
            self.refresh()

    def refresh(self):
        self.drawn = self.lib.version
        items = self.lib._items
        selected = set(self.selected_ids())
        scroll = self.view.verticalScrollBar().value()
        self._rail_counts(items)
        self._banner()
        self.model.clear()
        cols = ['Title', 'Type', 'Show / collection', 'Year', 'Format', 'Size', 'Plays on Roku']
        self.model.setHorizontalHeaderLabels(cols)
        shown = [i for i in items if self._matches(i)]
        root = self.model.invisibleRootItem()
        if self.filter == 'shows':
            self._fill_shows(root, shown)
        elif self.filter == 'collections':
            self._fill_collections(root, shown)
        else:
            for i in shown:
                root.appendRow(self._row(i))
        self.view.expandAll()
        h = self.view.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(cols)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._reselect(selected)
        self._sel_changed()
        self.view.verticalScrollBar().setValue(scroll)
        self._status()

    def _rail_counts(self, items):
        n = {'all': len(items), 'movies': 0, 'shows': len({i['show'].lower() for i in items if i['kind'] == 'episode' and not i['hidden']}),
             'collections': len(self.lib.data['collections']), 'kids': 0, 'hidden': 0, 'attention': 0}
        for i in items:
            n['hidden'] += i['hidden']
            if not i['hidden']:
                n['movies'] += i['kind'] == 'movie'
                n['kids'] += i['kids']
                n['attention'] += bool(i['problems'])
        for r in range(self.rail.count()):
            it = self.rail.item(r)
            key = it.data(ID)
            label = dict(FILTERS)[key]
            it.setText(f'{label}   {n[key]}')
            it.setForeground(QColor('#ff6b5b') if key == 'attention' and n[key] else QColor(suite_style.TEXT))

    def _banner(self):
        sug = self.lib.suggestions()
        self.suggestion = sug[0] if sug else None
        self.banner.setVisible(bool(self.suggestion))
        if self.suggestion:
            s = self.suggestion
            if s['key'].startswith('folder:'):
                why = f'{len(s["members"])} films share the folder "{html.escape(s["name"])}"'
            else:
                why = f'{len(s["members"])} films start with "{html.escape(s["key"].title())}"'
            self.banner_text.setText(f'<b>Looks like a collection:</b> {why}. Group them as '
                                     f'<b>{html.escape(s["name"])}</b> so the TV shows one entry, in release order.')

    def _row(self, i):
        coll = self.lib.data['collections'].get(i['collection'], {}).get('name', '') if i['collection'] else ''
        where = f"{i['show']} · Season {i['season']}" if i['kind'] == 'episode' else coll
        cells = [i['title'], 'Episode' if i['kind'] == 'episode' else 'Movie', where, str(i.get('year') or ''),
                 i['format'].upper(), human_size(i['size']),
                 'Check' if i['problems'] else 'Yes']
        row = [QStandardItem(c) for c in cells]
        row[0].setData(i['id'], ID)
        row[0].setData('item', KIND)
        if i['kids']:
            row[0].setText(i['title'] + '   ·  Kids')
        if i['hidden']:
            for c in row:
                c.setForeground(QColor(suite_style.PLACEHOLDER))
        row[6].setForeground(QColor('#ff6b5b' if i['problems'] else '#5fd38d'))
        if i['problems']:
            row[6].setToolTip('\n'.join(i['problems']))
        row[5].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return row

    def _fill_shows(self, root, shown):
        shows = {}
        for i in shown:
            shows.setdefault(i['show'], {}).setdefault(i['season'], []).append(i)
        for name in sorted(shows, key=names.sort_key):
            n = sum(len(e) for e in shows[name].values())
            top = QStandardItem(f'{name}   ({len(shows[name])} season{"s" if len(shows[name]) > 1 else ""}, {n} episodes)')
            top.setData('show', KIND)
            for s in sorted(shows[name]):
                si = QStandardItem(f'Season {s}')
                si.setData('season', KIND)
                for i in sorted(shows[name][s], key=lambda x: x['episode']):
                    si.appendRow(self._row(i))
                top.appendRow([si])
            root.appendRow([top])

    def _fill_collections(self, root, shown):
        ids = {i['id'] for i in shown}
        for cid, c in sorted(self.lib.data['collections'].items(), key=lambda kv: names.sort_key(kv[1]['name'])):
            ms = [m for m in self.lib.collection_members(cid) if m['id'] in ids]
            if not ms:
                continue
            order = 'release order' if c.get('order', 'year') == 'year' else 'custom order'
            top = QStandardItem(f"{c['name']}   ({len(ms)} films, {order})")
            top.setData(cid, ID)
            top.setData('collection', KIND)
            for m in ms:
                top.appendRow(self._row(m))
            root.appendRow([top])

    # ---- selection
    def selected_ids(self):
        out = []
        for idx in self.view.selectionModel().selectedRows(0) if self.view.selectionModel() else []:
            if idx.data(KIND) == 'item':
                out.append(idx.data(ID))
        return out

    def _reselect(self, ids):
        if not ids:
            return
        sm = self.view.selectionModel()

        def walk(parent):
            for r in range(self.model.rowCount(parent)):
                idx = self.model.index(r, 0, parent)
                if idx.data(KIND) == 'item' and idx.data(ID) in ids:
                    sm.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                walk(idx)
        walk(self.model.index(-1, -1).parent())

    def _sel_changed(self, *_):
        self._detail_for([self.lib.by_id(i, 3600) for i in self.selected_ids()])

    def _detail_for(self, its):
        its = [i for i in its if i]
        self.sel = its
        on = bool(its)
        for wdg in (self.d_title, self.d_movie, self.d_show, self.d_kids, self.d_hide, self.d_col, self.d_open):
            wdg.setEnabled(on)
        self.ep_box.setVisible(len(its) == 1 and its[0]['kind'] == 'episode')
        if not on:
            self.d_heading.setText('Nothing selected')
            self.d_title.clear()
            self.d_file.setText('Pick a title to change how it shows on the TV.')
            self.d_problems.clear()
            self.d_col.clear()
            return
        one = its[0] if len(its) == 1 else None
        self.d_heading.setText(one['title'] if one else f'{len(its)} titles')
        self.d_title.setEnabled(bool(one))
        same = one and getattr(self, '_title_for', None) == one['id']
        if not (same and self.d_title.isModified()):     # don't wipe a title you're in the middle of typing
            self.d_title.setText(one['title'] if one else '')
        self._title_for = one['id'] if one else None
        kinds = {i['kind'] for i in its}
        self.d_movie.setChecked(kinds == {'movie'})
        self.d_show.setChecked(kinds == {'episode'})
        self.d_kids.setChecked(all(i['kids'] for i in its))
        self.d_hide.setChecked(all(i['hidden'] for i in its))
        if one and one['kind'] == 'episode':
            self.d_showname.setText(one['show'])
            self.d_season.setValue(one['season'])
            self.d_ep.setValue(one['episode'])
        self.d_col.clear()
        self.d_col.addItem('Not in a collection', None)
        for cid, c in sorted(self.lib.data['collections'].items(), key=lambda kv: names.sort_key(kv[1]['name'])):
            self.d_col.addItem(c['name'], cid)
        self.d_col.addItem('New collection…', '__new__')
        cols = {i['collection'] for i in its}
        if len(cols) == 1:
            self.d_col.setCurrentIndex(max(0, self.d_col.findData(cols.pop())))
        self.d_col.setEnabled(all(i['kind'] == 'movie' for i in its))
        if one:
            self.d_file.setText(f"{one['file']}\n{one['format'].upper()} · {human_size(one['size'])}\n{os.path.dirname(one['path'])}")
            self.d_problems.setText('\n'.join(one['problems']))
        else:
            self.d_file.setText(f"{len(its)} files selected. Kids, Hide and Collection apply to all of them.")
            self.d_problems.clear()

    # ---- edits
    def _set_all(self, **kv):
        self.lib.set_items([i['id'] for i in self.sel], **kv)
        self.refresh()

    def _title_done(self):
        if len(self.sel) == 1:
            t = self.d_title.text().strip()
            if t and t != self.sel[0]['title']:
                self.lib.set_item(self.sel[0]['id'], title=t)
                self.refresh()

    def _set_kind(self, kind):
        if kind == 'movie':
            self._set_all(kind='movie')
            return
        if len(self.sel) != 1:
            QMessageBox.information(self, 'PLAY', 'Pick one file at a time to make it an episode.')
            self._detail_for(self.sel)
            return
        i = self.sel[0]
        if i['kind'] == 'episode':
            return
        self.ep_box.setVisible(True)
        self.d_showname.setText(i['title'])
        self.d_showname.setFocus()

    def _apply_episode(self):
        if len(self.sel) != 1 or not self.d_showname.text().strip():
            return
        i = self.sel[0]
        s, e = self.d_season.value(), self.d_ep.value()
        self.lib.set_item(i['id'], kind='episode', show=self.d_showname.text().strip(), season=s, episode=e,
                          title=f"{self.d_showname.text().strip()} S{s:02d}E{e:02d}")
        self.refresh()

    def _col_picked(self, _row):
        cid = self.d_col.currentData()
        ids = [i['id'] for i in self.sel]
        if cid == '__new__':
            name, ok = QInputDialog.getText(self, 'New collection', 'Name on the TV:',
                                            text=self.sel[0]['title'] if self.sel else '')
            if ok and name.strip():
                self.lib.make_collection(name, ids)
        elif cid is None:
            for c_id in {i['collection'] for i in self.sel if i['collection']}:
                rest = [m for m in self.lib.data['collections'][c_id]['members'] if m not in ids]
                self.lib.update_collection(c_id, members=rest)
        else:
            c = self.lib.data['collections'][cid]
            self.lib.update_collection(cid, members=c['members'] + [i for i in ids if i not in c['members']])
        self.refresh()

    def _dismiss(self):
        if self.suggestion:
            self.lib.dismiss(self.suggestion['key'])
            self.refresh()

    def _make_suggested(self):
        s = self.suggestion
        if s:
            cid = self.lib.make_collection(s['name'], s['members'])
            self.refresh()
            CollectionDialog(self, self.lib, cid).exec()
            self.refresh()

    def _double(self, idx):
        if idx.sibling(idx.row(), 0).data(KIND) == 'collection':
            CollectionDialog(self, self.lib, idx.sibling(idx.row(), 0).data(ID)).exec()
            self.refresh()

    def _menu(self, pos):
        idx = self.view.indexAt(pos)
        m = QMenu(self)
        if idx.isValid() and idx.sibling(idx.row(), 0).data(KIND) == 'collection':
            m.addAction('Edit collection…', lambda: self._double(idx))
        elif self.sel:
            m.addAction('Show in folder', self._open_folder)
        else:
            return
        m.exec(self.view.viewport().mapToGlobal(pos))

    def _open_folder(self):
        if self.sel:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(self.sel[0]['path'])])

    def open_settings(self, first_run=False):
        d = SettingsDialog(self, self.lib, first_run)
        if d.exec() != QDialog.Accepted:
            return
        folders, port, skin, auto = d.values()
        self.lib.set_folders(folders)
        self.lib.set_value('skin', skin)
        apply_skin(QApplication.instance(), skin)
        try:
            set_autostart(auto)
        except OSError as e:
            QMessageBox.warning(self, 'PLAY', f'Could not change "Start with Windows": {e}')
        if port != self.srv.port or not self.srv.running:
            self.restart_server(port)
        self.scanner.kick()

    def restart_server(self, port=None):
        """Restart on `port` (default: the saved one). If that port is busy, go back to the old one and say so;
        the new port is only saved once it works."""
        old = self.srv.port
        new = self.lib.data['port'] if port is None else port
        self.srv.stop()
        self.srv.port = new
        ok = self.srv.start()
        if ok and new != self.lib.data['port']:
            self.lib.set_value('port', new)
        if not ok:
            err = self.srv.error
            if new != old:
                self.srv.port = old
                self.srv.start()
            QMessageBox.warning(self, 'PLAY', err)
        self._status()
        return ok

    def closeEvent(self, e):
        if QSystemTrayIcon.isSystemTrayAvailable() and getattr(self, 'tray', None):
            e.ignore()
            self.hide()
            if not getattr(self, '_told', False):
                self._told = True
                self.tray.showMessage('PLAY is still running', 'Your TVs can keep watching. Right-click the '
                                      'PLAY icon to quit.', play_icon(), 4000)
        else:                          # no tray to live in: closing the window quits PLAY
            e.accept()
            QApplication.instance().quit()


EXTRA_QSS = """
    QLabel#heading {{ font-size:20px; font-weight:700; }}
    QLabel#muted {{ color:#9aa0aa; }}
    QLabel#problem {{ color:#ff8a8a; }}
    QFrame#banner {{ background:{sel}; border:1px solid {accent}; border-radius:6px; }}
    QListWidget#rail {{ border:0; background:transparent; }}
    QListWidget#rail::item {{ padding:4px 8px; border-radius:4px; }}
    QListWidget#rail::item:selected {{ background:{sel}; color:#ffffff; }}
    QPushButton#chip:checked {{ background:{accent}; color:#04121f; border-color:{accent}; font-weight:600; }}
    QWidget#detail {{ background:{bar}; }}
"""


def apply_skin(app, name):
    t = skins.get(name)
    suite_style.apply(app, t)
    app.setStyleSheet(app.styleSheet() + EXTRA_QSS.format(**t))
    return t


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    # one PLAY at a time: a second launch just opens the first one's window
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b'show')
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return 0
    try:   # the uninstaller checks this mutex and asks you to quit PLAY first
        import ctypes
        k32 = ctypes.windll.kernel32
        main.mutex = [k32.CreateMutexW(None, False, n) for n in ('PLAYAppMutex', 'Global\\PLAYAppMutex')]
    except (AttributeError, OSError):
        pass
    lib = library.Library()
    t = apply_skin(app, lib.data['skin'])
    app.setWindowIcon(play_icon(t['accent']))
    srv = server.PlayServer(lib, port=lib.data['port'], accent=lambda: skins.get(lib.data['skin'])['accent'])
    srv.start()
    scanner = Scanner(lib)
    w = Window(lib, srv, scanner)
    tray = QSystemTrayIcon(play_icon(t['accent']), app)
    tray.setToolTip('PLAY')
    menu = QMenu()
    menu.addAction('Open library', lambda: (w.showNormal(), w.raise_(), w.activateWindow()))
    menu.addAction('Restart server', w.restart_server)
    menu.addSeparator()
    menu.addAction('Quit PLAY', app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and (w.showNormal(), w.activateWindow()))
    tray.show()
    w.tray = tray
    listener = QLocalServer()
    QLocalServer.removeServer(INSTANCE_KEY)
    listener.listen(INSTANCE_KEY)
    def second_launch():
        conn = listener.nextPendingConnection()
        if conn:
            conn.disconnected.connect(conn.deleteLater)
            conn.disconnectFromServer()
        w.showNormal()
        w.raise_()
        w.activateWindow()
    listener.newConnection.connect(second_launch)
    scanner.kick()
    if '--tray' not in argv:
        w.show()
    if not lib.folders:
        w.show()
        QTimer.singleShot(200, lambda: w.open_settings(first_run=True))
    if not srv.running:
        QTimer.singleShot(400, lambda: QMessageBox.warning(w, 'PLAY', srv.error))
    rc = app.exec()
    srv.stop()
    return rc
