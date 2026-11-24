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
    lib.data['port'] = srv.port            # as in the app: the saved port is the one being served
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: None)   # a test never waits on a modal
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
    win.d_title.setModified(True)
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
    ids = [i['id'] for i in win.lib.current() if i['title'].startswith('Jungle')]
    cid = win.lib.make_collection('JP', ids)
    monkeypatch.setattr(pa.CollectionDialog, 'exec', lambda self: QDialog.Rejected)
    before = len(win.findChildren(pa.CollectionDialog))
    for _ in range(5):
        win._edit_collection(cid)
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
    monkeypatch.setattr(win, 'restart_server', lambda port=None, save=True: got.append((port, save)))
    d = pa.SettingsDialog(win, win.lib)
    d.port.setValue(18999)
    btn = next(b for b in d.findChildren(pa.QPushButton) if b.text() == 'Restart server')
    btn.click()
    d.deleteLater()
    assert got == [(18999, False)]                 # tried, not saved: Save or Cancel decides


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



# ------------------------------------------------------------------ b6eeb8b review
def test_close_notice_and_tray_use_the_skin_icon(win, monkeypatch):
    class Tray:
        icon = msg_icon = None

        def setIcon(self, i):
            self.icon = i

        def showMessage(self, title, text, icon, ms):
            self.msg_icon = icon
    made = []
    monkeypatch.setattr(pa, 'play_icon', lambda accent=None, size=256: made.append(accent) or pa.QIcon())
    monkeypatch.setattr(pa.QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: True))
    win.lib.set_value('skin', 'Neon')
    win.tray = Tray()
    win.set_icons()
    assert win.tray.icon is not None and made[-1] == skins.get('Neon')['accent']
    made.clear()
    win.show()
    win.close()
    assert win.tray.msg_icon is not None and made == [skins.get('Neon')['accent']]


def test_settle_instance_starts_when_the_old_copy_is_gone():
    answers = iter([pa.instance.SAME_SESSION, pa.instance.NONE])
    waits = []
    who = pa.settle_instance(check=lambda: next(answers), hand=lambda: False, wait=waits.append)
    assert who == pa.instance.NONE and waits == [1]


def test_settle_instance_hands_over_when_answered():
    who = pa.settle_instance(check=lambda: pa.instance.SAME_SESSION, hand=lambda: True, wait=lambda s: None)
    assert who == pa.instance.SAME_SESSION


