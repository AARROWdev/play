"""PLAY for Windows: the library window, settings and tray icon. The movie server runs inside this program."""
import html
import os
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap, QStandardItem,
                           QStandardItemModel)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QSpinBox, QSplitter, QSystemTrayIcon, QTreeView, QVBoxLayout, QWidget)

import suite_style
from . import instance, library, media, names, server, skins
from .names import plural

APP = 'PLAY'
INSTANCE_KEY = os.environ.get('PLAY_INSTANCE_KEY', 'PLAY-library-window')   # (tests use their own)
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
ID = Qt.UserRole + 1          # item id (file fingerprint) or collection id
KIND = Qt.UserRole + 2        # 'item' | 'collection' | 'show' | 'season'

FILTERS = [('all', 'Everything'), ('movies', 'Movies'), ('shows', 'Shows'), ('collections', 'Collections'),
           ('kids', 'Kids'), ('hidden', 'Hidden'), ('attention', 'Needs a look')]


def play_icon(accent=None, size=256):
    return QIcon(play_pixmap(accent, size))


def play_pixmap(accent=None, size=256):
    """The PLAY mark drawn at exactly size x size pixels (the .ico gets one per size, so small ones stay crisp)."""
    accent = accent or skins.accent()
    pm = QPixmap(size, size)
    pm.setDevicePixelRatio(1)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(skins.get(None)['base']))
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
    return pm


def run_dialog(d, *args):
    """exec() a dialog or menu, then let Qt delete it (a tray app runs for weeks; they mustn't pile up)."""
    try:
        return d.exec(*args)
    finally:
        d.deleteLater()


def ask(parent, title, text):
    """Yes/No, No by default, the text shown as plain text."""
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, parent)
    box.setTextFormat(Qt.PlainText)
    box.setDefaultButton(QMessageBox.No)
    try:
        return box.exec() == QMessageBox.Yes
    finally:
        box.deleteLater()


class _Progress(QProgressDialog):
    """A progress window that stays until its job is done: Esc and the close box do nothing (a move can't be
    half-cancelled from here)."""
    job_done = False

    def reject(self):
        if self.job_done:
            super().reject()

    def closeEvent(self, e):
        if self.job_done:
            super().closeEvent(e)
        else:
            e.ignore()


def run_with_progress(parent, title, job, total):
    """job(progress) in a worker thread, with a progress window meanwhile (the window stays responsive).
    progress(i, total, name) updates it. Returns job's result."""
    dlg = _Progress(title, None, 0, total, parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumDuration(0)
    dlg.setWindowModality(Qt.WindowModal)
    box, state = {}, {'i': 0, 'name': ''}

    def progress(i, n, name):
        state.update(i=i, name=name)

    def run():
        try:
            box['value'] = job(progress)
        except BaseException as e:                 # handed back below
            box['error'] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        dlg.setValue(state['i'])
        dlg.setLabelText(f'{title}: {state["name"]}')
        QApplication.processEvents()
        t.join(0.05)
    dlg.job_done = True
    dlg.setValue(total)
    dlg.deleteLater()
    if 'error' in box:
        raise box['error']
    return box['value']


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


def installed_copy(exe=None):
    """Is this the installed PLAY: the installer's uninstaller sits next to it (whatever folder was chosen)? A
    portable copy, an exe opened straight from a zip (Windows runs it from a temp folder it later deletes) or a
    source run never adds itself to sign-in."""
    if exe is None:
        if not getattr(sys, 'frozen', False):
            return False
        exe = sys.executable
    return os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(exe)), 'unins000.exe'))


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
        self.changed.emit()             # the window compares versions and redraws only if something changed


# ------------------------------------------------------------------ dialogs
class SettingsDialog(QDialog):
    def __init__(self, parent, lib, first_run=False):
        super().__init__(parent)
        self.setWindowTitle('PLAY settings')
        self.setMinimumWidth(620)
        self.lib = lib
        v = QVBoxLayout(self)
        if first_run:
            hi = QLabel('<b>Welcome to PLAY.</b> Your movies and shows go in the folder below (Add media puts '
                        'them there for you). If they are somewhere else already, add that folder too.')
            hi.setWordWrap(True)
            v.addWidget(hi)
        note = QLabel('Media folders. The first one is the default: Add media moves files there. Kids, movies and '
                      'shows are marked in the library, not by folder.')
        note.setWordWrap(True)
        v.addWidget(note)
        self.folders = QListWidget()
        self.folders.setSelectionMode(QAbstractItemView.SingleSelection)
        paths = [f['path'] for f in lib.folders] or ([library.default_folder_path()] if first_run else [])
        for p in paths:
            self._add_row(p)
        self._mark_default()
        v.addWidget(self.folders)
        row = QHBoxLayout()
        add, rem, dft = QPushButton('Add folder…'), QPushButton('Remove'), QPushButton('Make default')
        for b in (add, rem, dft):
            b.setObjectName('chip')
        add.clicked.connect(self._add)
        rem.clicked.connect(self._remove)
        dft.clicked.connect(self._make_default)
        row.addWidget(add)
        row.addWidget(rem)
        row.addWidget(dft)
        row.addStretch()
        v.addLayout(row)
        grid = QHBoxLayout()
        grid.addWidget(QLabel('Port'))
        self.port = QSpinBox()
        self.port.setRange(library.PORT_MIN, library.PORT_MAX)
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
        self.auto_was = get_autostart()
        self.auto.setChecked(self.auto_was)
        grid.addWidget(self.auto)
        grid.addStretch()
        v.addLayout(grid)
        row2 = QHBoxLayout()
        restart = QPushButton('Restart server')
        restart.setObjectName('chip')
        restart.setToolTip('Stop and start the movie server, e.g. after the network changed')
        restart.clicked.connect(lambda: parent.restart_server(self.port.value(), save=False))
        row2.addWidget(restart)
        row2.addStretch()
        v.addLayout(row2)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName('primary')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _paths(self):
        return [self.folders.item(i).data(Qt.UserRole) for i in range(self.folders.count())]

    def _add_row(self, path):
        it = QListWidgetItem(path)
        it.setData(Qt.UserRole, path)
        self.folders.addItem(it)

    def _mark_default(self):
        """The first folder is the default: it says so."""
        for i in range(self.folders.count()):
            it = self.folders.item(i)
            path = it.data(Qt.UserRole)
            it.setText(f'{path}   (default: new media goes here)' if i == 0 else path)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, 'Add a media folder')
        if d:
            self._add_row(os.path.normpath(d))
            self._mark_default()

    def _remove(self):
        self.folders.takeItem(self.folders.currentRow())
        self._mark_default()

    def _make_default(self):
        row = self.folders.currentRow()
        if row > 0:
            self.folders.insertItem(0, self.folders.takeItem(row))
            self.folders.setCurrentRow(0)
            self._mark_default()

    def values(self):
        fs = [{'path': p, 'kids': False} for p in self._paths()]   # (an older Kids folder: the library keeps it)
        return fs, self.port.value(), self.skin.currentText(), self.auto.isChecked()


