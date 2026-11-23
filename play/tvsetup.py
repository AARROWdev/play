"""Set up TVs: a small wizard that finds the Roku TVs on the network, says what each one needs, and installs PLAY's
TV app on them. It can be opened any time (a new TV, or the TV app after a PLAY update)."""
import html
import ipaddress
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import roku, server

IP = Qt.UserRole

CHECKING, OFFLINE, BLOCKED, NO_DEV, READY, OTHER, INSTALLED, OLDER, INSTALLING, DONE, FAILED, UNSURE = (
    'checking', 'offline', 'blocked', 'no_dev', 'ready', 'other', 'installed', 'older', 'installing', 'done',
    'failed', 'unsure')

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
    UNSURE: 'No answer from the TV',
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
    idle = Signal()               # a worker finished


def confirm(parent, text):
    """Yes/No, with the text shown as plain text (it can hold what a device on the network says about itself)."""
    box = QMessageBox(QMessageBox.Question, 'Set up TVs', text, QMessageBox.Yes | QMessageBox.No, parent)
    box.setTextFormat(Qt.PlainText)
    box.setDefaultButton(QMessageBox.No)
    try:
        return box.exec() == QMessageBox.Yes
    finally:
        box.deleteLater()


class TvSetupDialog(QDialog):
    def __init__(self, parent, lib, srv, net=roku):
        super().__init__(parent)
        self.lib, self.srv, self.net = lib, srv, net
        self.setWindowTitle('Set up TVs')
        self.resize(720, 440)
        self.tv = {}                                   # ip -> {'status', 'info', 'app', 'msg'}
        self.gen = {}                                  # ip -> the latest check or install started for it
        self.searching = False
        self.active = 0                                # workers still running (the dialog outlives them)
        self.closing = False
        self.bus = _Bus(self)
        self.bus.tvs.connect(self._found)
        self.bus.state.connect(self._set)
        self.bus.idle.connect(self._worker_done)

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
        self.active += 1

        def run():
            try:
                fn(*args)
            finally:
                self.bus.idle.emit()
        threading.Thread(target=run, daemon=True).start()

    def _worker_done(self):
        self.active -= 1

    def release(self):
        """Closed. The window keeps this one dialog and opens it again (an install still running finishes, keeps
        its password, and shows when it's opened again); nothing new is checked meanwhile."""
        self.closing = True

    def reopen(self):
        self.closing = False
        for ip, t in self.tv.items():                  # a wrong password that came in while it was closed
            if t.pop('ask_again', False):
                QTimer.singleShot(0, lambda ip=ip: self._retry(ip))
        self.search()

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
        except Exception:
            found = list(known)
        self.bus.tvs.emit(found)

    def _found(self, ips):
        self.searching = False
        self.again.setEnabled(True)
        if self.closing:
            return
        for ip in ips:
            if self.tv.get(ip, {}).get('status') != INSTALLING:
                self._check(ip)
        self._details()

    def _next(self, ip):
        self.gen[ip] = self.gen.get(ip, 0) + 1
        return self.gen[ip]

    def _check(self, ip):
        self.tv[ip] = {'status': CHECKING, 'info': self.tv.get(ip, {}).get('info'), 'app': None, 'msg': ''}
        self._row(ip)
        self._run(self._check_worker, ip, ip in self.srv.tvs(), self._next(ip))

    def _check_worker(self, ip, talks_to_play, gen=0):
        st = {'info': None, 'app': None, 'msg': '', 'gen': gen}
        try:
            st['info'] = self.net.info(ip)
            if not st['info']['dev_mode']:
                st['status'] = NO_DEV
            else:
                app = self.net.dev_app(ip)
                st['app'] = app
                if app and app[0] == roku.APP_TITLE:
                    st['status'] = OLDER if newer(roku.app_version(), app[1]) else INSTALLED
                elif app and app[0]:
                    st['status'] = OTHER
                elif app is None and talks_to_play:     # the TV won't list its apps, but PLAY's app plays on it
                    st['status'] = INSTALLED            # (app stays unknown: installing still asks first)
                else:
                    st['status'] = READY
        except roku.Blocked:
            st['status'] = BLOCKED
        except Exception:                               # nothing there, not a Roku, or a garbled answer
            st['status'] = OFFLINE
        self.bus.state.emit(ip, st)

    def _set(self, ip, st):
        if st.get('gen', self.gen.get(ip, 0)) != self.gen.get(ip, 0):
            return                                      # a check or install that something newer replaced
        wrong = st.get('wrong')                         # the TV refused the password: forget it, ask again
        ask = wrong or st.get('ask_pw')                 # ask again (also for one no TV was asked about)
        if st.get('ask_pw') and st.get('stored'):       # an unusable one that was stored: forget it too
            st = dict(st, wrong=True)
        if 'pw' in st or st.get('wrong'):
            _after_install(self, ip, st)
        st = {k: v for k, v in st.items() if k not in ('pw', 'wrong', 'ask_pw', 'stored', 'install', 'gen')}
        cur = self.tv.setdefault(ip, {'status': CHECKING, 'info': None, 'app': None, 'msg': ''})
        cur.update(st)
        self._row(ip)
        self._details()
        if ask:                                         # ask again, once (when it's open: see _retry)
            QTimer.singleShot(0, lambda: self._retry(ip))

    def _retry(self, ip):
        if self.tv.get(ip, {}).get('status') != FAILED:
            return
        if self.closing:                                # closed just now: ask when it's opened again
            self.tv[ip]['ask_again'] = True
            return
        pw, stored = self._password(ip, retry=True,
                                    why=self.tv[ip].get('msg') if self.tv[ip].get('pw_problem') else None)
        if pw is not None:
            self._start_install(ip, pw, stored)

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
            FAILED: (f'<b>That password can\'t be used:</b> {html.escape(t.get("msg") or "")}<br>Type the '
                     'developer password you set on the TV.') if t.get('pw_problem') else
                    (f'<b>That didn\'t work:</b> {html.escape(t.get("msg") or "")}<br>Check the TV is on, '
                     'Developer Mode is on, and try again.'),
            UNSURE: f'<b>PLAY may have reached {name}, but the TV didn\'t say how it went.</b> Look on the TV: '
                    'if PLAY opened, it\'s installed. If not, click <b>Install again</b>.',
        }
        self.detail.setText(texts[s])
        labels = {INSTALLED: 'Reinstall', OLDER: 'Update this TV', DONE: 'Reinstall', UNSURE: 'Install again'}
        self.go.setText(labels.get(s, 'Install on this TV'))
        self.go.setEnabled(s in (READY, OTHER, INSTALLED, OLDER, BLOCKED, DONE, FAILED, UNSURE))
        self.again.setEnabled(not self.searching)

    # ------------------------------------------------------------------ adding and installing
    def add_by_address(self):
        text, ok = QInputDialog.getText(self, 'Add a TV', 'The TV\'s IP address (Settings › Network › About):')
        text = (text or '').strip()
        if not ok or not text:
            return
        try:
            ip = str(ipaddress.IPv4Address(text))
        except ValueError:
            ip = None
        if not ip or not roku.lan_address(ip):
            QMessageBox.warning(self, 'Set up TVs', f'"{text}" isn\'t a home-network address, like 192.168.1.50.')
            return
        if self.tv.get(ip, {}).get('status') != INSTALLING:
            self._check(ip)
        self.list.setCurrentItem(self._item(ip))

    def _key(self, ip):
        """The stored password's key: the TV's serial and its address together, so a device that copies a serial
        or later gets the address is asked for the password, not handed it. None (nothing stored) when the TV
        didn't say its serial."""
        serial = (self.tv[ip].get('info') or {}).get('serial')
        return f'{serial}@{ip}' if serial and serial != ip else None

    def _password(self, ip, retry=False, why=None):
        """(password, was it the stored one), or (None, False) when the question is cancelled."""
        key = self._key(ip)
        stored = self.lib.data['tv_passwords'].get(key) if key else None
        if stored and not retry:
            pw = self.net.unprotect(stored)
            if pw:
                return pw, True
        prompt = ((why + ' ') if why else 'That password didn\'t work. ' if retry else '') + \
            'The developer password you set on this TV when you turned on Developer Mode:'
        pw, ok = QInputDialog.getText(self, 'Developer password', prompt, QLineEdit.Password)
        return (pw, False) if ok and pw else (None, False)

    def install_current(self):
        ip = self.current_ip()
        if not ip:
            return
        t = self.tv[ip]
        if t['status'] == INSTALLING:
            return
        app = t.get('app')
        if t['status'] == OTHER or app is None:         # another app, or the TV won't say which: ask first
            other = f'"{app[0]}"' if app and app[0] else 'any other developer app'
            if not confirm(self, f'A Roku holds one developer app at a time. Installing PLAY replaces {other} on '
                                 f'this TV. Go ahead?'):
                return
        pw, stored = self._password(ip)
        if pw is None:
            return
        self._start_install(ip, pw, stored)

    def _start_install(self, ip, pw, stored):
        self.tv[ip].update(status=INSTALLING, msg='')
        self._row(ip)
        self._details()
        self._run(self._install_worker, ip, pw, self._next(ip), stored)

    def _install_worker(self, ip, pw, gen, stored):
        st = {'install': True, 'gen': gen, 'pw_problem': False, 'stored': stored}   # (every outcome says whether it's the password)
        try:
            st['msg'] = self.net.install(ip, pw, self.net.package())
            st['status'] = DONE
            st['app'] = (roku.APP_TITLE, roku.app_version())   # what's on the TV now
            st['pw'] = pw
        except roku.TookTooLong as e:
            # the app was sent, with the password, but no answer came: it may or may not be on the TV. Nothing
            # says the password worked, so it isn't kept.
            st['status'], st['msg'] = (UNSURE if e.uploaded else FAILED), str(e)
        except roku.UnusablePassword as e:           # no TV was asked: say why, and ask again (nothing to forget)
            st['status'], st['ask_pw'], st['pw_problem'] = FAILED, True, True
            st['msg'] = str(e)
        except roku.WrongPassword as e:
            st['status'], st['wrong'], st['pw_problem'] = FAILED, True, False
            st['msg'] = str(e) or 'The TV didn\'t accept that password.'
        except Exception as e:
            st['status'], st['msg'] = FAILED, str(e) or type(e).__name__
        self.bus.state.emit(ip, st)


def _after_install(dlg, ip, st):
    """What an install result means for the stored password (called on the GUI thread): a password that worked is
    kept (encrypted; saved only when it's new), a refused one is forgotten. Not kept for a TV that didn't say its
    serial."""
    key = dlg._key(ip)
    if not key:
        return
    pws = dict(dlg.lib.data['tv_passwords'])
    if st.get('pw'):                                # it worked: the same TV's old addresses are forgotten
        serial = key.rsplit('@', 1)[0]
        for k in [k for k in pws if k != key and k.rsplit('@', 1)[0] == serial]:
            del pws[k]
        if not pws.get(key) or dlg.net.unprotect(pws[key]) != st['pw']:
            try:
                pws[key] = dlg.net.protect(st['pw'])
            except OSError:
                pass
    elif st.get('wrong'):
        pws.pop(key, None)
    if pws != dlg.lib.data['tv_passwords']:
        try:
            dlg.lib.set_value('tv_passwords', pws)
        except OSError:
            pass