def test_autostart_is_offered_once_per_user(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(pa, 'set_autostart', lambda on: writes.append(on))
    lb = library.Library(str(tmp_path / 'a'))
    assert pa.offer_autostart(lb, installed=False) is False and writes == []    # a zip or portable run
    assert pa.offer_autostart(lb, installed=True) is True and writes == [True]   # the installed copy, first start
    assert pa.offer_autostart(lb, installed=True) is False and writes == [True]  # once: switching it off sticks
    assert library.Library(lb.dir).data['autostart_offered'] is True
    ro = library.Library(str(tmp_path / 'b'))
    ro.read_only = True
    assert pa.offer_autostart(ro, installed=True) is False and writes == [True]  # can't record it: leave it


def test_refused_host_is_shown_as_plain_text(win):
    win.srv.refuse('<b>PLAY update required</b>')
    win._status()
    assert win.status.textFormat() == pa.Qt.PlainText
    assert '<b>' not in win.status.toolTip() and '&lt;b&gt;' in win.status.toolTip()


def test_restart_button_then_cancel_goes_back(win, monkeypatch):
    saved = win.srv.port
    trial = free_port()

    def try_then_cancel(d):
        d.port.setValue(trial)
        next(b for b in d.findChildren(pa.QPushButton) if b.text() == 'Restart server').click()
        assert win.srv.port == trial and win.lib.data['port'] == saved      # tried, not saved
        return QDialog.Rejected
    monkeypatch.setattr(pa.SettingsDialog, 'exec', try_then_cancel)
    win.open_settings()
    assert win.srv.port == saved and win.srv.running



# ------------------------------------------------------------------ 7015d01 review
def test_save_after_restart_button_keeps_the_port(win, monkeypatch):
    trial = free_port()

    def try_then_save(d):
        d.port.setValue(trial)
        next(b for b in d.findChildren(pa.QPushButton) if b.text() == 'Restart server').click()
        return QDialog.Accepted
    monkeypatch.setattr(pa.SettingsDialog, 'exec', try_then_save)
    win.open_settings()
    assert win.srv.port == trial and win.lib.data['port'] == trial
    assert library.Library(win.lib.dir).data['port'] == trial


def test_installed_copy_is_where_the_uninstaller_is(tmp_path):
    d = tmp_path / 'Apps' / 'PLAY'
    d.mkdir(parents=True)
    (d / 'PLAY.exe').write_bytes(b'')
    assert not pa.installed_copy(str(d / 'PLAY.exe'))            # portable, or opened from a zip
    (d / 'unins000.exe').write_bytes(b'')
    assert pa.installed_copy(str(d / 'PLAY.exe'))                 # a custom install folder still counts


def test_main_offers_autostart_to_the_installed_copy(qapp, tmp_path, monkeypatch):
    class Stop(Exception):
        pass
    calls = []

    def offer(lib, installed):
        calls.append((lib.dir, installed))
        raise Stop                                   # main got this far: the wiring is what's under test
    monkeypatch.setattr(pa, 'QApplication', lambda argv: qapp)
    monkeypatch.setattr(pa, 'settle_instance', lambda: pa.instance.NONE)
    monkeypatch.setattr(pa, 'start_listener', lambda on_show: None)
    real_lib = library.Library
    monkeypatch.setattr(pa.library, 'Library', lambda *a: real_lib(str(tmp_path)))
    monkeypatch.setattr(pa.server, 'PlayServer', lambda *a, **k: type('S', (), {'start': lambda s: None})())
    monkeypatch.setattr(pa, 'installed_copy', lambda: True)
    monkeypatch.setattr(pa, 'offer_autostart', offer)
    with pytest.raises(Stop):
        pa.main(['PLAY.exe', '--tray'])
    assert calls == [(str(tmp_path), True)]


def test_autostart_isnt_switched_on_when_the_flag_cant_be_saved(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(pa, 'set_autostart', lambda on: writes.append(on))
    lb = library.Library(str(tmp_path))

    def fail(*a):
        raise PermissionError('read-only file')
    monkeypatch.setattr(library, '_write_text', fail)
    assert pa.offer_autostart(lb, installed=True) is False and writes == []
    assert pa.offer_autostart(lb, installed=True) is False and writes == []      # every start: still nothing


def test_main_skips_autostart_for_a_portable_copy(qapp, tmp_path, monkeypatch):
    class Stop(Exception):
        pass
    seen = []
    monkeypatch.setattr(pa, 'QApplication', lambda argv: qapp)
    monkeypatch.setattr(pa, 'settle_instance', lambda: pa.instance.NONE)
    monkeypatch.setattr(pa, 'start_listener', lambda on_show: None)
    real_lib = library.Library
    monkeypatch.setattr(pa.library, 'Library', lambda *a: real_lib(str(tmp_path)))
    monkeypatch.setattr(pa.server, 'PlayServer', lambda *a, **k: type('S', (), {'start': lambda s: None})())
    monkeypatch.setattr(pa, 'installed_copy', lambda: False)
    monkeypatch.setattr(pa, 'offer_autostart', lambda lib, installed: seen.append(installed) or (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        pa.main(['PLAY.exe', '--tray'])
    assert seen == [False]


def test_every_address_is_in_the_tooltip(win, monkeypatch):
    monkeypatch.setattr(server, 'lan_ips', lambda: ['10.8.0.6', '192.168.56.1', '10.0.0.201', '192.168.137.1'])
    win.resize(1600, 700)
    win._status()
    assert '(+2 more)' in win.status.text()
    for ip in ('10.0.0.201', '192.168.137.1'):
        assert ip in win.status.toolTip()


def test_a_failed_run_key_write_offers_autostart_again(tmp_path, monkeypatch):
    def fail(on):
        raise PermissionError('policy')
    monkeypatch.setattr(pa, 'set_autostart', fail)
    lb = library.Library(str(tmp_path))
    assert pa.offer_autostart(lb, installed=True) is False
    assert library.Library(lb.dir).data['autostart_offered'] is False         # offered again next start


def test_missing_folders_are_named_in_the_status(win):
    win.lib.missing = [r'D:\Media\Kids']
    win._status()
    assert r'Not found: D:\Media\Kids. Update it in Settings' in win.status.toolTip()
    win.lib.missing = [r'D:\Media\Kids', r'D:\Media\Films']
    win._status()
    assert 'Update them in Settings' in win.status.toolTip()



def test_set_up_tvs_is_one_dialog_and_opens_again(win, monkeypatch):
    from play import tvsetup
    monkeypatch.setattr(tvsetup.TvSetupDialog, 'search', lambda self: None)
    shown = []

    def exec_(self):
        shown.append(self)
        self.setVisible(True)
        if len(shown) == 1:
            win.open_tv_setup()                     # the tray's Set up TVs while it's open: just brought up
            assert len(shown) == 1 and not self.closing
        self.setVisible(False)
        return 0
    monkeypatch.setattr(tvsetup.TvSetupDialog, 'exec', exec_)
    win.open_tv_setup()
    assert shown[0].closing
    win.open_tv_setup()                             # opened again: the same dialog
    assert len(shown) == 2 and shown[1] is shown[0] and shown[0].closing


# ------------------------------------------------------------------ one default folder, Add media, the panel
def test_a_new_play_starts_with_one_default_folder(win, monkeypatch, tmp_path):
    monkeypatch.setattr(library, 'default_folder_path', lambda: str(tmp_path / 'Videos' / 'PLAY'))
    win.lib.data['folders'] = []
    d = pa.SettingsDialog(win, win.lib, first_run=True)
    assert d._paths() == [str(tmp_path / 'Videos' / 'PLAY')]
    assert 'default' in d.folders.item(0).text()
    monkeypatch.setattr(pa.SettingsDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(pa, 'SettingsDialog', lambda *a, **k: d)
    win.open_settings(first_run=True)
    assert os.path.isdir(str(tmp_path / 'Videos' / 'PLAY'))                   # made when saved
    assert [f['path'] for f in win.lib.folders] == [str(tmp_path / 'Videos' / 'PLAY')]


def test_more_folders_and_which_is_default(win, tmp_path, monkeypatch):
    d = pa.SettingsDialog(win, win.lib)
    first = d._paths()
    extra = tmp_path / 'More'
    extra.mkdir()
    monkeypatch.setattr(pa.QFileDialog, 'getExistingDirectory', lambda *a: str(extra))
    d._add()
    assert d._paths() == first + [str(extra)] and 'default' not in d.folders.item(len(first)).text()
    d.folders.setCurrentRow(len(first))
    d._make_default()
    assert d._paths()[0] == str(extra) and 'default' in d.folders.item(0).text()
    fs = d.values()[0]
    assert all(f['kids'] is False for f in fs)                              # Settings never makes a Kids folder


def test_add_media_copies_the_files_into_the_default_folder(win, tmp_path, monkeypatch):
    downloads = tmp_path / 'Downloads'
    make(str(downloads), 'Up 2009.mkv', b'up' * 500)
    make(str(downloads), 'Heat 1995.mkv', b'other heat' * 500)
    default = win.lib.default_folder
    make(default, 'Heat 1995.mkv', b'already here' * 100)                   # a name that's taken
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames',
                        lambda *a, **k: ([str(downloads / 'Up 2009.mkv'), str(downloads / 'Heat 1995.mkv')], ''))

    def yes_with_kids(box):
        box.checkBox().setChecked(True)
        return pa.QMessageBox.Yes
    monkeypatch.setattr(pa.QMessageBox, 'exec', yes_with_kids)
    win.add_media()
    assert sorted(f.name for f in downloads.iterdir()) == ['Heat 1995.mkv', 'Up 2009.mkv']   # copied: still there
    assert os.path.exists(os.path.join(default, 'Up 2009.mkv'))
    assert os.path.exists(os.path.join(default, 'Heat 1995 [2].mkv'))        # nothing overwritten
    up = library.fingerprint(os.path.join(default, 'Up 2009.mkv'), 1000)
    assert win.lib.data['items'][up]['kids'] is True


def test_add_media_needs_a_folder_and_can_be_cancelled(win, tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a))
    folders = win.lib.data['folders']
    win.lib.data['folders'] = []
    win.add_media()
    assert told
    win.lib.data['folders'] = folders
    src = tmp_path / 'x' / 'Up 2009.mkv'
    make(str(src.parent), src.name)
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(src)], ''))
    monkeypatch.setattr(pa.QMessageBox, 'exec', lambda box: pa.QMessageBox.No)
    win.add_media()
    assert src.exists()                                                      # not moved


