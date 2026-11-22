"""The library window, offscreen. Qt-synthetic only: no real mouse, keyboard, registry or settings folder."""
import os
import socket

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402
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
    assert win.search_timer.isActive()          # typing waits for a pause before redrawing
    win.search_timer.timeout.emit()
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
    t = win.status.toolTip()                       # the full text (the bar itself may be shortened)
    assert f':{win.srv.port}' in t or f'port {win.srv.port}' in t
    assert 'waiting for a TV' in t
    win.srv.seen('10.0.0.9')
    win._status()
    assert '1 TV connected' in win.status.toolTip()


def free_port():
    sock = socket.socket()
    sock.bind(('0.0.0.0', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_settings_change_port_and_skin(win, monkeypatch, tmp_path):
    port = free_port()
    warned = []
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: warned.append(a))   # never a modal in a test

    def fake_exec(d):
        d.port.setValue(port)
        d.skin.setCurrentText('Ember')
        return QDialog.Accepted
    monkeypatch.setattr(pa.SettingsDialog, 'exec', fake_exec)
    win.open_settings()
    assert warned == []
    assert win.lib.data['skin'] == 'Ember' and win.srv.port == port and win.srv.running
    assert skins.get('Ember')['accent'] in QApplication.instance().styleSheet()
    assert library.Library(win.lib.dir).data['port'] == port


def test_every_skin_applies(qapp):
    for name in skins.SKINS:
        t = pa.apply_skin(qapp, name)
        assert t['accent'] in qapp.styleSheet()
    pa.apply_skin(qapp, 'Blue')


def test_unknown_skin_falls_back(qapp):
    assert pa.apply_skin(qapp, 'Plaid')['name'] == 'Blue'


def test_close_hides_to_tray(win, monkeypatch):
    class Tray:
        msg = None

        def showMessage(self, *a):
            self.msg = a
    quit_called = []
    monkeypatch.setattr(pa.QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: True))
    monkeypatch.setattr(QApplication.instance(), 'quit', lambda: quit_called.append(1))
    win.tray = Tray()
    win.show()
    win.close()
    assert win.isHidden() and win.tray.msg and quit_called == []      # still running: the TVs keep playing


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
    win._changed()                                 # the queued signal, delivered
    assert win.lib.version == v + 1 and 'Brand New' in titles(win)


def test_multi_select_edit_never_walks_the_disk(win, monkeypatch):
    pick(win, 'Conor', 'Clip', 'Jungle World')
    calls = []
    real = win.lib.scan
    monkeypatch.setattr(win.lib, 'scan', lambda *a, **k: calls.append(1) or real(*a, **k))
    win.d_kids.click()
    assert calls == []                         # an edit rebuilds from memory: no disk walk at all
    assert all(i['kids'] for i in win.lib.current() if i['title'] in ('Conor', 'Clip', 'Jungle World'))


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
    pick(win, 'Ann Droid S01E01')
    win.d_season.setValue(0)
    win.d_ep.setValue(0)
    win._apply_episode()
    it = next(i for i in win.lib.current() if i.get('show') == 'Ann Droid' and i['season'] == 0)
    assert it['episode'] == 0 and it['title'] == 'Ann Droid S00E00'


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


def test_plays_on_roku_column_is_about_the_format_only(win, tmp_path):
    make(str(tmp_path / 'home'), 'Ann Droid S01E04.mkv')        # leaves a gap: episode 3 is missing
    win.lib.scan()
    win.refresh()
    rows = {}
    for r in range(win.model.rowCount()):
        rows[win.model.item(r, 0).text()] = (win.model.item(r, 6).text(), win.model.item(r, 6).toolTip())
    assert rows['Ann Droid S01E04'][0] == 'Yes' and 'missing episode 3' in rows['Ann Droid S01E04'][1]
    assert rows['Clip'][0] == 'Check'



# ------------------------------------------------------------------ baseline fixes, window (B1, B9, B15-B20, B22, B39, B49, B57-B59)
def test_collection_menu_survives_a_redraw_while_open(win, monkeypatch):
    ids = [i['id'] for i in win.lib.current() if i['title'].startswith('Jungle')]
    cid = win.lib.make_collection('JP', ids)
    set_filter(win, 'collections')
    opened = []
    monkeypatch.setattr(win, '_edit_collection', lambda c: opened.append(c))

    class Menu(pa.QMenu):
        def exec(self, *a):
            win.refresh()                              # a background change while the menu is open
            self.actions()[0].trigger()
    monkeypatch.setattr(pa, 'QMenu', Menu)
    pos = win.view.visualRect(win.model.index(0, 0)).center()
    win._menu(pos)
    assert opened == [cid]


