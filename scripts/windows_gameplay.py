"""Owned-window observation/capture for Windows gameplay measurements (stdlib only)."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import struct
import time
import zlib


class WindowsDesktop:
    def __init__(self):
        self.user = c.WinDLL('user32', use_last_error=True)
        self.gdi = c.WinDLL('gdi32', use_last_error=True)
        self.dwm = c.WinDLL('dwmapi', use_last_error=True)
        self.callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        for fn, args, result in [
            (self.user.EnumWindows, [self.callback, w.LPARAM], w.BOOL),
            (self.user.GetWindowThreadProcessId, [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
            (self.user.GetWindowTextW, [w.HWND, w.LPWSTR, c.c_int], c.c_int),
            (self.user.IsWindowVisible, [w.HWND], w.BOOL),
            (self.user.IsIconic, [w.HWND], w.BOOL),
            (self.user.GetClientRect, [w.HWND, c.POINTER(w.RECT)], w.BOOL),
            (self.user.ClientToScreen, [w.HWND, c.POINTER(w.POINT)], w.BOOL),
            (self.user.GetForegroundWindow, [], w.HWND),
            (self.user.SetForegroundWindow, [w.HWND], w.BOOL),
            (self.user.ShowWindow, [w.HWND, c.c_int], w.BOOL),
            (self.user.SetWindowPos, [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT], w.BOOL),
            (self.user.GetWindowRect, [w.HWND, c.POINTER(w.RECT)], w.BOOL),
            (self.user.PostMessageW, [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
            (self.user.GetDC, [w.HWND], w.HDC),
            (self.user.ReleaseDC, [w.HWND, w.HDC], c.c_int),
            (self.gdi.CreateCompatibleDC, [w.HDC], w.HDC),
            (self.gdi.CreateCompatibleBitmap, [w.HDC, c.c_int, c.c_int], w.HBITMAP),
            (self.gdi.SelectObject, [w.HDC, w.HANDLE], w.HANDLE),
            (self.gdi.BitBlt, [w.HDC,c.c_int,c.c_int,c.c_int,c.c_int,w.HDC,c.c_int,c.c_int,w.DWORD],w.BOOL),
            (self.gdi.GetDIBits, [w.HDC,w.HBITMAP,w.UINT,w.UINT,c.c_void_p,c.c_void_p,w.UINT],c.c_int),
            (self.gdi.DeleteObject, [w.HANDLE],w.BOOL),
            (self.gdi.DeleteDC, [w.HDC],w.BOOL),
            (self.dwm.DwmGetWindowAttribute,[w.HWND,w.DWORD,c.c_void_p,w.DWORD],c.c_long),
        ]:
            fn.argtypes, fn.restype = args, result
        # Match physical pixel geometry with Vulkan's swapchain dimensions.
        self.user.SetProcessDPIAware()

    def owner(self, window):
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(int(window), c.byref(pid))
        return pid.value

    def find(self, pid, title):
        matches = []
        @self.callback
        def visit(window, unused):
            text = c.create_unicode_buffer(512)
            self.user.GetWindowTextW(window, text, len(text))
            if self.owner(window) == pid and text.value == title:
                matches.append(int(window))
            return True
        self.user.EnumWindows(visit, 0)
        if len(matches) > 1:
            raise ValueError('Multiple windows owned by measurement process')
        return self.observe(matches[0], pid) if matches else {}

    def observe(self, window, pid):
        if self.owner(window) != pid:
            raise ValueError('Window ownership changed')
        rect, origin, cloaked = w.RECT(), w.POINT(), w.DWORD()
        if not self.user.GetClientRect(window, c.byref(rect)) or not self.user.ClientToScreen(window, c.byref(origin)):
            raise c.WinError(c.get_last_error())
        dwm_ok = self.dwm.DwmGetWindowAttribute(window, 14, c.byref(cloaked), c.sizeof(cloaked)) == 0
        width, height = rect.right, rect.bottom
        # Conservative: require foreground, not minimized/cloaked and wholly on the virtual desktop.
        vx, vy, vw, vh = [self.user.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        inside = origin.x >= vx and origin.y >= vy and origin.x+width <= vx+vw and origin.y+height <= vy+vh
        visible = bool(self.user.IsWindowVisible(window) and not self.user.IsIconic(window) and
                       dwm_ok and not cloaked.value and self.user.GetForegroundWindow() == window and inside and width and height)
        return dict(window=window,pid=pid,size=[width,height],position=[origin.x,origin.y],visible=visible,
                    hidden=not visible,foreground=self.user.GetForegroundWindow()==window,
                    cloaked=cloaked.value if dwm_ok else None,visibility_method='foreground/unminimized/uncloaked/client bounds; overlays not detected')

    def prepare(self, window, pid, width=1280, height=720):
        self.observe(window,pid)
        self.user.ShowWindow(window,9)
        outer, inner = w.RECT(), w.RECT()
        self.user.GetWindowRect(window,c.byref(outer)); self.user.GetClientRect(window,c.byref(inner))
        if not self.user.SetWindowPos(window,None,40,40,width+outer.right-outer.left-inner.right,
                                      height+outer.bottom-outer.top-inner.bottom,0x0040):
            raise c.WinError(c.get_last_error())
        self.user.SetForegroundWindow(window)
        return self.observe(window,pid)

    def close(self, window, pid):
        if self.owner(window) != pid:
            raise ValueError('Refusing close after window ownership changed')
        if not self.user.PostMessageW(window,0x0010,0,0):
            raise c.WinError(c.get_last_error())

    def capture(self, window, pid, path, timer_path=None):
        state = self.observe(window,pid)
        if not state['visible']:
            raise ValueError('Cannot capture hidden/unfocused measurement window')
        width,height=state['size']; x,y=state['position']
        state['capture_monotonic_ns']=time.perf_counter_ns()
        source=self.user.GetDC(None); target=self.gdi.CreateCompatibleDC(source)
        bitmap=self.gdi.CreateCompatibleBitmap(source,width,height); old=self.gdi.SelectObject(target,bitmap)
        try:
            if not self.gdi.BitBlt(target,0,0,width,height,source,x,y,0x00CC0020):
                raise c.WinError(c.get_last_error())
            self.gdi.SelectObject(target,old); old=None
            header=struct.pack('<IiiHHIIiiII',40,width,-height,1,32,0,width*height*4,0,0,0,0)
            info=c.create_string_buffer(header); data=c.create_string_buffer(width*height*4)
            if self.gdi.GetDIBits(target,bitmap,0,height,data,info,0)!=height:
                raise c.WinError(c.get_last_error())
            rgba=bytearray(data.raw)
            rgba[0::4],rgba[2::4]=rgba[2::4],rgba[0::4]
            rgba[3::4]=b'\xff'*(width*height)
            write_png(path,width,height,rgba)
            if timer_path:
                timer=bytearray()
                # Same yellow-HUD mask and normalized region as the Linux route.
                for ty in range(34*4):
                    for tx in range(100*4):
                        sx=int((590+tx/4)*width/1280);sy=int((72+ty/4)*height/720)
                        r,g,b=rgba[(sy*width+sx)*4:(sy*width+sx)*4+3]
                        value=0 if r>165 and g>89 and b<102 else 255
                        timer.extend((value,value,value,255))
                write_png(timer_path,400,136,timer)
        finally:
            if old:self.gdi.SelectObject(target,old)
            self.gdi.DeleteObject(bitmap); self.gdi.DeleteDC(target); self.user.ReleaseDC(None,source)
        return state


def write_png(path,width,height,rgba):
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    rows=b''.join(b'\0'+rgba[y*width*4:(y+1)*width*4] for y in range(height))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,6,0,0,0))+
                          chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))
