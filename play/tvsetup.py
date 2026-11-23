"""Set up TVs: a small wizard that finds the Roku TVs on the network, says what each one needs, and installs PLAY's
TV app on them. It can be opened any time (a new TV, or the TV app after a PLAY update)."""
import html
import ipaddress
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import roku, server

IP = Qt.UserRole

CHECKING, OFFLINE, BLOCKED, NO_DEV, READY, OTHER, INSTALLED, OLDER, INSTALLING, DONE, FAILED = (
    'checking', 'offline', 'blocked', 'no_dev', 'ready', 'other', 'installed', 'older', 'installing', 'done',
    'failed')

STATUS = {
    CHECKING: 'Checking…',
    OFFLINE: 'Not answering',
    BLOCKED: 'Needs a setting changed',
    NO_DEV: 'Developer Mode is off',
    READY: 'Ready to install',
    OTHER: 'Has another developer app',
    INSTALLED: 'PLAY is on this TV',
    OLDER: 'Update available',
    INSTALLING: 'Installing…',
    DONE: 'Installed',
    FAILED: 'Install didn\'t work',
}

DEV_STEPS = (
    '<b>Turn on Developer Mode</b> (once per TV, about a minute):'
    '<ol><li>On the Roku remote press: <b>Home ×3, Up ×2, Right, Left, Right, Left, Right</b>.</li>'
    '<li>On the TV, choose <b>Enable installer and restart</b> and accept the agreement.</li>'
    '<li>Set a <b>developer password</b> (PLAY asks for it once and remembers it for this TV).</li>'
    '<li>When the TV has restarted, click <b>Search again</b> here.</li></ol>')
BLOCKED_STEPS = (
    '<b>This TV won\'t answer PLAY\'s questions.</b> On the TV open <b>Settings › System › Advanced system '
    'settings › Control by mobile apps</b> and set <b>Network access</b> to <b>Default</b>, then click '
    '<b>Search again</b>.<br><br>If Developer Mode is already on, you can still install.')
NONE_FOUND = (
    '<b>No Roku found.</b> Check that the TV is on and on the same Wi-Fi or network as this PC, then click '
    '<b>Search again</b>.<br><br>You can also add it by address: on the TV, <b>Settings › Network › About</b> '
    'shows its IP address.')


def newer(a, b):
    """Is version a newer than b ('1.0.2' > '1.0.0')?"""
    def t(v):
        return tuple(int(x) if x.isdigit() else 0 for x in (v or '').split('.'))
    return t(a) > t(b)


class _Bus(QObject):
    tvs = Signal(object)          # the addresses found
    state = Signal(str, object)   # ip, {...}