def test_reselect_is_one_selection(win, tmp_path, monkeypatch):
    for n in range(400):
        make(str(tmp_path / 'home'), 'Movie %03d.mkv' % n)
    win.lib.scan()
    win.refresh()
    win.view.selectAll()
    calls = []
    real = win._detail_for
    monkeypatch.setattr(win, '_detail_for', lambda its: calls.append(len(its)) or real(its))
    selects = []
    sm = win.view.selectionModel()
    real_select = sm.select
    monkeypatch.setattr(sm, 'select', lambda *a: selects.append(1) or real_select(*a))
    win.refresh()
    # one detail update and one select() call for the whole redraw (the old way: one per row, each signalling)
    assert calls == [len(win.lib.current())] and len(selects) == 1
    assert len(win.selected_ids()) == len(win.lib.current())


def test_set_episode_keeps_a_typed_title_and_leaves_no_title_behind(win):
    pick(win, 'Conor')
    win.d_show.click()
    win.d_showname.setText('Conor Show')
    win.d_season.setValue(2)
    win.d_ep.setValue(5)
    win._apply_episode()
    it = next(i for i in win.lib.current() if i.get('show') == 'Conor Show')
    assert it['title'] == 'Conor Show S02E05' and 'title' not in win.lib.data['items'].get(it['id'], {})
    pick(win, 'Conor Show S02E05')
    win.d_movie.click()
    assert win.lib.by_id(it['id'])['title'] == 'Conor'


def test_title_box_follows_again_after_enter(win):
    pick(win, 'Conor')
    win.d_title.setText('Conor the Barbarian')
    win.d_title.setModified(True)
    win.d_title.editingFinished.emit()
    assert not win.d_title.isModified()
    fp = win.sel[0]['id']
    win.lib.set_item(fp, title='Other Name')
    win.refresh()
    assert win.d_title.text() == 'Other Name'


def test_episode_edit_in_progress_survives_a_redraw(win, tmp_path):
    from PySide6.QtTest import QTest
    pick(win, 'Ann Droid S01E01')
    win.d_showname.clear()
    QTest.keyClicks(win.d_showname, 'Android')
    make(str(tmp_path / 'home'), 'Something New 2021.mkv')
    win.lib.scan()
    win.refresh()
    assert win.d_showname.text() == 'Android' and win.ep_box.isVisibleTo(win)


def test_shows_view_ignores_capital_letters(win, tmp_path):
    make(str(tmp_path / 'home'), 'ann droid S01E03.mkv')
    win.lib.scan()
    set_filter(win, 'shows')
    assert win.model.rowCount() == 1 and '3 episodes' in win.model.item(0, 0).text()


def test_first_draw_never_scans_on_the_gui_thread(qapp, tmp_path, monkeypatch):
    make(str(tmp_path / 'home'), 'Heat 1995.mkv')
    lib = library.Library(str(tmp_path / 'store'))
    lib.set_folders([{'path': str(tmp_path / 'home')}])
    calls = []
    monkeypatch.setattr(lib, 'scan', lambda *a, **k: calls.append(1))
    srv = server.PlayServer(lib, port=0, discovery_port=0)
    w = pa.Window(lib, srv, pa.Scanner(lib))
    w.timer.stop()
    assert calls == [] and 'reading your folders' in w.status.toolTip()
    w.deleteLater()


def test_mixed_selection_lights_neither_kind(win):
    pick(win, 'Conor', 'Ann Droid S01E01')
    assert not win.d_movie.isChecked() and not win.d_show.isChecked()


def test_dialogs_and_menus_are_deleted(win, monkeypatch):
    from PySide6.QtWidgets import QDialog as D
    ids = [i['id'] for i in win.lib.current() if i['title'].startswith('Jungle')]
    cid = win.lib.make_collection('JP', ids)
    monkeypatch.setattr(pa.CollectionDialog, 'exec', lambda self: D.Rejected)
    before = len(win.findChildren(pa.CollectionDialog))
    for _ in range(5):
        win._edit_collection(cid)
    qapp = QApplication.instance()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(win.findChildren(pa.CollectionDialog)) == before