def test_the_panel_on_the_right_shows_only_with_a_selection(win):
    win.show()
    win._detail_for([])
    assert not win.detail_pane.isVisible()
    win._detail_for([win.lib._items[0]])
    assert win.detail_pane.isVisible()
    win.hide()



# ------------------------------------------------------------------ de8c9f7 review
def _films(win, n=None):
    fs = [i for i in win.lib._items if i['kind'] == 'movie']
    return fs[:n] if n else fs



# ------------------------------------------------------------------ 1a25fcb review
def test_the_progress_window_cant_be_closed_mid_move(qapp):
    dlg = pa._Progress('Adding media', None, 0, 2)
    dlg.show()
    dlg.reject()                                    # Esc
    assert dlg.isVisible()
    dlg.job_done = True
    dlg.reject()
    assert not dlg.isVisible()


def test_nothing_else_starts_while_files_move(win, monkeypatch):
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a))
    opened = []
    monkeypatch.setattr(pa, 'SettingsDialog', lambda *a, **k: opened.append(1))
    quit_called = []
    monkeypatch.setattr(QApplication.instance(), 'quit', lambda: quit_called.append(1))
    win.moving = True
    win.open_settings()
    win.open_tv_setup()
    win.add_media()
    win.quit_app()
    assert len(told) == 4 and opened == [] and quit_called == []
    win.moving = False


def test_a_default_folder_that_cant_be_opened_is_said(win, monkeypatch, tmp_path):
    warned = []
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: warned.append(a[2]))
    src = tmp_path / 'x' / 'Up 2009.mkv'
    make(str(src.parent), src.name)
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(src)], ''))
    monkeypatch.setattr(pa.QMessageBox, 'exec', lambda box: pa.QMessageBox.Yes)
    monkeypatch.setattr(pa.os, 'makedirs', lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    win.add_media()
    assert warned and src.exists()


def test_a_renamed_default_folder_isnt_made_again(win, monkeypatch, tmp_path):
    gone = tmp_path / 'Renamed'
    d = pa.SettingsDialog(win, win.lib)
    d.folders.clear()
    d._add_row(str(gone))
    d._mark_default()
    monkeypatch.setattr(pa.SettingsDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(pa, 'SettingsDialog', lambda *a, **k: d)
    win.open_settings()
    assert not gone.exists()                        # reported as not found, not quietly recreated


def test_the_videos_folder_comes_from_windows():
    assert os.path.basename(library.default_folder_path()) == 'PLAY'
    assert os.path.isabs(library.videos_folder())



# ------------------------------------------------------------------ 1104925 review
def test_the_progress_window_ignores_its_close_box_mid_move(qapp):
    from PySide6.QtGui import QCloseEvent
    dlg = pa._Progress('Adding media', None, 0, 2)
    e = QCloseEvent()
    dlg.closeEvent(e)
    assert not e.isAccepted()
    dlg.job_done = True
    e = QCloseEvent()
    dlg.closeEvent(e)
    assert e.isAccepted()
    dlg.deleteLater()


def test_nothing_starts_while_the_file_picker_is_open(win, monkeypatch):
    seen = []

    def picker(*a, **k):
        seen.append(win.moving)                     # busy from the picker on
        return ([], '')
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', picker)
    win.add_media()
    assert seen == [True] and win.moving is False


def test_closing_without_a_tray_waits_for_a_move(win, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: None)
    win.tray = None
    win.moving = True
    e = QCloseEvent()
    win.closeEvent(e)
    assert not e.isAccepted()
    win.moving = False


def test_a_kids_mark_that_couldnt_be_set_is_said(win, tmp_path, monkeypatch):
    src = tmp_path / 'x' / 'Up 2009.mkv'
    make(str(src.parent), src.name)
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(src)], ''))

    def yes_with_kids(box):
        box.checkBox().setChecked(True)
        return pa.QMessageBox.Yes
    monkeypatch.setattr(pa.QMessageBox, 'exec', yes_with_kids)
    monkeypatch.setattr(pa.media.library, 'fingerprint', lambda *a: (_ for _ in ()).throw(PermissionError('av')))
    warned = []
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: warned.append(a[2]))
    win.add_media()
    assert warned and 'could not be marked Kids' in warned[0]


def test_settings_never_writes_a_kids_folder_back(win, tmp_path):
    kids = tmp_path / 'OldKids'
    kids.mkdir()
    win.lib.set_folders(win.lib.folders + [{'path': str(kids), 'kids': True}])
    d = pa.SettingsDialog(win, win.lib)
    assert all(f['kids'] is False for f in d.values()[0])
    d.deleteLater()


def test_the_videos_folder_falls_back_to_the_profile(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes, 'windll', None, raising=False)          # Windows won't say
    assert library.videos_folder() == os.path.join(os.path.expanduser('~'), 'Videos')



