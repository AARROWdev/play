"""The library window, offscreen. Qt-synthetic only: no real mouse, keyboard, registry or settings folder."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from play import app as pa, library, server, skins  # noqa: E402
from test_library import make  # noqa: E402


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(pa, 'set_autostart', lambda on: None)
    monkeypatch.setattr(pa, 'get_autostart', lambda: False)
    home, kids = tmp_path / 'home', tmp_path / 'kids'
    for rel in ['Jungle Park 1993.mp4', 'Jungle World 2015.mp4', 'Jungle Park III 2001.mp4',
                'Conor 1982.mkv', 'Ann Droid S01E01.mkv', 'Ann Droid S01E02.mkv', 'Clip.avi']:
        make(str(home), rel)
    make(str(kids), 'Toy Shelf.mp4')
    lib = library.Library(str(tmp_path / 'store'))
    lib.set_folders([{'path': str(home)}, {'path': str(kids), 'kids': True}])
    srv = server.PlayServer(lib, port=0, discovery_port=0)
    srv.start()
    lib.scan()
    w = pa.Window(lib, srv, pa.Scanner(lib))
    w.timer.stop()
    yield w
    srv.stop()
    w.deleteLater()


def titles(w):
    out = []

    def walk(parent):
        for r in range(w.model.rowCount(parent)):
            idx = w.model.index(r, 0, parent)
            if idx.data(pa.KIND) == 'item':
                out.append(w.lib.by_id(idx.data(pa.ID))['title'])
            walk(idx)
    walk(w.model.index(-1, -1).parent())
    return out


def pick(w, *names):
    ids = {i['id'] for i in w.lib.items(3600) if i['title'] in names}
    w.view.clearSelection()
    w._reselect(ids)
    w._sel_changed()


def set_filter(w, key):
    w.rail.setCurrentRow([k for k, _ in pa.FILTERS].index(key))


def test_lists_everything_and_counts(win):
    assert len(titles(win)) == 8
    labels = [win.rail.item(r).text() for r in range(win.rail.count())]
    assert labels[0] == 'Everything   8' and 'Kids   1' in labels and 'Needs a look   1' in labels


def test_filters(win):
    set_filter(win, 'shows')
    assert titles(win) == ['Ann Droid S01E01', 'Ann Droid S01E02']
    set_filter(win, 'kids')
    assert titles(win) == ['Toy Shelf']
    set_filter(win, 'attention')
    assert titles(win) == ['Clip']


def test_search(win):
    win.search.setText('jungle w')
    assert titles(win) == ['Jungle World']


def test_kids_and_hide_buttons_apply_to_selection(win):
    pick(win, 'Conor', 'Clip')
    win.d_kids.click()
    assert all(i['kids'] for i in win.lib.items(3600) if i['title'] in ('Conor', 'Clip'))
    pick(win, 'Conor')
    win.d_hide.click()
    assert win.lib.by_id(win.sel[0]['id'])['hidden']
    assert 'Conor' not in [m['title'] for m in win.lib.tv_view()['movies']]


def test_title_edit(win):
    pick(win, 'Conor')
    win.d_title.setText('Conor the Barbarian')
    win.d_title.editingFinished.emit()
    assert 'Conor the Barbarian' in titles(win)


def test_suggestion_banner_makes_collection(win, monkeypatch):
    monkeypatch.setattr(pa.CollectionDialog, 'exec', lambda self: QDialog.Accepted)
    assert not win.banner.isHidden() and 'Jungle Park' in win.banner_text.text()
    win._make_suggested()
    (cid, c), = win.lib.data['collections'].items()
    assert c['name'] == 'Jungle Park' and len(c['members']) == 3
    assert win.banner.isHidden()
    set_filter(win, 'collections')
    assert titles(win) == ['Jungle Park', 'Jungle Park III', 'Jungle World']


def test_not_now_hides_the_suggestion(win):
    win._dismiss()
    assert win.banner.isHidden()


def test_collection_dialog_custom_order(win):
    ids = [i['id'] for i in win.lib.items(3600) if i['title'].startswith('Jungle')]
    cid = win.lib.make_collection('JP', ids)
    d = pa.CollectionDialog(win, win.lib, cid)
    assert [d.list.item(i).text() for i in range(3)] == ['Jungle Park   1993', 'Jungle Park III   2001',
                                                          'Jungle World   2015']
    d.custom.setChecked(True)
    it = d.list.takeItem(2)
    d.list.insertItem(0, it)
    d.name.setText('Dinosaurs')
    d._save()
    assert [m['title'] for m in win.lib.collection_members(cid)] == ['Jungle World', 'Jungle Park',
                                                                     'Jungle Park III']
    assert win.lib.data['collections'][cid]['name'] == 'Dinosaurs'


def test_collection_combo_adds_and_removes(win):
    ids = [i['id'] for i in win.lib.items(3600) if i['title'] == 'Jungle Park']
    cid = win.lib.make_collection('JP', ids)
    win.refresh()
    pick(win, 'Jungle World')
    win.d_col.setCurrentIndex(win.d_col.findData(cid))
    win._col_picked(0)
    assert len(win.lib.data['collections'][cid]['members']) == 2
    pick(win, 'Jungle World')
    win.d_col.setCurrentIndex(win.d_col.findData(None))
    win._col_picked(0)
    assert win.lib.data['collections'][cid]['members'] == ids


def test_make_a_movie_an_episode(win):
    pick(win, 'Conor')
    win.d_show.click()
    assert win.ep_box.isVisibleTo(win)
    win.d_showname.setText('Conor Show')
    win.d_season.setValue(2)
    win.d_ep.setValue(5)
    win._apply_episode()
    it = next(i for i in win.lib.items(3600) if i.get('show') == 'Conor Show')
    assert (it['kind'], it['season'], it['episode'], it['title']) == ('episode', 2, 5, 'Conor Show S02E05')


def test_status_shows_port_and_tvs(win):
    win._status()
    assert f'port {win.srv.port}' in win.status.text() and 'waiting for a TV' in win.status.text()
    win.srv.clients['10.0.0.9'] = __import__('time').monotonic()
    win._status()
    assert '1 TV connected' in win.status.text()


def test_settings_change_port_and_skin(win, monkeypatch, tmp_path):
    def fake_exec(d):
        d.port.setValue(0 or 18765)
        d.skin.setCurrentText('Ember')
        return QDialog.Accepted
    monkeypatch.setattr(pa.SettingsDialog, 'exec', fake_exec)
    win.open_settings()
    assert win.lib.data['skin'] == 'Ember' and win.srv.port == 18765 and win.srv.running
    assert skins.get('Ember')['accent'] in QApplication.instance().styleSheet()
    assert library.Library(win.lib.dir).data['port'] == 18765


def test_every_skin_applies(qapp):
    for name in skins.SKINS:
        t = pa.apply_skin(qapp, name)
        assert t['accent'] in qapp.styleSheet()
    pa.apply_skin(qapp, 'Blue')


def test_unknown_skin_falls_back(qapp):
    assert pa.apply_skin(qapp, 'Plaid')['name'] == 'Blue'


def test_close_hides_to_tray(win):
    class Tray:
        def showMessage(self, *a):
            self.msg = a
    win.tray = Tray()
    win.show()
    win.close()
    if pa.QSystemTrayIcon.isSystemTrayAvailable():
        assert win.isHidden() and win.tray.msg


def test_selection_handler_runs_once_per_change_after_many_refreshes(win, monkeypatch):
    for _ in range(5):
        win.refresh()
    calls = []
    real = win._detail_for
    monkeypatch.setattr(win, '_detail_for', lambda its: calls.append(1) or real(its))
    idx = win.model.index(0, 0)
    win.view.selectionModel().select(idx, pa.QItemSelectionModel.SelectionFlag.ClearAndSelect
                                     | pa.QItemSelectionModel.SelectionFlag.Rows)
    assert len(calls) == 1


# ------------------------------------------------------------------ review fixes (2c7ec7f)
def test_background_scans_dont_rebuild_or_wipe_typing(win, tmp_path):
    from PySide6.QtTest import QTest
    for n in range(60):
        make(str(tmp_path / 'home'), 'Movie %02d.mkv' % n)
    win.lib.scan()
    win.refresh()
    win.resize(900, 400)
    pick(win, 'Movie 40')
    win.view.verticalScrollBar().setValue(win.view.verticalScrollBar().maximum())
    pos = win.view.verticalScrollBar().value()
    QTest.keyClicks(win.d_title, ' typed')
    v = win.lib.version
    rebuilt = []
    real_refresh = win.refresh
    win.refresh = lambda: rebuilt.append(1) or real_refresh()
    for _ in range(3):
        win.scanner._run()                     # what the 1.5 s timer does, synchronously
        win._changed()
    assert win.lib.version == v and rebuilt == []
    assert win.d_title.text() == 'Movie 40 typed'
    win.refresh()                              # even a real rebuild keeps the scroll and the typing
    assert win.view.verticalScrollBar().value() == pos and win.d_title.text() == 'Movie 40 typed'


def test_a_real_change_still_refreshes(win, tmp_path):
    v = win.lib.version
    make(str(tmp_path / 'home'), 'Brand New 2020.mkv')
    win.scanner._run()
    assert win.lib.version == v + 1


def test_multi_select_edit_scans_once(win, monkeypatch):
    pick(win, 'Conor', 'Clip', 'Jungle World')
    calls = []
    real = win.lib.scan
    monkeypatch.setattr(win.lib, 'scan', lambda: calls.append(1) or real())
    win.d_kids.click()
    assert len(calls) == 1


def test_busy_port_keeps_the_working_server(win, monkeypatch):
    import socket
    blocker = socket.socket()
    blocker.bind(('0.0.0.0', 0))
    blocker.listen()
    busy = blocker.getsockname()[1]
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: None)
    old = win.srv.port
    saved = win.lib.data['port']
    assert win.restart_server(busy) is False
    blocker.close()
    assert win.srv.running and win.srv.port == old and win.lib.data['port'] == saved


def test_season_zero_specials(win):
    assert win.d_season.minimum() == 0


def test_no_tray_closing_quits(win, monkeypatch):
    quit_called = []
    monkeypatch.setattr(pa.QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: False))
    monkeypatch.setattr(QApplication.instance(), 'quit', lambda: quit_called.append(1))
    win.show()
    win.close()
    assert quit_called == [1]


def test_autostart_only_counts_this_install(monkeypatch):
    import winreg
    vals = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(winreg, 'OpenKey', lambda *a: Key())
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda k, n: (vals[n], 1) if n in vals else (_ for _ in ()).throw(OSError()))
    assert pa.get_autostart() is False
    vals['PLAY'] = pa.launch_command()
    assert pa.get_autostart() is True
    vals['PLAY'] = r'"C:\Old\PLAY.exe" --tray'
    assert pa.get_autostart() is False


def test_folder_suggestion_banner_names_the_folder_not_the_path(win, tmp_path):
    for rel in ['The Thin Man (1934).mkv', 'After the Thin Man (1936).mkv']:
        make(str(tmp_path / 'home' / 'The Thin Man'), rel)
    win.lib.dismiss('jungle')
    win.lib.scan()
    win.refresh()
    t = win.banner_text.text()
    assert 'the folder "The Thin Man"' in t and str(tmp_path) not in t and 'Folder:' not in t