class TvSetupDialog(QDialog):
    def __init__(self, parent, lib, srv, net=roku):
        super().__init__(parent)
        self.lib, self.srv, self.net = lib, srv, net
        self.setWindowTitle('Set up TVs')
        self.resize(720, 440)
        self.tv = {}                                   # ip -> {'status', 'info', 'app', 'msg'}
        self.searching = False
        self.bus = _Bus(self)
        self.bus.tvs.connect(self._found)
        self.bus.state.connect(self._set)

        v = QVBoxLayout(self)
        intro = QLabel('PLAY looks for Roku TVs on your network and puts the PLAY app on them. '
                       'Run this again any time to add a TV or update the TV app.')
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(['TV', 'Address', 'Status'])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        h = self.list.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.list.currentItemChanged.connect(lambda *_: self._details())
        v.addWidget(self.list, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.RichText)
        self.detail.setMinimumHeight(120)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(self.detail)
        b = QHBoxLayout()
        self.again = QPushButton('Search again')
        self.again.clicked.connect(self.search)
        self.add = QPushButton('Add by address…')
        self.add.clicked.connect(self.add_by_address)
        self.go = QPushButton('Install on this TV')
        self.go.setDefault(True)
        self.go.clicked.connect(self.install_current)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        for w in (self.again, self.add):
            b.addWidget(w)
        b.addStretch()
        b.addWidget(self.go)
        b.addWidget(close)
        v.addLayout(b)
        self.search()

    # ------------------------------------------------------------------ finding and checking
    def _run(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def search(self):
        if self.searching:
            return
        self.searching = True
        self.again.setEnabled(False)
        self.detail.setText('Looking for Roku TVs…')
        known = list(self.srv.tvs()) + [ip for ip in self.tv if self.tv[ip]['status'] != OFFLINE]
        self._run(self._search_worker, server.lan_ips(), known)

    def _search_worker(self, local, known):
        try:
            found = self.net.find(local, known)
        except OSError:
            found = list(known)
        self.bus.tvs.emit(found)

    def _found(self, ips):
        self.searching = False
        self.again.setEnabled(True)
        for ip in ips:
            self._check(ip)
        self._details()

    def _check(self, ip):
        self.tv[ip] = {'status': CHECKING, 'info': self.tv.get(ip, {}).get('info'), 'app': None, 'msg': ''}
        self._row(ip)
        self._run(self._check_worker, ip, ip in self.srv.tvs())

    def _check_worker(self, ip, talks_to_play):
        st = {'info': None, 'app': None, 'msg': ''}
        try:
            st['info'] = self.net.info(ip)
        except roku.Blocked:
            st['status'] = BLOCKED
        except Exception:                               # nothing there, or not a Roku
            st['status'] = OFFLINE
        else:
            if not st['info']['dev_mode']:
                st['status'] = NO_DEV
            else:
                app = self.net.dev_app(ip)
                st['app'] = app
                if app and app[0] == roku.APP_TITLE:
                    st['status'] = OLDER if newer(roku.app_version(), app[1]) else INSTALLED
                elif app and app[0]:
                    st['status'] = OTHER
                elif app is None and talks_to_play:     # the TV won't list its apps, but PLAY's app is on it
                    st['status'] = INSTALLED
                else:
                    st['status'] = READY
        self.bus.state.emit(ip, st)

    def _set(self, ip, st):
        if 'pw' in st or 'wrong' in st:
            _after_install(self, ip, st)
            st = {k: v for k, v in st.items() if k not in ('pw', 'wrong')}
        cur = self.tv.setdefault(ip, {'status': CHECKING, 'info': None, 'app': None, 'msg': ''})
        cur.update(st)
        self._row(ip)
        self._details()

    # ------------------------------------------------------------------ the list and the details
    def _item(self, ip):
        for i in range(self.list.topLevelItemCount()):
            it = self.list.topLevelItem(i)
            if it.data(0, IP) == ip:
                return it
        it = QTreeWidgetItem(self.list)
        it.setData(0, IP, ip)
        if self.list.currentItem() is None:
            self.list.setCurrentItem(it)
        return it

    def _row(self, ip):
        t = self.tv[ip]
        it = self._item(ip)
        info = t.get('info') or {}
        it.setText(0, info.get('name') or 'Roku')
        it.setText(1, ip)
        it.setText(2, STATUS[t['status']])

    def current_ip(self):
        it = self.list.currentItem()
        return it.data(0, IP) if it else None

    def _details(self):
        ip = self.current_ip()
        if not ip:
            self.go.setEnabled(False)
            if not self.searching:
                self.detail.setText(NONE_FOUND)
            return
        t = self.tv[ip]
        s = t['status']
        app = t.get('app') or ('', '')
        name = html.escape((t.get('info') or {}).get('name') or ip)
        texts = {
            CHECKING: f'Checking {name}…',
            OFFLINE: f'<b>{name} isn\'t answering.</b> Check that it\'s switched on and on this network.',
            BLOCKED: BLOCKED_STEPS,
            NO_DEV: DEV_STEPS,
            READY: f'<b>{name} is ready.</b> Click <b>Install on this TV</b>. PLAY opens on the TV when it\'s done.',
            OTHER: f'<b>{name} has another developer app ({html.escape(app[0])}).</b> A Roku holds one at a time: '
                   'installing PLAY replaces it.',
            INSTALLED: f'<b>PLAY is on {name}</b>{" (version " + html.escape(app[1]) + ")" if app[1] else ""}. '
                       'Nothing to do. You can reinstall it if the TV app misbehaves.',
            OLDER: f'<b>{name} has an older PLAY ({html.escape(app[1])}).</b> Click <b>Update this TV</b> to put '
                   f'version {html.escape(roku.app_version())} on it.',
            INSTALLING: f'Installing PLAY on {name}… (about 10 seconds)',
            DONE: f'<b>Done.</b> PLAY is opening on {name}. It finds this PC by itself.',
            FAILED: f'<b>That didn\'t work:</b> {html.escape(t.get("msg") or "")}<br>Check the TV is on, '
                    'Developer Mode is on, and try again.',
        }
        self.detail.setText(texts[s])
        labels = {INSTALLED: 'Reinstall', OLDER: 'Update this TV', DONE: 'Reinstall'}
        self.go.setText(labels.get(s, 'Install on this TV'))
        self.go.setEnabled(s in (READY, OTHER, INSTALLED, OLDER, BLOCKED, DONE, FAILED))

    # ------------------------------------------------------------------ adding and installing
    def add_by_address(self):
        text, ok = QInputDialog.getText(self, 'Add a TV', 'The TV\'s IP address (Settings › Network › About):')
        text = (text or '').strip()
        if not ok or not text:
            return
        try:
            ip = str(ipaddress.IPv4Address(text))
        except ValueError:
            QMessageBox.warning(self, 'Set up TVs', f'"{text}" isn\'t an IP address, like 192.168.1.50.')
            return
        self._check(ip)
        self.list.setCurrentItem(self._item(ip))

    def _key(self, ip):
        return (self.tv[ip].get('info') or {}).get('serial') or ip

    def _password(self, ip, retry=False):
        stored = self.lib.data['tv_passwords'].get(self._key(ip))
        if stored and not retry:
            pw = self.net.unprotect(stored)
            if pw:
                return pw
        prompt = ('That password didn\'t work. ' if retry else '') + \
            'The developer password you set on this TV when you turned on Developer Mode:'
        pw, ok = QInputDialog.getText(self, 'Developer password', prompt, QLineEdit.Password)
        return pw if ok and pw else None

    def install_current(self):
        ip = self.current_ip()
        if not ip:
            return
        t = self.tv[ip]
        app = t.get('app')
        if t['status'] == OTHER or (app is None and t['status'] in (BLOCKED, READY)):
            other = f'"{app[0]}"' if app and app[0] else 'any other developer app'
            if QMessageBox.question(self, 'Set up TVs', f'A Roku holds one developer app at a time. Installing PLAY '
                                    f'replaces {other} on this TV. Go ahead?') != QMessageBox.Yes:
                return
        pw = self._password(ip)
        if pw is None:
            return
        self._start_install(ip, pw)

    def _start_install(self, ip, pw):
        self.tv[ip].update(status=INSTALLING, msg='')
        self._row(ip)
        self._details()
        self._run(self._install_worker, ip, pw)

    def _install_worker(self, ip, pw):
        st = {}
        try:
            st['msg'] = self.net.install(ip, pw, self.net.package())
            st['status'] = DONE
            st['pw'] = pw
        except roku.WrongPassword:
            st['status'], st['wrong'] = FAILED, True
            st['msg'] = 'The TV didn\'t accept that password.'
        except Exception as e:
            st['status'], st['msg'] = FAILED, str(e) or type(e).__name__
        self.bus.state.emit(ip, st)


def _after_install(dlg, ip, st):
    """What an install result means for the stored password (called on the GUI thread)."""
    key = dlg._key(ip)
    pws = dict(dlg.lib.data['tv_passwords'])
    if st.get('pw'):
        try:
            pws[key] = dlg.net.protect(st.pop('pw'))
        except OSError:
            pass
    elif st.get('wrong'):
        pws.pop(key, None)
    if pws != dlg.lib.data['tv_passwords']:
        try:
            dlg.lib.set_value('tv_passwords', pws)
        except OSError:
            pass