# ------------------------------------------------------------------ 861140e review
def test_the_style_is_set_once_and_the_stylesheet_once_per_skin(qapp, monkeypatch):
    import suite_style
    calls = {'style': 0, 'sheet': 0}
    real_style, real_sheet = QApplication.setStyle, QApplication.setStyleSheet

    def set_style(self, *a):                        # (a static method, called on the app)
        calls['style'] += 1
        return real_style(*a)

    def set_sheet(self, *a):
        calls['sheet'] += 1
        return real_sheet(self, *a)
    qapp.setStyleSheet('')                          # (no stylesheet style wrapping the one about to be replaced)
    QApplication.setStyle('Windows')                # start from another style, as a fresh app does
    monkeypatch.setattr(QApplication, 'setStyle', set_style)
    monkeypatch.setattr(QApplication, 'setStyleSheet', set_sheet)
    monkeypatch.setattr(suite_style, '_fusion_for', None)
    names = list(skins.SKINS)
    pa.apply_skin(qapp, names[0])
    pa.apply_skin(qapp, names[1])                   # a different skin
    pa.apply_skin(qapp, names[0])
    assert calls == {'style': 1, 'sheet': 3}        # Fusion once; one stylesheet per skin



# ------------------------------------------------------------------ the collection window, the pick window, Add to Kids
def _capture_dialogs(monkeypatch):
    shown = []
    monkeypatch.setattr(pa, 'run_dialog', lambda d, *a: shown.append(d))
    return shown


def test_new_collection_opens_the_window_with_the_selection_in_it(win, monkeypatch):
    shown = _capture_dialogs(monkeypatch)
    films = _films(win, 2)
    episode = next(i for i in win.lib._items if i['kind'] == 'episode')
    win.sel = films + [episode]
    next(b for b in win.findChildren(pa.QPushButton) if b.text() == 'New collection…').click()
    (d,) = shown
    assert isinstance(d, pa.CollectionDialog) and d.cid is None
    assert sorted(d.ids()) == sorted(i['id'] for i in films)              # films only, nothing asked first
    win.sel = []
    win.new_collection()                            # nothing selected: the window still opens, empty
    assert shown[1].ids() == []