GONE_NOTE = 'That title is no longer in the library: the edit wasn\'t saved'


def title_line(i):
    """How a title reads in a pick list: "Heat (1995)", "Bluey · S1 E3 · The Pool", "Bluey · S1 E3-E4"."""
    if i['kind'] == 'episode':
        season = 'Specials' if i.get('season') == 0 else f"S{i.get('season')}"
        last = i.get('episode_last')
        ep = f"E{i.get('episode')}" + (f"-E{last}" if last and last != i.get('episode') else '')
        line = f"{i['show']} · {season} {ep}"
        t = i['title'].strip()
        # the episode's own name: a title typed in PLAY (not the automatic one it makes from the numbers)
        if t and i.get('typed_title'):
            line += f' · {t}'
        return line
    return f"{i['title']} ({i['year']})" if i.get('year') else i['title']


class PickDialog(QDialog):
    """Pick titles from a list: a search box, tick boxes, Select all shown. picked() gives the ids ticked."""

    def __init__(self, parent, title, items, prompt, button='Add'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)
        v = QVBoxLayout(self)
        note = QLabel(prompt)
        note.setWordWrap(True)
        v.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        self.list = QListWidget()
        for i in sorted(items, key=lambda i: (names.sort_key(i['show'] if i['kind'] == 'episode' else i['title']),
                                              i.get('season') or 0, i.get('episode') or 0)):
            it = QListWidgetItem(title_line(i))
            it.setData(ID, i['id'])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda *_: self._count())
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        allb, noneb = QPushButton('Select all shown'), QPushButton('Clear')
        for b in (allb, noneb):
            b.setObjectName('chip')
            row.addWidget(b)
        allb.clicked.connect(lambda: self._tick_shown(Qt.Checked))
        noneb.clicked.connect(lambda: self._tick_shown(Qt.Unchecked))
        row.addStretch()
        self.count = QLabel()
        row.addWidget(self.count)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText(button)
        self.ok.setObjectName('primary')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._count()

    def _filter(self, text):
        q = text.strip().lower()
        for r in range(self.list.count()):
            it = self.list.item(r)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _tick_shown(self, state):
        self.list.blockSignals(True)                   # (a count per row would be slow on a big library)
        try:
            for r in range(self.list.count()):
                it = self.list.item(r)
                if not it.isHidden():
                    it.setCheckState(state)
        finally:
            self.list.blockSignals(False)
        self._count()

    def picked(self):
        return [self.list.item(r).data(ID) for r in range(self.list.count())
                if self.list.item(r).checkState() == Qt.Checked]

    def _count(self):
        n = len(self.picked())
        self.count.setText(f'{n} picked' if n else '')
        self.ok.setEnabled(bool(n))


class CollectionDialog(QDialog):
    """Make a collection (cid None) or change one: its name, its films (+ Add films: pick from every film, with
    search) and their order."""

    def __init__(self, parent, lib, cid=None, preselect=()):
        super().__init__(parent)
        self.lib, self.cid = lib, cid
        c = lib.data['collections'][cid] if cid else {'name': '', 'order': 'year'}
        self.setWindowTitle('Collection' if cid else 'New collection')
        self.setMinimumSize(520, 520)
        v = QVBoxLayout(self)
        self.name = QLineEdit(c['name'])
        self.name.setPlaceholderText('For example: Comedy')
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
        v.addWidget(QLabel('Films in it'))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.known = {}                     # id -> the film, for every film the dialog has listed
        members = lib.collection_members(cid) if cid else []
        self.shown = [m['id'] for m in members]
        by_id = {i['id']: i for i in lib._items}
        for m in members + [by_id[i] for i in preselect if i in by_id and by_id[i]['kind'] == 'movie'
                            and i not in self.shown]:
            self._add_row(m)
        v.addWidget(self.list, 1)
        self.order.buttonToggled.connect(self._order_changed)
        self._order_changed()
        row = QHBoxLayout()
        add, rem = QPushButton('+ Add films…'), QPushButton('Remove')
        for b in (add, rem):
            b.setObjectName('chip')
            row.addWidget(b)
        add.clicked.connect(self._pick)
        rem.clicked.connect(self._remove)
        row.addStretch()
        if cid:
            dele = QPushButton('Delete collection')
            dele.setObjectName('chip')
            dele.clicked.connect(self._delete)
            row.addWidget(dele)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName('primary')
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_row(self, m):
        self.known[m['id']] = m
        it = QListWidgetItem(f"{m['title']}   {m.get('year') or ''}".rstrip())
        it.setData(ID, m['id'])
        self.list.addItem(it)

    def ids(self):
        return [self.list.item(i).data(ID) for i in range(self.list.count())]

    def _pick(self):
        have = set(self.ids())
        films = [i for i in self.lib._items if i['kind'] == 'movie' and i['id'] not in have]
        d = PickDialog(self, 'Add films', films, 'Tick the films to add (search to find them).')
        try:
            if d.exec() != QDialog.Accepted:
                return
            by_id = {i['id']: i for i in films}
            for fid in d.picked():
                self._add_row(by_id[fid])
        finally:
            d.deleteLater()
        self._order_changed()

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _order_changed(self, *_):
        custom = self.custom.isChecked()
        self.list.setDragEnabled(custom)
        if not custom:   # show the year order right away (from the list as it opened: nothing is dropped)
            self._reorder(sorted(self.ids(), key=lambda i: library.release_key(self.known[i])))

    def _reorder(self, ids):
        rows = {self.list.item(i).data(ID): self.list.item(i).text() for i in range(self.list.count())}
        self.list.clear()
        for i in ids:
            it = QListWidgetItem(rows[i])
            it.setData(ID, i)
            self.list.addItem(it)

    def _delete(self):
        if ask(self, 'Delete collection', f'Delete the collection {self.lib.data["collections"][self.cid]["name"]}? '
                                          'The films stay in the library.'):
            self.lib.delete_collection(self.cid)
            self.accept()

    def _save(self):
        name, members = self.name.text().strip(), self.ids()
        order = 'custom' if self.custom.isChecked() else 'year'
        if not name:
            QMessageBox.information(self, 'Collection', 'Give it a name (the TV shows it).')
            return
        kept = self.lib.kept_unshown(self.cid, self.shown) if self.cid else []   # (a drive that's away)
        if not members and not kept:
            QMessageBox.information(self, 'Collection', 'Add at least one film (+ Add films).')
            return
        same = next((k for k, c in self.lib.data['collections'].items()
                     if k != self.cid and c['name'].strip().casefold() == name.casefold()), None)
        if same:
            QMessageBox.information(self, 'Collection', f'There is already a collection called {name}. Give this '
                                    'one another name, or open that one to add these films.')
            return
        mover = getattr(self.parent(), '_moves_ok', None)
        if mover and not mover(members, into=self.cid):
            return
        if self.cid:
            self.lib.update_collection(self.cid, name=name, members=members, shown=self.shown, order=order)
        else:
            self.cid = self.lib.make_collection(name, members, order=order)
        self.accept()


