"""One PLAY per PC: the mutex check, including another account's PLAY and session 0. Unique names per test."""
import ctypes
import uuid
from ctypes import wintypes

from play import instance


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
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    psd = ctypes.c_void_p()
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:', 1, ctypes.byref(psd), None)
    sa = instance._SA(ctypes.sizeof(instance._SA), psd, False)
    h = k32.CreateMutexW(ctypes.byref(sa), False, name)
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