def test_the_collection_window_makes_a_collection(win, monkeypatch):
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    d = pa.CollectionDialog(win, win.lib, None, [])
    d._save()
    assert 'name' in told[-1] and not win.lib.data['collections']           # a name first
    d.name.setText('Comedy')
    d._save()
    assert 'Add at least one film' in told[-1]
    films = _films(win, 3)
    monkeypatch.setattr(pa.PickDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(pa.PickDialog, 'picked', lambda self: [i['id'] for i in films])
    d._pick()                                       # + Add films
    assert sorted(d.ids()) == sorted(i['id'] for i in films)
    d.custom.setChecked(True)
    d._save()
    (c,) = win.lib.data['collections'].values()
    assert c['name'] == 'Comedy' and c['order'] == 'custom' and sorted(c['members']) == sorted(d.ids())
    d.deleteLater()


def test_the_pick_window_offers_only_what_isnt_in_the_collection(win, monkeypatch):
    films = _films(win, 3)
    cid = win.lib.make_collection('Comedy', [films[0]['id']])
    d = pa.CollectionDialog(win, win.lib, cid)
    offered = []
    real_init = pa.PickDialog.__init__

    def spy(self, parent, title, items, *a, **k):
        offered.extend(i['id'] for i in items)
        real_init(self, parent, title, items, *a, **k)
    monkeypatch.setattr(pa.PickDialog, '__init__', spy)
    monkeypatch.setattr(pa.PickDialog, 'exec', lambda self: QDialog.Rejected)
    d._pick()
    assert films[0]['id'] not in offered and films[1]['id'] in offered
    assert all(win.lib.by_id(i)['kind'] == 'movie' for i in offered)       # episodes aren't offered
    d.deleteLater()


def test_a_name_that_exists_is_refused(win, monkeypatch):
    films = _films(win, 2)
    win.lib.make_collection('Comedy', [films[0]['id']])
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    d = pa.CollectionDialog(win, win.lib, None, [films[1]['id']])
    d.name.setText(' comedy')
    d._save()
    assert 'already a collection called' in told[-1] and len(win.lib.data['collections']) == 1
    d.deleteLater()


def test_taking_films_out_of_another_collection_asks_first(win, monkeypatch):
    films = _films(win, 3)
    marvel = win.lib.make_collection('Marvel', [films[0]['id'], films[1]['id']])
    asked = []
    monkeypatch.setattr(pa, 'ask', lambda parent, title, text: asked.append(text) or False)
    d = pa.CollectionDialog(win, win.lib, None, [i['id'] for i in films])
    d.name.setText('Action')
    d._save()
    assert 'Marvel' in asked[0] and 'removed' in asked[0]                   # it would be left empty
    assert set(win.lib.data['collections']) == {marvel}                     # said no: nothing changed
    d.deleteLater()


def test_editing_a_collection_and_deleting_it(win, monkeypatch):
    films = _films(win, 2)
    cid = win.lib.make_collection('Comedy', [i['id'] for i in films])
    d = pa.CollectionDialog(win, win.lib, cid)
    d.list.setCurrentRow(0)
    d.list.item(0).setSelected(True)
    d._remove()
    d.name.setText('Funny')
    d._save()
    c = win.lib.data['collections'][cid]
    assert c['name'] == 'Funny' and len(c['members']) == 1
    monkeypatch.setattr(pa, 'ask', lambda *a: True)
    d2 = pa.CollectionDialog(win, win.lib, cid)
    d2._delete()
    assert cid not in win.lib.data['collections']
    d.deleteLater()
    d2.deleteLater()


def test_the_pick_window_searches_and_selects_what_is_shown(win):
    items = win.lib._items
    d = pa.PickDialog(win, 'Add', items, 'Pick')
    d.search.setText('ann droid')
    shown = [d.list.item(r) for r in range(d.list.count()) if not d.list.item(r).isHidden()]
    assert shown and all('Ann Droid' in it.text() for it in shown)
    assert not d.ok.isEnabled()                     # nothing picked yet
    d._tick_shown(pa.Qt.Checked)
    assert sorted(d.picked()) == sorted(it.data(pa.ID) for it in shown) and d.ok.isEnabled()
    d.deleteLater()


def test_the_add_to_kids_button_shows_in_the_kids_view(win):
    win.show()
    rows = {win.rail.item(r).data(pa.ID): r for r in range(win.rail.count())}
    win.rail.setCurrentRow(rows['kids'])
    assert win.kids_add.isVisible()
    win.rail.setCurrentRow(rows['all'])
    assert not win.kids_add.isVisible()
    win.hide()


def test_add_to_kids_offers_what_isnt_kids_and_marks_the_picks(win, monkeypatch):
    offered = []
    real_init = pa.PickDialog.__init__

    def spy(self, parent, title, items, *a, **k):
        offered.extend(i['id'] for i in items)
        real_init(self, parent, title, items, *a, **k)
    monkeypatch.setattr(pa.PickDialog, '__init__', spy)
    target = next(i for i in win.lib._items if not i['kids'])
    monkeypatch.setattr(pa.PickDialog, 'exec', lambda self: QDialog.Accepted)
    monkeypatch.setattr(pa.PickDialog, 'picked', lambda self: [target['id']])
    win.add_to_kids()
    assert all(not win.lib.by_id(i)['kids'] or i == target['id'] for i in offered)
    assert not any(i['kids'] and i['id'] in offered and i['id'] != target['id'] for i in win.lib._items)
    assert win.lib.by_id(target['id'])['kids']



# ------------------------------------------------------------------ the Year box; ad76dbc review
def test_the_year_box_sets_a_films_year(win):
    film = next(i for i in win.lib._items if i['kind'] == 'movie' and not i.get('year'))
    win._detail_for([film])
    assert win.d_year.isEnabled() and win.d_year.text() == ''
    win.d_year.setText('1995')
    win.d_year.setModified(True)
    win._year_done()
    assert win.lib.by_id(film['id'])['year'] == 1995
    win._detail_for([win.lib.by_id(film['id'])])
    win.d_year.setText('')                          # cleared: no year again
    win.d_year.setModified(True)
    win._year_done()
    assert not win.lib.by_id(film['id']).get('year')
    win._detail_for([win.lib.by_id(film['id'])])
    win.d_year.setText('12')                        # not a year: left as it was
    win.d_year.setModified(True)
    win._year_done()
    assert not win.lib.by_id(film['id']).get('year')
    episode = next(i for i in win.lib._items if i['kind'] == 'episode')
    win._detail_for([episode])
    assert win.d_year.isEnabled() and win.d_year_label.text() == 'Show year'   # (an episode: its show's year)


def test_select_all_shown_counts_once(win, monkeypatch):
    items = [dict(win.lib._items[0], id=f'{n:016x}', title=f'Film {n}') for n in range(2000)]
    d = pa.PickDialog(win, 'Add', items, 'Pick')
    calls = []
    real = pa.PickDialog._count
    monkeypatch.setattr(pa.PickDialog, '_count', lambda self: calls.append(1) or real(self))
    d._tick_shown(pa.Qt.Checked)
    assert len(calls) == 1 and len(d.picked()) == 2000
    d._tick_shown(pa.Qt.Unchecked)
    assert len(calls) == 2 and d.picked() == []
    d.deleteLater()


def test_add_to_kids_leaves_out_hidden_titles_and_says_why_when_empty(win, monkeypatch):
    hidden = next(i for i in win.lib._items if not i['kids'])
    win.lib.set_item(hidden['id'], hidden=True)
    offered = []
    real_init = pa.PickDialog.__init__

    def spy(self, parent, title, items, *a, **k):
        offered.extend(i['id'] for i in items)
        real_init(self, parent, title, items, *a, **k)
    monkeypatch.setattr(pa.PickDialog, '__init__', spy)
    monkeypatch.setattr(pa.PickDialog, 'exec', lambda self: QDialog.Rejected)
    win.add_to_kids()
    assert offered and hidden['id'] not in offered
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    monkeypatch.setattr(win.lib, '_scanned', 0)
    win.add_to_kids()
    assert 'still reading' in told[-1]


def test_deleting_a_collection_can_be_called_off(win, monkeypatch):
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    cid = win.lib.make_collection('Comedy', [i['id'] for i in films])
    monkeypatch.setattr(pa, 'ask', lambda *a: False)
    d = pa.CollectionDialog(win, win.lib, cid)
    d._delete()
    assert cid in win.lib.data['collections']
    d.deleteLater()


def test_a_suggestion_named_like_a_collection_joins_it(win, monkeypatch):
    monkeypatch.setattr(pa, 'run_dialog', lambda d, *a: None)
    films = [i for i in win.lib._items if i['title'].startswith('Jungle')]
    cid = win.lib.make_collection('Jungle', [films[0]['id']])
    win.suggestion = {'name': 'Jungle', 'members': [i['id'] for i in films[1:]], 'key': 'x'}
    win._make_suggested()
    assert list(win.lib.data['collections']) == [cid]
    assert sorted(win.lib.data['collections'][cid]['members']) == sorted(i['id'] for i in films)


def test_a_collection_of_only_offline_films_can_still_be_saved(win, monkeypatch):
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    cid = win.lib.make_collection('Comedy', [films[0]['id'], films[1]['id']])
    win.lib.data['collections'][cid]['members'].append('ffffffffffffffff')   # a film on a drive that's away
    monkeypatch.setattr(win.lib, '_incomplete', True)   # (that drive isn't read this time: the film is kept)
    d = pa.CollectionDialog(win, win.lib, cid)
    for _ in range(d.list.count()):
        d.list.item(0).setSelected(True)
        d._remove()
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    d._save()
    assert told == [] and 'ffffffffffffffff' in win.lib.data['collections'][cid]['members']
    d.deleteLater()


def test_episode_lines_dont_repeat_the_file_name():
    base = {'kind': 'episode', 'show': 'Ann Droid', 'season': 1, 'episode': 1, 'episode_last': None,
            'typed_title': False}
    assert pa.title_line(dict(base, title='Ann Droid S01E01')) == 'Ann Droid · S1 E1'
    typed = dict(base, typed_title=True)
    assert pa.title_line(dict(typed, title='The Pool')) == 'Ann Droid · S1 E1 · The Pool'
    assert pa.title_line(dict(typed, title='Ann Droid and the Pool')) == 'Ann Droid · S1 E1 · Ann Droid and the Pool'
    two = dict(base, episode=3, episode_last=4)
    assert pa.title_line(dict(two, title='Ann Droid S01E03-E04')) == 'Ann Droid · S1 E3-E4'


def test_a_renumbered_episode_doesnt_show_its_title_twice(win, tmp_path):
    make(str(tmp_path / 'm' / 'Ann Droid' / 'Season 1'), 'Ann Droid S01E02.mkv', b'two' * 100)
    win.lib.set_folders(win.lib.folders + [{'path': str(tmp_path / 'm')}])
    win.lib.scan()
    ep = next(i for i in win.lib.current() if i.get('show') == 'Ann Droid')
    win.lib.set_item(ep['id'], episode=3)           # renumbered: the title follows the numbers, not typed
    assert pa.title_line(win.lib.by_id(ep['id'])) == 'Ann Droid · S1 E3'
    win.lib.set_item(ep['id'], title='The Pool')
    assert pa.title_line(win.lib.by_id(ep['id'])) == 'Ann Droid · S1 E3 · The Pool'


def test_a_real_double_episode_shows_both_numbers(win, tmp_path):
    make(str(tmp_path / 'm' / 'Ann Droid' / 'Season 2'), 'Ann Droid S02E03-E04.mkv', b'two' * 100)
    win.lib.set_folders(win.lib.folders + [{'path': str(tmp_path / 'm')}])
    win.lib.scan()
    ep = next(i for i in win.lib.current() if i['kind'] == 'episode' and i.get('season') == 2)
    assert pa.title_line(ep) == 'Ann Droid · S2 E3-E4'


def test_the_dropdowns_new_collection_opens_the_window(win, monkeypatch):
    shown = []
    monkeypatch.setattr(pa, 'run_dialog', lambda d, *a: shown.append(d))
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    win.sel = films
    win.d_col.addItem('New collection…', '__new__')
    win.d_col.setCurrentIndex(win.d_col.count() - 1)
    win._col_picked(0)
    assert isinstance(shown[0], pa.CollectionDialog) and sorted(shown[0].ids()) == sorted(i['id'] for i in films)



# ------------------------------------------------------------------ ce5c370 review
def _type_year(win, keys):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest
    win.show()
    win.d_year.setFocus()
    win.d_year.selectAll()
    QTest.keyClick(win.d_year, _Qt.Key_Backspace)
    if keys:
        QTest.keyClicks(win.d_year, keys)
    QTest.keyClick(win.d_year, _Qt.Key_Return)      # Enter, as a person ends it
    win.search.setFocus()


def test_typing_in_the_year_box_saves_and_clearing_it_clears(win):
    film = next(i for i in win.lib._items if i['kind'] == 'movie' and not i.get('year'))
    win._reselect([film['id']])
    win._sel_changed()
    _type_year(win, '1995')
    assert win.lib.by_id(film['id'])['year'] == 1995
    win._detail_for([win.lib.by_id(film['id'])])
    _type_year(win, '')                              # cleared with the keyboard
    assert not win.lib.by_id(film['id']).get('year')
    win._detail_for([win.lib.by_id(film['id'])])
    _type_year(win, '19')                            # not a year: put back, and said
    assert not win.lib.by_id(film['id']).get('year') and win.d_year.text() == ''
    win._detail_for([win.lib.by_id(film['id'])])
    _type_year(win, '1895')                          # an early film is a year
    assert win.lib.by_id(film['id'])['year'] == 1895
    win._detail_for([win.lib.by_id(film['id'])])
    _type_year(win, '19²')                           # (AZERTY's plain key: isdigit() but no int)
    assert win.lib.by_id(film['id'])['year'] == 1895 and win.d_year.text() == '1895'
    win._status()                                    # the note outlives the next status redraw
    assert 'is not a year' in win.status.text()
    win.hide()


def test_removing_every_film_isnt_saved_as_offline(win, monkeypatch):
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    cid = win.lib.make_collection('Comedy', [i['id'] for i in films])
    win.lib.data['collections'][cid]['members'].append('eeeeeeeeeeeeeeee')   # gone for good (all folders read)
    assert not win.lib._incomplete
    d = pa.CollectionDialog(win, win.lib, cid)
    for _ in range(d.list.count()):
        d.list.item(0).setSelected(True)
        d._remove()
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    d._save()
    assert 'Add at least one film' in told[-1] and cid in win.lib.data['collections']
    d.deleteLater()


def test_only_hidden_titles_left_says_so(win, monkeypatch):
    for i in win.lib._items:
        if not i['kids']:
            win.lib.set_item(i['id'], hidden=True)
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    win.add_to_kids()
    assert 'hidden' in told[-1]


# ------------------------------------------------------------------ f8c0536 review
def test_a_year_typed_then_another_film_picked_is_saved(win):
    from PySide6.QtTest import QTest
    a, b = [i for i in win.lib._items if i['kind'] == 'movie' and not i.get('year')][:2]
    win.show()
    win._reselect([a['id']])
    win._sel_changed()
    win.d_year.setFocus()
    QTest.keyClicks(win.d_year, '2001')              # typed, no Enter, no focus change ...
    win._reselect([b['id']])                         # ... and another film picked
    win._sel_changed()
    assert win.lib.by_id(a['id'])['year'] == 2001 and not win.lib.by_id(b['id']).get('year')
    win.hide()


def test_add_to_kids_with_nothing_in_kids_and_all_hidden(win, monkeypatch):
    for i in win.lib._items:
        win.lib.set_item(i['id'], hidden=True, kids=False)
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    win.add_to_kids()
    assert told[-1].startswith('Everything in the library is hidden')


# ------------------------------------------------------------------ 182310e review
def test_a_collection_whose_films_became_episodes_cant_be_saved_empty(win, monkeypatch):
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    cid = win.lib.make_collection('Comedy', [films[0]['id']])
    win.lib.data['collections'][cid]['members'].append(films[1]['id'])
    # the second one is no film now (an episode, say): the window doesn't list it ...
    monkeypatch.setitem(win.lib._by_id, films[1]['id'], dict(films[1], kind='episode'))
    d = pa.CollectionDialog(win, win.lib, cid)
    assert d.shown == [films[0]['id']]
    d.list.item(0).setSelected(True)
    d._remove()
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    d._save()
    assert told and 'Add at least one film' in told[-1]  # ... so nothing is left to save
    d.deleteLater()


def test_a_title_typed_then_another_film_picked_is_saved(win):
    from PySide6.QtTest import QTest
    a, b = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    win.show()
    win._reselect([a['id']])
    win._sel_changed()
    win.d_title.setFocus()
    win.d_title.selectAll()
    QTest.keyClicks(win.d_title, 'Renamed Film')
    win._reselect([b['id']])
    win._sel_changed()
    assert win.lib.by_id(a['id'])['title'] == 'Renamed Film' and win.lib.by_id(b['id'])['title'] == b['title']
    win.hide()


def test_a_year_never_lands_on_an_episode(win):
    ep = next(i for i in win.lib._items if i['kind'] == 'episode')
    win.d_year.setText('1999')
    win.d_year.setModified(True)
    win._save_year(ep['id'])
    assert not win.lib.by_id(ep['id']).get('year')


# ------------------------------------------------------------------ 2593796 review
def _row_of(w, title):
    for r in range(w.model.rowCount()):
        idx = w.model.index(r, 0)
        if idx.data(pa.KIND) == 'item' and w.lib.by_id(idx.data(pa.ID))['title'] == title:
            return idx
    raise AssertionError(title)


# Toy Shelf renamed Aardvark moves to the top: every row above it shifts down one (a redraw before the click
# lands would put another film under the mouse)
@pytest.mark.parametrize('film, box, keys', [('Toy Shelf', 'd_title', 'Aardvark')])   # (a year doesn't move a row)
def test_typing_then_clicking_another_film_selects_that_film(win, film, box, keys):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest
    win.resize(1000, 600)
    win.show()
    win.activateWindow()
    if not QTest.qWaitForWindowActive(win):          # a focus change needs an active window
        pytest.skip('no active window on this platform')
    pick(win, film)
    conor = win.sel[0]['id']
    edit = getattr(win, box)
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, keys)                       # no Enter: the click below ends the typing
    target = _row_of(win, 'Jungle Park III')
    want = target.data(pa.ID)
    QTest.mouseClick(win.view.viewport(), _Qt.LeftButton, pos=win.view.visualRect(target).center())
    for _ in range(3):
        QApplication.processEvents()
    assert win.selected_ids() == [want]               # the film clicked, not whatever slid under the mouse
    saved = win.lib.by_id(conor)
    assert (saved['title'] == 'Aardvark') if box == 'd_title' else (saved['year'] == 1999)
    win.hide()