# ------------------------------------------------------------------ main window
class Window(QMainWindow):
    def __init__(self, lib, srv, scanner):
        super().__init__()
        self.lib, self.srv, self.scanner = lib, srv, scanner
        self.filter = 'all'
        self.drawn = None
        self.sel = []
        self._title_for = None
        self._year_for = None
        self._note = None                                # (a passing note for the status bar, and until when)
        self._refreshing = False
        # one redraw after a save, once the event that ended the typing is done (a click lands first); saves in a
        # row restart it, so a gesture redraws once
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(0)
        self._redraw.timeout.connect(lambda: self.refresh())
        self._ep_dirty_for = None
        self._filling = False
        self._told = False
        self.tray = None
        self._view_cache = (None, None, None)       # (library version, rail counts, suggestions)
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
        self.status.setTextFormat(Qt.PlainText)      # parts of it come from the network (a refused Host name)
        self.statusBar().addWidget(self.status, 1)
        self.status_note = QLabel('Files are never renamed or changed')
        self.statusBar().addPermanentWidget(self.status_note)
        scanner.changed.connect(self._changed)
        self.ticks = 0
        self.timer = QTimer(self, interval=1500, timeout=self._tick)
        self.timer.start()
        self.search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.refresh()          # draws what the library has (nothing yet on a fresh start); the first scan is threaded

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
        t = QPushButton('Set up TVs')
        t.setObjectName('chip')
        t.setToolTip('Find the Roku TVs on your network and put PLAY on them')
        t.clicked.connect(self.open_tv_setup)
        v.addWidget(t)
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
        am = QPushButton('Add media…')                  # pick files: they're moved into the default folder
        am.setObjectName('chip')
        am.setToolTip('Pick movies or episodes: PLAY moves them into your default media folder')
        am.clicked.connect(self.add_media)
        head.addWidget(am)
        nc = QPushButton('New collection…')            # always here: any group of films, named as you like
        nc.setObjectName('chip')
        nc.setToolTip('Make a collection: name it (like "Comedy") and pick its films')
        nc.clicked.connect(self.new_collection)
        head.addWidget(nc)
        self.kids_add = QPushButton('+ Add to Kids…')  # only in the Kids view
        self.kids_add.setObjectName('chip')
        self.kids_add.setToolTip('Pick titles from the whole library to show in KIDS')
        self.kids_add.clicked.connect(self.add_to_kids)
        self.kids_add.setVisible(False)
        head.addWidget(self.kids_add)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search titles (Ctrl+F)')
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(lambda _t: self.search_timer.start())   # redraw once typing pauses
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
        self.detail_pane = w
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
        yr = QHBoxLayout()
        yr.addWidget(QLabel('Year'))
        self.d_year = QLineEdit()
        self.d_year.setPlaceholderText('none')
        self.d_year.setMaxLength(4)
        self.d_year.setFixedWidth(70)
        self.d_year.setToolTip('The release year: shown on the TV, and it puts collections in release order')
        self.d_year.editingFinished.connect(self._year_done)
        yr.addWidget(self.d_year)
        yr.addStretch()
        v.addLayout(yr)
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
        self.d_ep.setRange(0, 999)             # episode 0 = a pilot / special
        self.d_showname.textEdited.connect(self._ep_edited)
        self.d_season.valueChanged.connect(self._ep_edited)
        self.d_ep.valueChanged.connect(self._ep_edited)
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
    def scan_every(self):
        """Seconds between folder scans: 6 with the window on screen, 30 in the tray or minimized."""
        return 6 if self.isVisible() and not self.isMinimized() else 30

    def _tick(self):
        """Status every 1.5 s; a folder scan when the last one is older than scan_every()."""
        if not self.lib._scanned or time.monotonic() - self.lib._scanned >= self.scan_every():
            self.scanner.kick()
        self._status()

    def _status(self):
        every = []
        if self.srv.running:
            tvs = self.srv.tvs()
            every = server.lan_ips()                 # once: the bar and the tooltip agree
            ips = every[:2]
            where = ' or '.join(f'{ip}:{self.srv.port}' for ip in ips) if ips else f'port {self.srv.port}'
            if len(every) > 2:                       # a VPN, a virtual machine adapter, Mobile Hotspot ...
                where += f' (+{len(every) - 2} more)'
            t = f'●  Serving at {where}'
            t += f'   ·   {plural(len(tvs), "TV")} connected' if tvs else '   ·   waiting for a TV'
        else:
            t = f'○  Not serving. {self.srv.error or ""}'
        notes = []
        if self.srv.running and self.srv.discovery_error:
            notes.append(self.srv.discovery_error)
        refused = self.srv.refused_hosts() if self.srv.running else []
        if refused:
            notes.append(f'A TV used the name {refused[-1]}, which PLAY refuses: type the PC\'s IP address on it')
        if self.lib.unreachable:
            notes.append('Can\'t reach ' + ', '.join(self.lib.unreachable))
        if self.lib.missing:
            notes.append('Not found: ' + ', '.join(self.lib.missing) +
                         ('. Update it in Settings' if len(self.lib.missing) == 1 else '. Update them in Settings'))
        if self.lib.read_only:
            notes.append('Settings are not being saved (see the warning at start)')
        files = plural(len(self.lib._items), 'file') if self.lib._scanned else 'reading your folders…'
        text = f'{t}   ·   {files}' + ''.join(f'   ·   {n}' for n in notes)
        if self._note and time.monotonic() < self._note[1]:
            text = f'{self._note[0]}   ·   {text}'      # first: a long line is cut at its end, not here
        tip = text
        if len(every) > 2:                                         # every address, so none is ever out of reach
            tip += '   ·   All addresses: ' + ', '.join(f'{ip}:{self.srv.port}' for ip in every)
        self.status.setToolTip(html.escape(tip, quote=False).replace('   ·   ', '<br>'))   # escaped: never read as markup
        # the full text is in the tooltip; the bar shows what fits
        room = self.width() - self.status_note.sizeHint().width() - 40     # the bar's width, less the fixed note
        self.status.setText(self.status.fontMetrics().elidedText(text, Qt.ElideRight, max(200, room)))

    def _filter_changed(self, cur, _prev):
        if cur:
            self.filter = cur.data(ID)
            self.heading.setText(cur.text().split('   ')[0])
            self.kids_add.setVisible(self.filter == 'kids')
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
        if self.lib.version != self.drawn:   # only rebuild for something not on screen yet
            self.refresh()

    def refresh(self):
        self._refreshing = True                          # selection changes from here on aren't the user's
        try:
            self._refresh()
        finally:
            self._refreshing = False

    def _refresh(self):
        self._redraw.stop()                              # (this is the redraw a save was waiting for)
        # before the first scan nothing is drawn for real: leave drawn unset so the scan's result is drawn
        self.drawn = self.lib.version if self.lib._scanned else None
        items = self.lib._items
        ready = bool(self.lib._scanned)      # before the first scan nothing may call into the library's views
        selected = set(self.selected_ids())
        scroll = self.view.verticalScrollBar().value()
        self._rail_counts(items, ready)
        self._banner(ready)
        self.model.clear()
        self._row_items = {}
        cols = ['Title', 'Type', 'Show / collection', 'Year', 'Format', 'Size', 'Plays on Roku']
        self.model.setHorizontalHeaderLabels(cols)
        shown = [i for i in items if self._matches(i)]
        root = self.model.invisibleRootItem()
        if self.filter == 'shows':
            self._fill_shows(root, shown)
        elif self.filter == 'collections':
            if ready:
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

    def _view_data(self, items, ready):
        """Rail counts and suggestions, worked out once per library version."""
        key = (self.lib.version, ready)
        if self._view_cache[0] != key:
            n = {'all': len(items), 'movies': 0,
                 'shows': len({names.show_key(i['show']) for i in items if i['kind'] == 'episode' and not i['hidden']}),
                 'collections': len(self.lib.data['collections']), 'kids': 0, 'hidden': 0, 'attention': 0}
            for i in items:
                n['hidden'] += i['hidden']
                if not i['hidden']:
                    n['movies'] += i['kind'] == 'movie'
                    n['kids'] += i['kids']
                    n['attention'] += bool(i['problems'])
            self._view_cache = (key, n, self.lib.suggestions() if ready else [])
        return self._view_cache[1], self._view_cache[2]

    def _rail_counts(self, items, ready=True):
        n, _ = self._view_data(items, ready)
        for r in range(self.rail.count()):
            it = self.rail.item(r)
            key = it.data(ID)
            label = dict(FILTERS)[key]
            it.setText(f'{label}   {n[key]}')
            it.setForeground(QColor('#ff6b5b') if key == 'attention' and n[key] else QColor(suite_style.TEXT))

    def _banner(self, ready=True):
        sug = self._view_data(self.lib._items, ready)[1]
        self.suggestion = sug[0] if sug else None
        self.banner.setVisible(bool(self.suggestion))
        if self.suggestion:
            s = self.suggestion
            if s['key'].startswith('folder:'):
                why = f'{len(s["members"])} films share the folder "{html.escape(s["label"])}"'
            else:
                why = f'{len(s["members"])} films start with "{html.escape(s["label"])}"'
            self.banner_text.setText(f'<b>Looks like a collection:</b> {why}. Group them as '
                                     f'<b>{html.escape(s["name"])}</b> so the TV shows one entry, in release order.')

    def _row(self, i):
        coll = self.lib.data['collections'].get(i['collection'], {}).get('name', '') if i['collection'] else ''
        season = 'Specials' if i.get('season') == 0 else f"Season {i.get('season')}"
        where = f"{i['show']} · {season}" if i['kind'] == 'episode' else coll
        cells = [i['title'], 'Episode' if i['kind'] == 'episode' else 'Movie', where, str(i.get('year') or ''),
                 i['format'].upper(), human_size(i['size']),
                 'Yes' if i['roku_ok'] else 'Check']
        row = [QStandardItem(c) for c in cells]
        row[0].setData(i['id'], ID)
        row[0].setData('item', KIND)
        if i['kids']:
            row[0].setText(i['title'] + '   ·  Kids')
        if i['hidden']:
            for c in row:
                c.setForeground(QColor(suite_style.PLACEHOLDER))
        row[6].setForeground(QColor('#5fd38d' if i['roku_ok'] else '#ff6b5b'))
        if i['problems']:              # missing episodes, copies, formats: shown on hover; the column is just playback
            for c in (row[0], row[6]):
                c.setToolTip('\n'.join(i['problems']))
        row[5].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._row_items.setdefault(i['id'], row[0])     # for reselecting by id without walking the tree
        return row

    def _fill_shows(self, root, shown):
        shows, label = {}, {}
        for i in shown:                      # capital letters don't matter, as on the TV
            key = names.show_key(i['show'])
            label.setdefault(key, i['show'])
            shows.setdefault(key, {}).setdefault(i['season'], []).append(i)
        for key in sorted(shows, key=lambda k: names.sort_key(label[k])):
            seasons = shows[key]
            n = sum(len(e) for e in seasons.values())
            top = QStandardItem(f'{label[key]}   ({plural(len(seasons), "season")}, {plural(n, "episode")})')
            top.setData('show', KIND)
            for s in sorted(seasons):
                si = QStandardItem('Specials' if s == 0 else f'Season {s}')
                si.setData('season', KIND)
                for i in sorted(seasons[s], key=lambda x: x['episode']):
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
            top = QStandardItem(f"{c['name']}   ({plural(len(ms), 'film')}, {order})")
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
        """Select these titles again in one go (one selection, signals quiet): a rebuild with thousands selected
        stays instant."""
        if not ids:
            return
        last = self.model.columnCount() - 1
        rows = {}                                   # parent -> row numbers, from the id map (no tree walk)
        for i in ids:
            item = self._row_items.get(i)
            if item is not None:
                idx = item.index()
                rows.setdefault(idx.parent(), []).append(idx.row())
        sel = QItemSelection()
        for parent, rs in rows.items():             # consecutive rows become one range: selectedRows stays fast
            rs.sort()
            start = prev = rs[0]
            for r in rs[1:] + [None]:
                if r is not None and r == prev + 1:
                    prev = r
                    continue
                sel.select(self.model.index(start, 0, parent), self.model.index(prev, last, parent))
                if r is not None:
                    start = prev = r
        sm = self.view.selectionModel()
        sm.blockSignals(True)
        sm.select(sel, QItemSelectionModel.SelectionFlag.Select)
        sm.blockSignals(False)

    def _sel_changed(self, *_):
        if not self.lib._scanned:
            return self._detail_for([])
        ids = self.selected_ids()
        now = ids[0] if len(ids) == 1 else None
        for box, fid, save in ((self.d_year, self._year_for, self._save_year),
                               (self.d_title, self._title_for, self._save_title)):
            if not (fid and fid != now and box.isModified()):
                continue
            if self._refreshing:
                # a redraw moved the selection, not the user: the text may be half typed, so it isn't saved, and
                # that's said
                self._say(GONE_NOTE if self.lib.by_id(fid) is None else
                          'What you were typing wasn\'t saved: that title left this view')
            elif save(fid):                              # typed, then another film picked
                self._redraw.start()
            box.setModified(False)
        self._detail_for([self.lib.by_id(i) for i in ids])

    def _ep_edited(self, *_):
        if len(self.sel) == 1 and not self._filling:
            self._ep_dirty_for = self.sel[0]['id']       # an episode edit in progress: a redraw must keep it

    def _detail_for(self, its):
        its = [i for i in its if i]
        self.sel = its
        on = bool(its)
        self.detail_pane.setVisible(on)                # nothing selected: no panel at all
        for wdg in (self.d_title, self.d_movie, self.d_show, self.d_kids, self.d_hide, self.d_col, self.d_open):
            wdg.setEnabled(on)
        one = its[0] if len(its) == 1 else None
        editing_ep = one is not None and self._ep_dirty_for == one['id']
        if not editing_ep:
            self._ep_dirty_for = None
            self.ep_box.setVisible(len(its) == 1 and its[0]['kind'] == 'episode')
        if not on:                                      # (the panel is hidden)
            self.d_title.clear()
            self.d_year.clear()
            self.d_title.setModified(False)
            self.d_year.setModified(False)
            self._title_for = self._year_for = None
            self.d_col.clear()
            return
        self.d_heading.setText(one['title'] if one else f'{len(its)} titles')
        self.d_title.setEnabled(bool(one))
        self.d_year.setEnabled(bool(one) and one['kind'] == 'movie')
        if not (one and self._year_for == one['id'] and self.d_year.isModified()):   # (not mid-typing)
            self.d_year.setText(str(one.get('year') or '') if one else '')   # (an episode's year is None)
        self._year_for = one['id'] if one else None
        same = one and self._title_for == one['id']
        if not (same and self.d_title.isModified()):     # don't wipe a title you're in the middle of typing
            self.d_title.setText(one['title'] if one else '')
        self._title_for = one['id'] if one else None
        kinds = {i['kind'] for i in its}
        self.kind_group.setExclusive(False)  # a mixed selection lights neither
        self.d_movie.setChecked(kinds == {'movie'})
        self.d_show.setChecked(kinds == {'episode'})
        self.kind_group.setExclusive(True)
        self.d_kids.setChecked(all(i['kids'] for i in its))
        self.d_hide.setChecked(all(i['hidden'] for i in its))
        if one and one['kind'] == 'episode' and not editing_ep:
            self._filling = True
            self.d_showname.setText(one['show'])
            self.d_season.setValue(one['season'])
            self.d_ep.setValue(one['episode'])
            self._filling = False
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
            self.d_file.setText(f"{plural(len(its), 'file')} selected. Kids, Hide and Collection apply to all of them.")
            self.d_problems.clear()

    # ---- edits
    def _set_all(self, **kv):
        self.lib.set_items([i['id'] for i in self.sel], **kv)
        self.refresh()

    YEARS = (names.FIRST_YEAR, 2100)                   # the first films to a little ahead

    def _year_done(self):
        if not self.d_year.isModified():                 # (Enter or focus-out with nothing typed)
            return
        if len(self.sel) == 1 and self._save_year(self.sel[0]['id']):
            self._redraw.start()
        self.d_year.setModified(False)

    def _say(self, text):
        """A passing note at the front of the status bar."""
        self._note = (text, time.monotonic() + 8)
        self._status()

    def _saved(self, fid):
        """After a save: the panel's copy of the film is the saved one, heading too (the redraw comes a moment
        later)."""
        new = self.lib.by_id(fid)
        self.sel = [new if (i['id'] == fid and new is not None) else i for i in self.sel]
        if new is not None and len(self.sel) == 1 and self.sel[0]['id'] == fid:
            self.d_heading.setText(new['title'])

    def _save_year(self, fid):
        """The Year box's text for the film fid: a year saves, empty clears it, anything else is put back.
        True when saved (the caller redraws)."""
        text = self.d_year.text().strip()
        film = self.lib.by_id(fid)
        if film is None:
            self._say(GONE_NOTE)
            return False
        if film['kind'] != 'movie':
            self._say('That\'s an episode now (years are for films): the year wasn\'t saved')
            return False
        # ASCII digits only: '²' or '١' pass isdigit() and int() refuses or misreads them
        year = int(text) if text.isascii() and text.isdigit() else None
        if text and (year is None or not self.YEARS[0] <= year <= self.YEARS[1]):
            self.d_year.setText(str(film.get('year') or ''))    # not a year: back as it was
            self._say(f'"{text}" is not a year ({self.YEARS[0]}-{self.YEARS[1]})')
        elif year != film.get('year'):                         # (empty: no year)
            self.lib.set_item(fid, year=year)
            self._saved(fid)
            return True
        return False

    def _save_title(self, fid):
        """The title box's text for fid, if it's a change. True when saved (the caller redraws)."""
        t = self.d_title.text().strip()
        old = self.lib.by_id(fid)
        if old is None:
            self._say(GONE_NOTE)
            return False
        if t and t == old['title'] and old.get('untitled'):
            self._say("PLAY couldn't read a title from this file name: type the title you want")
        if t and t != old['title']:
            self.lib.set_item(fid, title=t)
            self._saved(fid)
            return True
        return False

    def _title_done(self):
        if not self.d_title.isModified():                # (Enter or focus-out with nothing typed)
            return
        changed = len(self.sel) == 1 and self._save_title(self.sel[0]['id'])
        self.d_title.setModified(False)          # Enter, changed or not: the box follows the title again
        if changed:
            self._redraw.start()

    def _set_kind(self, kind):
        if kind == 'movie':
            self._ep_dirty_for = None             # an episode edit in progress is dropped: it's a movie now
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
        self._filling = True
        self.d_showname.setText(i['title'])
        self.d_season.setValue(1)               # a new episode starts at S01E01, not S00E00
        self.d_ep.setValue(1)
        self._filling = False
        self._ep_dirty_for = i['id']
        self.d_showname.setFocus()

    def _apply_episode(self):
        if len(self.sel) != 1 or not self.d_showname.text().strip():
            return
        i = self.sel[0]
        s, e = self.d_season.value(), self.d_ep.value()
        # no title: the TV title follows show/season/episode (and a title you typed yourself is kept)
        self.lib.set_item(i['id'], kind='episode', show=self.d_showname.text().strip(), season=s, episode=e)
        self._ep_dirty_for = None
        self.refresh()

    def _col_picked(self, _row):
        cid = self.d_col.currentData()
        ids = [i['id'] for i in self.sel]
        if cid == '__new__':
            run_dialog(CollectionDialog(self, self.lib, None, ids))
        elif cid is None:
            self.lib.remove_from_collections(ids)
        elif self._moves_ok(ids, into=cid):
            self._add_to_collection(cid, ids)
        self.refresh()

    def _add_to_collection(self, cid, ids):
        c = self.lib.data['collections'][cid]
        self.lib.update_collection(cid, members=c['members'] + [i for i in ids if i not in c['members']])

    def _moves_ok(self, ids, into=None):
        """A film is in one collection at a time: putting these in a collection takes them out of the others.
        Say which, and which would be left empty (and so removed); True to go ahead."""
        ids, lines = set(ids), []
        for cid, c in sorted(self.lib.data['collections'].items(), key=lambda kv: names.sort_key(kv[1]['name'])):
            if cid == into:
                continue
            out = [m for m in c['members'] if m in ids]
            if out:
                left = len(c['members']) - len(out)
                lines.append(f'{c["name"]}: {plural(len(out), "film")} move out' +
                             (' and it will be removed (nothing left in it)' if not left else ''))
        if not lines:
            return True
        return ask(self, 'Collections', 'These films are in other collections now:\n\n' + '\n'.join(lines) +
                   '\n\nGo ahead?')

    def _dismiss(self):
        if self.suggestion:
            self.lib.dismiss(self.suggestion['key'])
            self.refresh()

    def add_media(self):
        """Pick video files and move them into the default folder (optionally marked Kids)."""
        if self.busy():
            return
        self.moving = True                             # from the file picker on: nothing else starts meanwhile
        try:
            self._add_media()
        finally:
            self.moving = False

    def _add_media(self):
        folder = self.lib.default_folder
        if not folder:
            QMessageBox.information(self, 'Add media', 'First add a media folder in Settings.')
            return
        exts = ' '.join(f'*{e}' for e in sorted(names.VIDEO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add media', '', f'Videos ({exts})')
        if not paths:
            return
        paths = [os.path.normpath(p) for p in paths]
        box = QMessageBox(QMessageBox.Question, 'Add media',
                          f'Move {plural(len(paths), "file")} into {folder}?', QMessageBox.Yes | QMessageBox.No, self)
        box.setTextFormat(Qt.PlainText)
        box.setInformativeText('They are moved, not copied: they leave the folder they are in now.')
        kids = QCheckBox('Mark them Kids')
        box.setCheckBox(kids)
        try:
            if box.exec() != QMessageBox.Yes:
                return
            kids = kids.isChecked()
        finally:
            box.deleteLater()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, 'Add media', f'Could not open {folder}: {e.strerror or e}')
            return
        roots = [f['path'] for f in self.lib.folders]
        moved, failed = run_with_progress(self, 'Adding media', lambda progress: media.move_files(
            paths, folder, roots, progress, fingerprints=kids), len(paths))
        unmarked = []
        if kids:
            fps = [fp for _, fp in moved if fp]
            unmarked = [p for p, fp in moved if not fp]
            if fps:
                self.lib.mark_kids(fps)
        self.scanner.kick()
        notes = []
        if failed:
            notes.append('These were not moved:\n' + '\n'.join(f'{os.path.basename(p)}: {why}' for p, why in failed))
        if unmarked:                                   # moved, but its Kids mark couldn't be set: say so
            notes.append('These were moved but could not be marked Kids (mark them in the library):\n' +
                         '\n'.join(os.path.basename(p) for p in unmarked))
        if notes:
            QMessageBox.warning(self, 'Add media', '\n\n'.join(notes))

    def busy(self):
        """Files are being moved: say so (True) rather than start something else meanwhile."""
        if getattr(self, 'moving', False):
            QMessageBox.information(self, 'PLAY', 'PLAY is adding media to your media folder. Finish that first.')
            return True
        return False

    def quit_app(self):
        if not self.busy():
            QApplication.instance().quit()

    def new_collection(self):
        """The collection window: name it, pick its films (any selected films are in it already), order them."""
        films = [i['id'] for i in self.sel if i['kind'] == 'movie']
        run_dialog(CollectionDialog(self, self.lib, None, films))
        self.refresh()

    def add_to_kids(self):
        """Pick titles from the whole library (those not Kids yet) and mark them Kids."""
        if self.busy():
            return
        if not self.lib._scanned:
            QMessageBox.information(self, 'Add to Kids', 'PLAY is still reading your folders. Try again in a moment.')
            return
        rest = [i for i in self.lib._items if not i['kids'] and not i['hidden']]   # (hidden ones never show)
        if not rest:
            hidden = any(not i['kids'] and i['hidden'] for i in self.lib._items)
            some = any(i['kids'] for i in self.lib._items)
            QMessageBox.information(self, 'Add to Kids', 'There is nothing in the library yet.'
                                    if not self.lib._items else
                                    ('Everything else is hidden' if some else 'Everything in the library is hidden')
                                    + ' (unhide it first: the Hidden view).'
                                    if hidden else 'Everything in the library is in Kids already.')
            return
        d = PickDialog(self, 'Add to Kids', rest, 'Tick what should show in KIDS on the TV. Search a show\'s name '
                       'and use Select all shown to add all its episodes.', button='Add to Kids')
        if run_dialog(d) != QDialog.Accepted:          # (deleted later: picked() still answers now)
            return
        ids = d.picked()
        if ids:
            self.lib.set_items(ids, kids=True)
            self.refresh()

    def _make_suggested(self):
        s = self.suggestion
        if s:
            same = next((k for k, c in self.lib.data['collections'].items()
                         if c['name'].strip().casefold() == s['name'].strip().casefold()), None)
            if same:                                   # one of that name exists: these films join it
                if not self._moves_ok(s['members'], into=same):
                    return
                self._add_to_collection(same, s['members'])
                cid = same
            else:
                cid = self.lib.make_collection(s['name'], s['members'])
            self.refresh()
            run_dialog(CollectionDialog(self, self.lib, cid))
            self._changed()                            # redraws only if the dialog changed something

    def _double(self, idx):
        if idx.sibling(idx.row(), 0).data(KIND) == 'collection':
            self._edit_collection(idx.sibling(idx.row(), 0).data(ID))

    def _edit_collection(self, cid):
        if cid in self.lib.data['collections']:       # it may have gone while a menu was open
            run_dialog(CollectionDialog(self, self.lib, cid))
            self._changed()

    def _menu(self, pos):
        idx = self.view.indexAt(pos)
        # read what the row stands for now: a background redraw while the menu is open frees the model's items
        cid = idx.sibling(idx.row(), 0).data(ID) if idx.isValid() and idx.sibling(idx.row(), 0).data(KIND) == 'collection' else None
        if not cid and not self.sel:
            return
        m = QMenu(self)
        if cid:
            m.addAction('Edit collection…', lambda: self._edit_collection(cid))
        else:
            m.addAction('Show in folder', self._open_folder)
        run_dialog(m, self.view.viewport().mapToGlobal(pos))

    def _open_folder(self):
        if self.sel:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(self.sel[0]['path'])])

    def open_tv_setup(self):
        from .tvsetup import TvSetupDialog
        if self.busy():
            return
        self.bring_up()
        d = getattr(self, '_tv_setup', None)
        if d is not None and d.isVisible():            # already open (the tray, while it's showing): bring it up
            d.raise_()
            d.activateWindow()
            return
        if d is None:                                  # one per window: reopening shows an install still running
            d = self._tv_setup = TvSetupDialog(self, self.lib, self.srv)
        else:
            d.reopen()
        try:
            d.exec()
        finally:
            d.release()

    def open_settings(self, first_run=False):
        if self.busy():
            return
        d = SettingsDialog(self, self.lib, first_run)
        accepted = d.exec() == QDialog.Accepted
        if not accepted and self.srv.port != self.lib.data['port']:
            self.restart_server()                      # a port tried with "Restart server", then Cancel
        try:
            folders, port, skin, auto = d.values()
            auto_was = d.auto_was
        finally:
            d.deleteLater()
        if not accepted:
            return
        if folders and not os.path.isdir(folders[0]['path']) and \
                os.path.normcase(os.path.abspath(folders[0]['path'])) == \
                os.path.normcase(os.path.abspath(library.default_folder_path())):
            try:                                       # the default folder (Videos\\PLAY at first): make it
                os.makedirs(folders[0]['path'], exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, 'PLAY', f'Could not create {folders[0]["path"]}: {e.strerror or e}')
        self.lib.set_folders(folders)
        if skin != skins.name(self.lib.data['skin']):  # restyle only for a new skin (it repolishes every widget)
            self.lib.set_value('skin', skin)
            apply_skin(QApplication.instance(), skin)
            self.set_icons()
        try:   # only when the box was changed: another copy (portable, from source) mustn't remove this one's
            if auto != auto_was:
                set_autostart(auto)
        except OSError as e:
            QMessageBox.warning(self, 'PLAY', f'Could not change "Start with Windows": {e}')
        if port != self.srv.port or not self.srv.running:
            self.restart_server(port)
        elif port != self.lib.data['port']:            # already serving there ("Restart server"): just save it
            self.lib.set_value('port', port)
        self.scanner.kick()

    def restart_server(self, port=None, save=True):
        """Restart on `port` (default: the saved one). If that port is busy, go back to the old one and say so;
        the new port is only saved once it works (and save is True: Settings' button only tries it)."""
        old = self.srv.port
        new = self.lib.data['port'] if port is None else port
        self.srv.stop()
        self.srv.port = new
        server.forget_lan_ips()                        # the network may have changed: look again
        ok = self.srv.start()
        if ok and save and new != self.lib.data['port']:
            self.lib.set_value('port', new)
        if not ok:
            err = self.srv.error
            if new != old:
                self.srv.port = old
                self.srv.start()
            QMessageBox.warning(self, 'PLAY', err)
        self._status()
        return ok

    def icon(self):
        return play_icon(skins.accent(self.lib.data['skin']))

    def set_icons(self):
        """The app, window and tray icons follow the skin."""
        icon = self.icon()
        QApplication.instance().setWindowIcon(icon)
        if self.tray:
            self.tray.setIcon(icon)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._status()                         # re-fit the status text to the new width

    def bring_up(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if QSystemTrayIcon.isSystemTrayAvailable() and self.tray:
            e.ignore()
            self.hide()
            if not self._told:
                self._told = True
                self.tray.showMessage('PLAY is still running', 'Your TVs can keep watching. Right-click the '
                                      'PLAY icon to quit.', self.icon(), 4000)
        elif self.busy():              # no tray: closing quits PLAY, but not in the middle of adding media
            e.ignore()
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
    QListView::indicator {{ width:14px; height:14px; border:1px solid #8a919c; border-radius:3px;
                           background:{base}; }}
    QListView::indicator:checked {{ background:{accent}; border-color:{accent}; }}
"""


def apply_skin(app, name):
    t = skins.get(name)
    suite_style.apply(app, t, EXTRA_QSS.format(**t))
    return t


def hand_over(timeout=5.0):
    """Ask the running PLAY to show its window. Retries while its listener may not be up yet (it is starting), and
    waits for its "ok". True if it answered."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        sock = QLocalSocket()
        sock.connectToServer(INSTANCE_KEY)
        if sock.waitForConnected(500):
            sock.write(b'show')
            sock.waitForBytesWritten(1000)
            ok = sock.waitForReadyRead(2000) and bytes(sock.readAll()).startswith(b'ok')
            sock.disconnectFromServer()
            if ok:
                return True
        time.sleep(0.2)
    return False


def settle_instance(check=None, hand=None, wait=None, tries=3):
    """instance.check(), and when another copy in this session doesn't answer the hand-over (it was quitting: Quit,
    then a quick relaunch), check again a second later, so this copy starts once the old one is gone."""
    import time
    check = check or instance.check
    hand = hand or hand_over
    wait = wait or time.sleep
    who = check()
    for _ in range(tries):
        if who != instance.SAME_SESSION or hand():
            return who
        wait(1)
        who = check()
    return who


def offer_autostart(lib, installed):
    """The installed PLAY's first start for this user turns on "Start with Windows", so the TVs can always reach it.
    Once only: a portable or zip trial before doesn't use it up, an upgrade from a build that had no such flag
    counts as offered (it keeps what the user chose), and a settings file PLAY can't write is left alone (it would
    turn it back on at every start). Returns True if it turned it on."""
    if not installed or lib.data['autostart_offered'] or lib.read_only:
        return False
    def unoffer():
        with lib.lock:
            lib.data['autostart_offered'] = False
        try:
            lib.save()                             # after the reset: a scan's save in between is overwritten
        except OSError:
            pass
    try:
        lib.set_value('autostart_offered', True)   # recorded first: if it can't be, nothing is switched on
    except OSError:
        unoffer()
        return False
    try:
        set_autostart(True)
    except OSError:                                # not switched on after all: offer it again next start
        unoffer()
        return False
    return True


def other_session_notice(argv, session=None):
    """Another Windows user (or a scheduled task) already runs PLAY. Say so only when someone launched this copy
    by hand; at sign-in (--tray) or in session 0 there is no one to read it, and a box nobody closes would keep
    this process alive."""
    if '--tray' in argv or (instance.session_id() if session is None else session) == 0:
        return False
    QMessageBox.information(None, 'PLAY', 'PLAY is already running on this PC, for another Windows user or as a '
                            'scheduled task, and serving the TVs there. This copy will close.')
    return True


def start_listener(on_show):
    """The listener a second launch hands over to: it answers "ok" and calls on_show."""
    listener = QLocalServer()

    def second_launch():
        while listener.hasPendingConnections():
            conn = listener.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)

            def answer(c=conn):
                if getattr(c, '_answered', False):
                    return
                c._answered = True
                c.readAll()
                c.write(b'ok')
                c.flush()
                c.disconnectFromServer()
                on_show()
            conn.readyRead.connect(answer)
            if conn.bytesAvailable():                 # the "show" may already be here: readyRead won't come again
                answer()
    listener.newConnection.connect(second_launch)          # connected before listen(): no connection is missed
    QLocalServer.removeServer(INSTANCE_KEY)
    listener.listen(INSTANCE_KEY)
    return listener


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    # One PLAY per PC. In this session: hand over to the running copy (it shows its window) and exit. For another
    # Windows account: say so and exit. Never a second server. The hand-over listener starts before the first
    # scan, so a launch during a slow startup scan still reaches it.
    who = settle_instance()
    if who == instance.SAME_SESSION:
        return 0
    if who == instance.OTHER_SESSION:
        other_session_notice(argv)
        return 0
    shown = {'window': None}
    # listening starts before the first scan; a hand-over that arrives early is answered once the loop runs
    listener = start_listener(lambda: shown['window'] and shown['window'].bring_up())
    lib = library.Library()
    apply_skin(app, lib.data['skin'])
    srv = server.PlayServer(lib, port=lib.data['port'], accent=lambda: skins.accent(lib.data['skin']))
    srv.start()
    offer_autostart(lib, installed_copy())
    scanner = Scanner(lib)
    w = Window(lib, srv, scanner)
    tray = QSystemTrayIcon(app)
    tray.setToolTip('PLAY')
    menu = QMenu()
    menu.addAction('Open library', w.bring_up)
    menu.addAction('Set up TVs…', w.open_tv_setup)
    menu.addAction('Restart server', w.restart_server)
    menu.addSeparator()
    menu.addAction('Quit PLAY', w.quit_app)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and w.bring_up())
    w.tray = tray
    w.set_icons()
    tray.show()
    w.listener = listener
    shown['window'] = w
    scanner.kick()
    if '--tray' not in argv:
        w.show()
    if not lib.folders:
        w.show()
        QTimer.singleShot(200, lambda: w.open_settings(first_run=True))
    if not srv.running:
        QTimer.singleShot(400, lambda: QMessageBox.warning(w, 'PLAY', srv.error))
    if lib.load_problem:
        w.show()
        QTimer.singleShot(300, lambda: QMessageBox.warning(w, 'PLAY', lib.load_problem))
    rc = app.exec()
    srv.stop()
    return rc
