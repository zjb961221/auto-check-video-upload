"""CI-only visual review; stdlib GDI capture, no runtime imaging dependency."""
import base64
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from unittest.mock import patch, MagicMock
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


def capture(window):
    user,gdi=ctypes.windll.user32,ctypes.windll.gdi32
    user.GetParent.argtypes=[w.HWND];user.GetParent.restype=w.HWND
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.GetDC.argtypes=[w.HWND];user.GetDC.restype=w.HDC
    user.ReleaseDC.argtypes=[w.HWND,w.HDC]
    user.PrintWindow.argtypes=[w.HWND,w.HDC,w.UINT]
    gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
    gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=w.HBITMAP
    gdi.SelectObject.argtypes=[w.HDC,w.HGDIOBJ];gdi.SelectObject.restype=w.HGDIOBJ
    gdi.GetDIBits.argtypes=[w.HDC,w.HBITMAP,w.UINT,w.UINT,ctypes.c_void_p,ctypes.c_void_p,w.UINT]
    gdi.DeleteObject.argtypes=[w.HGDIOBJ];gdi.DeleteDC.argtypes=[w.HDC]
    hwnd=user.GetParent(window.winfo_id())
    rect=w.RECT();user.GetWindowRect(hwnd,ctypes.byref(rect))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    dc=user.GetDC(hwnd);memory=gdi.CreateCompatibleDC(dc)
    bitmap=gdi.CreateCompatibleBitmap(dc,width,height)
    old=gdi.SelectObject(memory,bitmap)
    try:
        if not user.PrintWindow(hwnd,memory,2):
            raise OSError('PrintWindow failed')
        gdi.SelectObject(memory,old)
        stride=(width*3+3)&~3
        data=ctypes.create_string_buffer(stride*height)
        header=struct.pack('<IiiHHIIiiII',40,width,height,1,24,0,len(data),0,0,0,0)
        info=ctypes.create_string_buffer(header)
        if not gdi.GetDIBits(dc,bitmap,0,height,data,info,0):
            raise OSError('GetDIBits failed')
        return struct.pack('<2sIHHI',b'BM',54+len(data),0,0,54)+header+data.raw
    finally:
        gdi.SelectObject(memory,old)
        gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,dc)


if __name__=='__main__' and os.name=='nt':
    with tempfile.TemporaryDirectory() as folder,patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
        root=app.App()
        try:
            root.geometry('1280x850+0+0');root.update();time.sleep(.2);root.update()
            Path('ui-previews').mkdir(exist_ok=True)
            for name,zoom,geometry in [('dark',100,'1280x850+0+0'),('compact',150,'900x650+0+0')]:
                root.design.set_zoom(zoom);root.geometry(geometry);root.update();time.sleep(.2);root.update()
                data=capture(root)
                Path(f'ui-previews/{name}.bmp').write_bytes(data)
                print('UI_PREVIEW_'+name+'='+base64.b64encode(zlib.compress(data,9)).decode(),flush=True)
        finally:
            root.destroy()