def test_nothing_selected_then_the_same_film_shows_its_title(win):
    pick(win, 'Conor')
    win.view.clearSelection()
    win._sel_changed()
    pick(win, 'Conor')
    assert win.d_title.text() == 'Conor' and win.d_year.text() == '1982'



# ------------------------------------------------------------------ c104ade review
def _search_away(win):
    """A real redraw that filters the selected film out of view (the search box)."""
    win.search.setText('Clip')
    win.search_timer.stop()
    win.refresh()


@pytest.mark.parametrize('focused', [False, True])
def test_a_refresh_while_typing_doesnt_save_half_a_title(win, focused):
    from PySide6.QtTest import QTest
    if focused:                                         # (hiding the panel mid-redraw fires editingFinished)
        win.resize(1000, 600)
        win.show()
        win.activateWindow()
        if not QTest.qWaitForWindowActive(win):
            pytest.skip('no active window on this platform')
    pick(win, 'Conor')
    conor = win.sel[0]['id']
    if focused:
        win.d_title.setFocus()
        QApplication.processEvents()
        assert win.d_title.hasFocus()
    win.d_title.selectAll()
    QTest.keyClicks(win.d_title, 'Aardv')                # still typing ...
    _search_away(win)                                   # ... and a redraw drops the film from view
    assert win.sel == [] and "wasn't saved" in (win._note or ('',))[0]   # (the search did drop it)
    win.hide()
    assert win.lib.by_id(conor)['title'] == 'Conor'     # half a word isn't saved
    assert 'title' not in win.lib.data['items'].get(conor, {})


