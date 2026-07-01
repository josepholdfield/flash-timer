"""Windows per-pixel-alpha layered-window helper (ctypes, no dependencies).

Used to build an invisible "click-catcher": a layered window whose pixels are
almost fully transparent (alpha = 1/255, visually imperceptible) over chosen
rectangles and fully transparent (alpha = 0) everywhere else.

Windows hit-tests layered windows per pixel: alpha == 0 passes the click
through to whatever is beneath (the game), while any alpha > 0 receives the
click. This lets the overlay have genuinely transparent-looking regions that are
still clickable/draggable -- which colour-key (`-transparentcolor`) transparency
cannot do, since colour-keyed pixels are always click-through.

Everything here is a no-op / unavailable on non-Windows platforms.
"""

from __future__ import annotations

import sys

try:
    import ctypes
    from ctypes import wintypes
except Exception:  # pragma: no cover - ctypes missing is extremely unlikely
    ctypes = None  # type: ignore


def available() -> bool:
    """True only on Windows with a usable ctypes/user32 stack."""
    return sys.platform == "win32" and ctypes is not None


# --- Win32 constants -------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0


if available():
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_byte),
            ("BlendFlags", ctypes.c_byte),
            ("SourceConstantAlpha", ctypes.c_byte),
            ("AlphaFormat", ctypes.c_byte),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 3),
        ]

    # Explicit prototypes so 64-bit HWND/handles aren't truncated to c_int.
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
        wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL


def toplevel_hwnd(widget) -> int:
    """Return the real top-level HWND that owns a Tk widget."""
    hwnd = int(widget.winfo_id())
    if available():
        root = user32.GetAncestor(hwnd, GA_ROOT)
        if root:
            return int(root)
    return hwnd


def make_layered(hwnd: int) -> None:
    """Flag a window as a per-pixel-alpha layered overlay that never activates."""
    if not available():
        return
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def _build_buffer(w: int, h: int, rects) -> bytearray:
    """Premultiplied top-down BGRA buffer: alpha 1 inside rects, 0 elsewhere."""
    buf = bytearray(w * h * 4)  # zero-filled => fully transparent
    for x0, y0, x1, y1 in rects:
        x0 = max(0, int(x0)); y0 = max(0, int(y0))
        x1 = min(w, int(x1)); y1 = min(h, int(y1))
        if x1 <= x0 or y1 <= y0:
            continue
        # RGB stays 0 (premultiplied) so the near-zero alpha is imperceptible.
        span = bytes((0, 0, 0, 1)) * (x1 - x0)
        stride = len(span)
        for row in range(y0, y1):
            off = (row * w + x0) * 4
            buf[off:off + stride] = span
    return buf


def update_layered(hwnd: int, x: int, y: int, w: int, h: int, rects) -> bool:
    """Position/size the layered window and set its per-pixel alpha mask."""
    if not available() or w <= 0 or h <= 0:
        return False

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    hbmp = None
    old = None
    try:
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # negative => top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not hbmp or not bits:
            return False

        buf = _build_buffer(w, h, rects)
        ctypes.memmove(bits, bytes(buf), len(buf))

        old = gdi32.SelectObject(mem_dc, hbmp)
        dst = POINT(int(x), int(y))
        src = POINT(0, 0)
        size = SIZE(w, h)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
            mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
        return bool(ok)
    finally:
        if old is not None:
            gdi32.SelectObject(mem_dc, old)
        if hbmp:
            gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, screen_dc)