def test_banner_uses_the_word_as_written(win):
    assert 'start with "Jungle"' in win.banner_text.text()


def test_plural():
    assert pa.plural(1, 'film') == '1 film' and pa.plural(2, 'film') == '2 films' and pa.plural(0, 'TV') == '0 TVs'


def test_tray_icon_follows_the_skin(win, monkeypatch):
    class Tray:
        icon = None

        def setIcon(self, i):
            self.icon = i
    win.tray = Tray()
    port = win.srv.port

    def fake_exec(d):
        d.port.setValue(port)
        d.skin.setCurrentText('Neon')
        return QDialog.Accepted
    monkeypatch.setattr(pa.SettingsDialog, 'exec', fake_exec)
    win.open_settings()
    assert win.tray.icon is not None


def test_status_shows_discovery_and_unreachable_problems(win):
    win.srv.discovery_error = 'UDP port 8601 is in use'
    win.lib.unreachable = [r'N:\Media']
    win._status()
    t = win.status.toolTip()
    assert 'UDP port 8601' in t and "Can't reach" in t



# ------------------------------------------------------------------ second review (122cbc0 / d089fe2)
def test_scanner_tells_the_window(win, tmp_path):
    """A background scan that finds something new redraws the window (through the Scanner's signal)."""
    from PySide6.QtCore import QCoreApplication
    make(str(tmp_path / 'home'), 'Arrived Later 2021.mkv')
    win.scanner._run()
    QCoreApplication.processEvents()
    assert 'Arrived Later' in titles(win)


def test_set_episode_keeps_a_title_you_typed(win):
    pick(win, 'Ann Droid S01E01')
    win.d_title.setText('Pilot')
    win.d_title.setModified(True)
    win.d_title.editingFinished.emit()
    pick(win, 'Pilot')
    win.d_ep.setValue(9)
    win._apply_episode()
    it = next(i for i in win.lib.current() if i.get('episode') == 9)
    assert it['title'] == 'Pilot'


def test_kind_buttons_light_by_selection(win):
    pick(win, 'Conor')
    assert win.d_movie.isChecked() and not win.d_show.isChecked()
    pick(win, 'Ann Droid S01E01')
    assert win.d_show.isChecked() and not win.d_movie.isChecked()
    pick(win, 'Conor', 'Ann Droid S01E01')
    assert not win.d_movie.isChecked() and not win.d_show.isChecked()


def test_banner_label_is_not_the_lowercase_key(win):
    t = win.banner_text.text()
    assert '"Jungle"' in t and '"jungle"' not in t


def test_icon_follows_the_skin_and_close_notice_uses_it(win, monkeypatch):
    seen = {}
    monkeypatch.setattr(pa, 'play_icon', lambda accent=None, size=256: seen.setdefault('accent', accent))
    win.lib.set_value('skin', 'Crimson')
    win.icon()
    assert seen['accent'] == skins.get('Crimson')['accent']


def test_specials_label_in_rows_and_tree(win, tmp_path):
    make(str(tmp_path / 'home' / 'Ann Droid' / 'Specials'), 'Ann Droid S00E01.mkv')
    win.lib.scan()
    set_filter(win, 'shows')
    top = win.model.item(0, 0)
    labels = [top.child(r, 0).text() for r in range(top.rowCount())]
    assert 'Specials' in labels
    set_filter(win, 'all')
    row = next(r for r in range(win.model.rowCount()) if win.model.item(r, 0).text().startswith('Ann Droid S00E01'))
    assert 'Specials' in win.model.item(row, 2).text()


def test_edit_collection_rechecks_it_exists(win, monkeypatch):
    opened = []
    monkeypatch.setattr(pa.CollectionDialog, 'exec', lambda self: opened.append(1) or QDialog.Rejected)
    win._edit_collection('gone1234')
    assert opened == []


def test_menu_and_settings_dialog_are_deleted(win, monkeypatch):
    deleted = []
    real = pa.run_dialog
    monkeypatch.setattr(pa, 'run_dialog', lambda d, *a: deleted.append(type(d).__name__) or
                        (d.deleteLater() or 0))
    pick(win, 'Conor')
    win._menu(win.view.visualRect(win.model.index(0, 0)).center())
    assert 'QMenu' in deleted
    destroyed = []
    monkeypatch.setattr(pa.SettingsDialog, 'exec', lambda self: (self.destroyed.connect(lambda: destroyed.append(1)),
                                                                 QDialog.Rejected)[1])
    win.open_settings()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert destroyed == [1]