def test_saves_in_a_row_redraw_once(win, monkeypatch):
    films = [i for i in win.lib._items if i['kind'] == 'movie'][:2]
    calls = []
    monkeypatch.setattr(win, 'refresh', lambda: calls.append(1))
    for f in films:
        pick(win, f['title'])
        win.d_title.setText(f['title'] + ' X')
        win.d_title.setModified(True)
        win._title_done()
    QApplication.processEvents()
    QApplication.processEvents()
    assert len(calls) == 1


def test_an_edit_for_a_film_thats_gone_says_so(win):
    pick(win, 'Conor')
    fid = win.sel[0]['id']
    win.lib._by_id.pop(fid)                              # gone meanwhile
    win.d_title.setText('New Name')
    win.d_title.setModified(True)
    assert win._save_title(fid) is False and 'no longer in the library' in win._note[0]


def test_after_a_save_the_panel_holds_the_saved_film(win):
    pick(win, 'Conor')
    win.d_title.setText('Conor Renamed')
    win.d_title.setModified(True)
    win._title_done()
    assert win.sel[0]['title'] == 'Conor Renamed'        # before the redraw



# ------------------------------------------------------------------ 45a7558 review
def test_a_refresh_dropping_typing_says_so(win):
    from PySide6.QtTest import QTest
    pick(win, 'Conor')
    fid = win.sel[0]['id']
    QTest.keyClicks(win.d_title, 'x')
    _search_away(win)
    assert win._note and "wasn't saved" in win._note[0]
    assert 'title' not in win.lib.data['items'].get(fid, {})   # nothing half typed was saved


def test_enter_with_nothing_typed_says_nothing(win):
    pick(win, 'Conor')
    win.lib._by_id.pop(win.sel[0]['id'])           # gone meanwhile
    win._note = None
    win._title_done()
    win._year_done()
    assert win._note is None


def test_a_sync_refresh_cancels_the_pending_redraw(win):
    win._redraw.start()
    win.refresh()
    assert not win._redraw.isActive()


def test_a_year_typed_on_an_episode_is_its_shows_year(win):
    ep = next(i for i in win.lib._items if i['kind'] == 'episode')
    win.d_year.setText('1999')
    assert win._save_year(ep['id']) is True and win.lib.show_year(ep['show']) == 1999
    assert win.lib.by_id(ep['id'])['year'] is None


