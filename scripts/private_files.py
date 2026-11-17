"""Private license files: POSIX 0600 or a protected current-user Windows ACL."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid


class WindowsFiles:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.security = c.WinDLL("advapi32", use_last_error=True)
        signatures = [
            (self.kernel.GetCurrentProcess, [], w.HANDLE),
            (self.kernel.CloseHandle, [w.HANDLE], w.BOOL),
            (self.kernel.LocalFree, [w.HLOCAL], w.HLOCAL),
            (self.kernel.CreateFileW, [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            (self.kernel.MoveFileExW, [w.LPCWSTR, w.LPCWSTR, w.DWORD], w.BOOL),
            (self.security.OpenProcessToken, [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
            (self.security.GetTokenInformation, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.security.ConvertSidToStringSidW, [c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL),
            (self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW, [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)], w.BOOL),
            (self.security.SetFileSecurityW, [w.LPCWSTR, w.DWORD, c.c_void_p], w.BOOL),
            (self.security.GetFileSecurityW, [w.LPCWSTR, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW, [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.POINTER(w.DWORD)], w.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def checked(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    @contextmanager
    def owner_descriptor(self):
        c, w = self.c, self.w
        token = w.HANDLE()
        self.checked(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x8, c.byref(token)))
        try:
            size = w.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, c.byref(size))  # TokenUser
            buffer = c.create_string_buffer(size.value)
            self.checked(self.security.GetTokenInformation(token, 1, buffer, size, c.byref(size)))
            sid = w.LPWSTR()
            self.checked(self.security.ConvertSidToStringSidW(c.c_void_p.from_buffer(buffer), c.byref(sid)))
            try:
                descriptor = c.c_void_p()
                self.checked(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    "D:P(A;;FA;;;" + sid.value + ")", 1, c.byref(descriptor), None))
            finally:
                self.kernel.LocalFree(c.cast(sid, w.HLOCAL))
        finally:
            self.kernel.CloseHandle(token)
        try:
            yield descriptor
        finally:
            self.kernel.LocalFree(descriptor)

    def create(self, path):
        import msvcrt
        c, w = self.c, self.w

        class Attributes(c.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]

        with self.owner_descriptor() as descriptor:
            attributes = Attributes(c.sizeof(Attributes), descriptor, False)
            handle = self.kernel.CreateFileW(str(path), 0x40000000, 0, c.byref(attributes), 1, 0x80, None)
            if handle == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            try:
                return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY | os.O_NOINHERIT)
            except BaseException:
                self.kernel.CloseHandle(handle)
                raise

    def restrict(self, path):
        with self.owner_descriptor() as descriptor:
            self.checked(self.security.SetFileSecurityW(str(path), 0x80000004, descriptor))

    def publish(self, temporary, output):
        # MOVEFILE_WRITE_THROUGH, deliberately without REPLACE_EXISTING.
        if not self.kernel.MoveFileExW(str(temporary), str(output), 0x8):
            error = self.c.get_last_error()
            if error in (80, 183):
                raise FileExistsError("License sidecar already exists")
            raise self.c.WinError(error)

    def acl(self, path):
        c, w = self.c, self.w
        size = w.DWORD()
        self.security.GetFileSecurityW(str(path), 4, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        self.checked(self.security.GetFileSecurityW(str(path), 4, buffer, size, c.byref(size)))
        text = w.LPWSTR()
        self.checked(self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW(buffer, 1, 4, c.byref(text), None))
        try:
            return text.value
        finally:
            self.kernel.LocalFree(c.cast(text, w.HLOCAL))


def restrict_private(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
        raise ValueError("License sidecar must be a regular file, not a link")
    if os.name == "nt":
        WindowsFiles().restrict(path)
    else:
        path.chmod(0o600)


def publish_private(path, data):
    """Publish complete data without replacing a concurrently created sidecar."""
    path = Path(path)
    temporary = path.with_name(".rif-" + uuid.uuid4().hex + ".tmp")
    windows = WindowsFiles() if os.name == "nt" else None
    descriptor = windows.create(temporary) if windows else os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if windows:
            windows.publish(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
