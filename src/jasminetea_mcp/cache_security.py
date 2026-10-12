"""Private cache paths. Existing unsafe permissions are never silently repaired."""

import os
import stat
from pathlib import Path

from .errors import TeaError


def _unsafe():
    raise TeaError(
        "cache_permissions",
        "Unsafe cache ownership, permissions or link; choose a new private cache_dir or repair it locally.",
    )


def _windows(path: Path, create: bool):
    import ctypes as c
    from ctypes import wintypes as w

    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [
        w.LPWSTR,
        w.DWORD,
        w.DWORD,
        c.POINTER(c.c_void_p),
        c.c_void_p,
        c.POINTER(c.c_void_p),
        c.c_void_p,
        c.POINTER(c.c_void_p),
    ]
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    adv.GetAce.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]

    def sid_string(sid):
        text = w.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, c.byref(text)):
            _unsafe()
        try:
            return text.value
        finally:
            kernel.LocalFree(c.cast(text, c.c_void_p))

    handle, size = w.HANDLE(), w.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(handle)):
        _unsafe()
    try:
        adv.GetTokenInformation(handle, 1, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle, 1, buffer, size, c.byref(size)):
            _unsafe()
        user = sid_string(c.cast(buffer, c.POINTER(c.c_void_p))[0])
    finally:
        kernel.CloseHandle(handle)
    if create:
        descriptor, length = c.c_void_p(), w.DWORD()
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            c.POINTER(c.c_void_p),
            c.POINTER(w.DWORD),
        ]
        adv.GetSecurityDescriptorDacl.argtypes = [
            c.c_void_p,
            c.POINTER(w.BOOL),
            c.POINTER(c.c_void_p),
            c.POINTER(w.BOOL),
        ]
        adv.GetSecurityDescriptorOwner.argtypes = [
            c.c_void_p,
            c.POINTER(c.c_void_p),
            c.POINTER(w.BOOL),
        ]
        adv.SetNamedSecurityInfoW.argtypes = [
            w.LPWSTR,
            w.DWORD,
            w.DWORD,
            c.c_void_p,
            c.c_void_p,
            c.c_void_p,
            c.c_void_p,
        ]
        sddl = f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, c.byref(descriptor), c.byref(length)
        ):
            _unsafe()
        try:
            present, defaulted, dacl = w.BOOL(), w.BOOL(), c.c_void_p()
            if not adv.GetSecurityDescriptorDacl(
                descriptor, c.byref(present), c.byref(dacl), c.byref(defaulted)
            ):
                _unsafe()
            new_owner = c.c_void_p()
            if not adv.GetSecurityDescriptorOwner(descriptor, c.byref(new_owner), c.byref(defaulted)):
                _unsafe()
            if adv.SetNamedSecurityInfoW(str(path), 1, 0x80000005, new_owner, None, dacl, None):
                _unsafe()
        finally:
            kernel.LocalFree(descriptor)
    owner, acl, sd = c.c_void_p(), c.c_void_p(), c.c_void_p()
    if adv.GetNamedSecurityInfoW(str(path), 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(sd)):
        _unsafe()
    try:
        if sid_string(owner) != user or not acl.value:
            _unsafe()
        # ACL header: revision, padding, size, ACE count, padding.
        count = c.c_ushort.from_address(acl.value + 4).value
        allowed = {user, "S-1-5-18", "S-1-5-32-544"}
        for index in range(count):
            ace = c.c_void_p()
            if not adv.GetAce(acl, index, c.byref(ace)):
                _unsafe()
            kind, flags = c.c_ubyte.from_address(ace.value).value, c.c_ubyte.from_address(ace.value + 1).value
            if flags & 8:  # inherit-only ACE does not grant access to this path
                continue
            if kind == 1:  # deny ACE
                continue
            if kind != 0 or sid_string(ace.value + 8) not in allowed:
                _unsafe()
    finally:
        kernel.LocalFree(sd)


def private_path(path: Path, *, directory: bool = True) -> Path:
    path = path.absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _unsafe()
        if os.name == "posix" and item != path and info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            _unsafe()
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if directory:
                path.mkdir(mode=0o700)
            else:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
        except FileExistsError:
            # Another initializing process won the race. Inspect, never repair, its path.
            return private_path(path, directory=directory)
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        _unsafe()
    if os.name == "nt":
        _windows(path, created)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        _unsafe()
    return path