def test_the_heading_follows_a_saved_title(win):
    pick(win, 'Conor')
    win.d_title.setText('Conor Renamed')
    win.d_title.setModified(True)
    win._title_done()
    assert win.d_heading.text() == 'Conor Renamed'


def _find(w, title, parent=None):
    from PySide6.QtCore import QModelIndex
    parent = parent or QModelIndex()
    for r in range(w.model.rowCount(parent)):
        idx = w.model.index(r, 0, parent)
        if idx.data(pa.KIND) == 'item' and w.lib.by_id(idx.data(pa.ID))['title'] == title:
            return idx
        hit = _find(w, title, idx)
        if hit is not None:
            return hit
    return None


def test_a_year_typed_in_collections_then_a_click_selects_the_film_clicked(win, monkeypatch):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest
    monkeypatch.setattr(pa.CollectionDialog, 'exec', lambda self: QDialog.Accepted)
    win._make_suggested()                            # Jungle Park (1993), III (2001), World (2015)
    win.resize(1000, 600)
    win.show()
    win.activateWindow()
    if not QTest.qWaitForWindowActive(win):
        pytest.skip('no active window on this platform')
    set_filter(win, 'collections')
    win.view.expandAll()
    pick(win, 'Jungle Park')
    jp = win.sel[0]['id']
    win.d_year.setFocus()
    win.d_year.selectAll()
    QTest.keyClicks(win.d_year, '2020')              # moves it last in release order
    target = _find(win, 'Jungle World')
    want = target.data(pa.ID)
    QTest.mouseClick(win.view.viewport(), _Qt.LeftButton, pos=win.view.visualRect(target).center())
    for _ in range(3):
        QApplication.processEvents()
    win.view.expandAll()
    assert win.selected_ids() == [want] and win.lib.by_id(jp)['year'] == 2020
    win.hide()



# ------------------------------------------------------------------ 42bd0aa review
def test_a_selection_change_saves_even_with_the_box_focused(win):
    from PySide6.QtTest import QTest
    win.resize(1000, 600)
    win.show()
    win.activateWindow()
    if not QTest.qWaitForWindowActive(win):
        pytest.skip('no active window on this platform')
    pick(win, 'Conor')
    fid = win.sel[0]['id']
    win.d_title.setFocus()
    QApplication.processEvents()
    assert win.d_title.hasFocus()
    win.d_title.selectAll()
    QTest.keyClicks(win.d_title, 'Conor Two')
    pick(win, 'Clip')                                # the selection moves (not a redraw), focus still in the box
    assert win.lib.by_id(fid)['title'] == 'Conor Two'    # only a redraw holds a typed edit back, not the focus
    win.hide()


def test_a_deleted_film_while_typing_says_its_gone(win):
    from PySide6.QtTest import QTest
    pick(win, 'Conor')
    fid = win.sel[0]['id']
    path = win.sel[0]['path']
    QTest.keyClicks(win.d_title, 'x')
    os.remove(path)                                  # deleted on disk while typing
    win.lib.scan()
    win.refresh()                                    # the real redraw after the scan
    assert win.lib.by_id(fid) is None and 'no longer in the library' in win._note[0]
    assert 'title' not in win.lib.data['items'].get(fid, {})


def test_the_heading_is_left_alone_for_another_films_save(win):
    pick(win, 'Conor')
    other = next(i for i in win.lib._items if i['title'] == 'Clip')
    win.lib.set_item(other['id'], title='Clip Two')
    win._saved(other['id'])
    assert win.d_heading.text() == 'Conor'



def test_add_media_refuses_when_the_drive_is_too_full(win, tmp_path, monkeypatch):
    make(str(tmp_path / 'Downloads'), 'Up 2009.mkv', b'up' * 500)
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(tmp_path / 'Downloads' / 'Up 2009.mkv')], ''))
    monkeypatch.setattr(pa.shutil, 'disk_usage', lambda p: type('U', (), {'free': 10})())
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'warning', lambda *a: told.append(a[2]))
    asked = []
    monkeypatch.setattr(pa.QMessageBox, 'exec', lambda box: asked.append(1) or pa.QMessageBox.Yes)
    win.add_media()
    assert told and 'Not enough space' in told[-1] and asked == []
    assert not os.path.exists(os.path.join(win.lib.default_folder, 'Up 2009.mkv'))



def test_the_year_box_sets_an_episodes_show_year(win):
    ep = next(i for i in win.lib._items if i['kind'] == 'episode')
    pick(win, ep['title'])
    assert win.d_year.isEnabled() and win.d_year_label.text() == 'Show year'
    win.d_year.setText('2026')
    win.d_year.setModified(True)
    win._year_done()
    assert win.lib.show_year(ep['show']) == 2026
    assert win.lib.by_id(ep['id'])['year'] is None                 # the episode itself has no film year
    pick(win, ep['title'])
    assert win.d_year.text() == '2026'



def test_add_media_waits_for_the_first_scan_and_no_leaves_no_folder(win, tmp_path, monkeypatch):
    make(str(tmp_path / 'Downloads'), 'Up 2009.mkv', b'up' * 500)
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(tmp_path / 'Downloads' / 'Up 2009.mkv')], ''))
    told = []
    monkeypatch.setattr(pa.QMessageBox, 'information', lambda *a: told.append(a[2]))
    monkeypatch.setattr(win.lib, '_scanned', 0)
    win.add_media()
    assert 'still reading' in told[-1]
    monkeypatch.undo()
    monkeypatch.setattr(pa.QFileDialog, 'getOpenFileNames', lambda *a, **k: ([str(tmp_path / 'Downloads' / 'Up 2009.mkv')], ''))
    new_default = tmp_path / 'Fresh' / 'PLAY'
    monkeypatch.setattr(type(win.lib), 'default_folder', property(lambda self: str(new_default)))
    monkeypatch.setattr(pa.QMessageBox, 'exec', lambda box: pa.QMessageBox.No)
    win.add_media()
    assert not new_default.exists()                               # No: nothing made
