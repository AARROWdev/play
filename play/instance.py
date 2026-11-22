"""One PLAY per PC: only one copy can serve the TVs on port 8600.

PLAY holds two named mutexes for as long as it runs: one for this sign-in session and one for the whole PC
(Global\\). Both are created with a null DACL, so every account can see them. That lets the uninstaller (play.iss,
CheckForMutexes) see a PLAY running under another account, and lets a second account's launch tell that case apart
from "already running in my own session". A launch that finds another PLAY lets go of every handle it opened at
once, so it can't keep the mutex alive after the real owner quits.
"""
import ctypes
from ctypes import wintypes

LOCAL = 'PLAYAppMutex'
GLOBAL = 'Global\\PLAYAppMutex'
NONE, SAME_SESSION, OTHER_SESSION = 'none', 'same session', 'other session'
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
NULL_DACL = 'D:NO_ACCESS_CONTROL'

_held = []          # mutex handles of the running PLAY; Windows releases them when the process exits


class _SA(ctypes.Structure):
    _fields_ = [('nLength', wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p),
                ('bInheritHandle', wintypes.BOOL)]


def sa_from(sddl):
    """SECURITY_ATTRIBUTES for an SDDL string, or None if Windows refuses it."""
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    psd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(psd), None):
        return None
    return _SA(ctypes.sizeof(_SA), psd, False)


def session_id():
    """This process's sign-in session, or -1 if Windows can't say (treated like an ordinary session)."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    sid = wintypes.DWORD(0)
    if not k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid)):
        return -1
    return sid.value


def check(local=LOCAL, global_=GLOBAL, session=None):
    """Take PLAY's mutexes and say who else holds them: NONE (this is the only PLAY; the mutexes are now held),
    SAME_SESSION (another copy in this sign-in session: hand over to it) or OTHER_SESSION (PLAY runs for another
    Windows account, or as a scheduled task). Unless the answer is NONE, nothing is kept open."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    sa = sa_from(NULL_DACL)
    if session is None:
        session = session_id()
    # In session 0 (a scheduled task or a service) the session name and the Global name are the same object, so
    # only the Global one is taken; any holder is "someone else".
    names = [(global_, OTHER_SESSION)] if session == 0 else [(local, SAME_SESSION), (global_, OTHER_SESSION)]
    opened = []
    for name, if_taken in names:
        h = k32.CreateMutexW(ctypes.byref(sa) if sa else None, False, name)
        err = ctypes.get_last_error()
        if h:
            opened.append(h)
        # ACCESS_DENIED: a PLAY from before null DACLs, under another account
        if err == ERROR_ALREADY_EXISTS or (err == ERROR_ACCESS_DENIED and if_taken == OTHER_SESSION):
            for x in opened:
                k32.CloseHandle(x)
            return if_taken
    _held.extend(opened)
    return NONE


def release():
    """Let go of PLAY's mutexes (tests; the app itself just exits)."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    while _held:
        k32.CloseHandle(_held.pop())