def test_scan_cadence(win, monkeypatch):
    kicks = []
    monkeypatch.setattr(win.scanner, 'kick', lambda: kicks.append(1))
    monkeypatch.setattr(win, 'isVisible', lambda: True)
    monkeypatch.setattr(win, 'isMinimized', lambda: False)
    assert win.scan_every() == 6
    monkeypatch.setattr(win, 'isMinimized', lambda: True)
    assert win.scan_every() == 30
    win.lib._scanned = __import__('time').monotonic()
    win._tick()
    assert kicks == []                         # just scanned: no walk
    win.lib._scanned -= 31
    win._tick()
    assert kicks == [1]


def test_not_ready_guards(qapp, tmp_path, monkeypatch):
    """Before the first scan, selecting and the Collections view never call into the library's views."""
    make(str(tmp_path / 'home'), 'Heat 1995.mkv')
    lib = library.Library(str(tmp_path / 'store'))
    lib.set_folders([{'path': str(tmp_path / 'home')}])
    for name in ('scan', 'collection_members', 'suggestions', 'by_id'):
        monkeypatch.setattr(lib, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)))
    srv = server.PlayServer(lib, port=0, discovery_port=0)
    w = pa.Window(lib, srv, pa.Scanner(lib))
    w.timer.stop()
    set_filter(w, 'collections')
    w._sel_changed()
    w.deleteLater()


def test_restart_button_uses_the_typed_port(win, monkeypatch):
    got = []
    monkeypatch.setattr(win, 'restart_server', lambda port=None: got.append(port))
    d = pa.SettingsDialog(win, win.lib)
    d.port.setValue(18999)
    btn = next(b for b in d.findChildren(pa.QPushButton) if b.text() == 'Restart server')
    btn.click()
    d.deleteLater()
    assert got == [18999]


def test_autostart_written_only_when_changed(win, monkeypatch):
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: None)   # never a modal in a test
    win.lib.data['port'] = win.srv.port                                # Settings shows the running port
    writes = []
    monkeypatch.setattr(pa, 'set_autostart', lambda on: writes.append(on))
    monkeypatch.setattr(pa.SettingsDialog, 'exec', lambda self: QDialog.Accepted)
    win.open_settings()
    assert writes == []

    def tick(d):
        d.auto.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(pa.SettingsDialog, 'exec', tick)
    win.open_settings()
    assert writes == [True]


def test_other_session_notice_is_silent_at_sign_in(monkeypatch):
    shown = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: shown.append(a))
    assert pa.other_session_notice(['PLAY.exe', '--tray'], session=1) is False
    assert pa.other_session_notice(['PLAY.exe'], session=0) is False
    assert shown == []
    assert pa.other_session_notice(['PLAY.exe'], session=1) is True and len(shown) == 1


def test_hand_over_reaches_the_listener(qapp, monkeypatch):
    """A second launch (another process) hands over: the listener answers "ok" and shows the window."""
    import subprocess
    import sys
    import time
    from PySide6.QtCore import QCoreApplication
    key = 'PLAY-test-' + __import__('uuid').uuid4().hex
    monkeypatch.setattr(pa, 'INSTANCE_KEY', key)
    shown = []
    listener = pa.start_listener(lambda: shown.append(1))
    code = ('import os, sys; os.environ["QT_QPA_PLATFORM"] = "offscreen"; sys.path[:0] = [".", "tests"]; '
            'from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]); '
            'from play import app; print(app.hand_over(timeout=5))')
    p = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True,
                         env={**os.environ, 'PLAY_INSTANCE_KEY': key})
    end = time.monotonic() + 20
    while p.poll() is None and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    out = p.communicate(timeout=5)[0].strip()
    listener.close()
    assert out == 'True' and shown == [1]


def test_hand_over_gives_up_when_nobody_listens(monkeypatch):
    monkeypatch.setattr(pa, 'INSTANCE_KEY', 'PLAY-test-' + __import__('uuid').uuid4().hex)
    assert pa.hand_over(timeout=0.5) is False
