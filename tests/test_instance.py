"""One PLAY per PC: the mutex check, including another account's PLAY and session 0. Unique names per test."""
import ctypes
import uuid
from ctypes import wintypes

import pytest

from play import instance

# In session 0 (a service, OpenSSH, a CI runner) the local and Global names are one object; these tests model a
# normal sign-in session.
pytestmark = pytest.mark.skipif(instance.session_id() == 0, reason='runs in a normal sign-in session')


def names():
    u = uuid.uuid4().hex
    return 'PLAYTest-' + u, 'Global\\PLAYTest-' + u


def test_first_copy_then_same_session():
    loc, glo = names()
    assert instance.check(loc, glo, session=1) == instance.NONE
    assert instance.check(loc, glo, session=1) == instance.SAME_SESSION


def hold_denied(name):
    """Create a mutex nobody (not even this process) may open again: what another account's PLAY looks like
    when its mutex has a default, owner-only DACL."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(ctypes.byref(instance.sa_from('D:')), False, name)
    assert h
    return h


def test_another_accounts_play_is_other_session():
    loc, glo = names()
    keep = hold_denied(glo)
    assert instance.check(loc, glo, session=1) == instance.OTHER_SESSION
    assert keep


def test_another_session_with_open_mutex_is_other_session():
    loc1, glo = names()
    assert instance.check(loc1, glo, session=1) == instance.NONE
    loc2 = 'PLAYTest-' + uuid.uuid4().hex        # a different session's local name, same Global name
    assert instance.check(loc2, glo, session=2) == instance.OTHER_SESSION


def test_session_zero_alone_is_the_only_play():
    loc, glo = names()
    assert instance.check(loc, glo, session=0) == instance.NONE
    assert instance.check(loc, glo, session=0) == instance.OTHER_SESSION


def test_mutexes_have_a_null_dacl():
    """A null DACL is what lets any account (and the uninstaller's CheckForMutexes) open them."""
    loc, glo = names()
    instance.check(loc, glo, session=1)
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p
    for name in (loc, glo):
        h = k32.OpenMutexW(0x00020000, False, name)          # READ_CONTROL
        assert h
        dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
        rc = adv.GetSecurityInfo(wintypes.HANDLE(h), 6, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
        k32.CloseHandle(wintypes.HANDLE(h))
        assert rc == 0 and dacl.value is None, (name, rc, dacl.value)



def test_a_launch_that_finds_another_play_keeps_nothing_open():
    """Account B's launch saw A's PLAY; once A's PLAY quits, the next launch must be able to start."""
    loc, glo = names()
    owner = hold_denied(glo)
    assert instance.check(loc, glo, session=1) == instance.OTHER_SESSION
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle(owner)                                   # A quits
    assert instance.check(loc, glo, session=1) == instance.NONE


def test_default_session_path():
    loc, glo = names()
    assert instance.check(loc, glo) == instance.NONE
    assert instance.check(loc, glo) == instance.SAME_SESSION


def test_session_id_is_this_process():
    k32 = ctypes.WinDLL('kernel32')
    sid = wintypes.DWORD()
    k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid))
    assert instance.session_id() == sid.value



def test_already_exists_with_a_handle_is_closed():
    """Another session's PLAY holds Global (with a null DACL, so this launch gets a handle back). After this launch
    sees it, the owner quits: the mutex must disappear, i.e. this launch kept no handle."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.OpenMutexW.restype = ctypes.c_void_p
    loc, glo = names()
    owner = k32.CreateMutexW(ctypes.byref(instance.sa_from(instance.NULL_DACL)), False, glo)
    assert owner
    assert instance.check('PLAYTest-' + uuid.uuid4().hex, glo, session=1) == instance.OTHER_SESSION
    k32.CloseHandle(owner)                                    # the other session's PLAY quits
    assert not k32.OpenMutexW(0x00100000, False, glo)         # gone: nobody here still holds it
