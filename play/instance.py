"""One PLAY per PC: only one copy can serve the TVs on port 8600.

PLAY holds two named mutexes for as long as it runs: one for this sign-in session and one for the whole PC
(Global\\). Both get a null DACL, so every account can see them. That lets the uninstaller (play.iss,
CheckForMutexes) see a PLAY running under another account, and lets a second account's launch tell that case apart
from "already running in my own session".
"""
import ctypes
from ctypes import wintypes

LOCAL = 'PLAYAppMutex'
GLOBAL = 'Global\\PLAYAppMutex'
NONE, SAME_SESSION, OTHER_SESSION = 'none', 'same session', 'other session'
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

# Mutex handles stay open until PLAY exits; only CloseHandle (or exiting) releases them. Kept here so the
# security descriptor and handles have an owner, not because Python would otherwise close them.
_HELD = []


class _SA(ctypes.Structure):
    _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p),
                ('bInheritHandle', wintypes.BOOL)]


def _null_dacl():
    """Security attributes with a null DACL: anyone may open the mutex (as Inno Setup does for its own)."""
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    sd = ctypes.create_string_buffer(64)                 # SECURITY_DESCRIPTOR_MIN_LENGTH is 40 on x64
    if not adv.InitializeSecurityDescriptor(sd, 1) or not adv.SetSecurityDescriptorDacl(sd, True, None, False):
        return None, None
    sa = _SA(ctypes.sizeof(_SA), ctypes.cast(sd, ctypes.c_void_p), False)
    return sa, sd


def _create(k32, name, sa):
    h = k32.CreateMutexW(ctypes.byref(sa) if sa else None, False, name)
    err = ctypes.get_last_error()
    if h:
        _HELD.append(h)
    return err


def session_id():
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    sid = wintypes.DWORD(0)
    k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid))
    return sid.value


def check(local=LOCAL, global_=GLOBAL, session=None):
    """Take PLAY's mutexes and say who else holds them: NONE (this is the only PLAY), SAME_SESSION (another copy
    in this sign-in session: hand over to it) or OTHER_SESSION (PLAY runs for another Windows account)."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    sa, sd = _null_dacl()
    _HELD.append(sd)
    if session is None:
        session = session_id()
    if session == 0:
        # In session 0 (a scheduled task or a service) the session name and the Global name are the same object,
        # so only the Global one is taken; any holder is "someone else".
        err = _create(k32, global_, sa)
        return OTHER_SESSION if err in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED) else NONE
    if _create(k32, local, sa) == ERROR_ALREADY_EXISTS:
        return SAME_SESSION
    # ACCESS_DENIED: a PLAY started before mutexes had a null DACL, under another account
    if _create(k32, global_, sa) in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED):
        return OTHER_SESSION
    return NONE
