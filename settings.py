"""Local connection preferences; passwords use current-user Windows DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

FIELDS = ('host', 'port', 'database', 'user', 'ssl_ca')


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('保存密码仅支持 Windows；可取消“记住密码”后保存其他信息。')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    # UI_FORBIDDEN only: deliberately omit LOCAL_MACHINE (current-user scope).
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Windows 密码加密/解密失败，请重新输入密码后保存。')
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect(password):
    return base64.b64encode(_crypt(password.encode('utf-8'))).decode('ascii')


def unprotect(value):
    return _crypt(base64.b64decode(value, validate=True), decrypt=True).decode('utf-8')


def load_settings(path):
    """Return preferences and a recoverable password warning. Legacy files work."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}, ''
    if not isinstance(data, dict):
        raise ValueError('连接配置格式错误')
    result = {key: str(data[key]) for key in FIELDS if key in data}
    result['remember_password'] = data.get('remember_password', True) is True
    result['password'] = ''
    warning = ''
    if result['remember_password'] and data.get('password_dpapi'):
        try:
            result['password'] = unprotect(data['password_dpapi'])
        except (OSError, ValueError, TypeError):
            warning = '已恢复连接信息，但保存的密码无法解密。请在当前 Windows 账号下重新输入密码并保存。'
    return result, warning


def save_settings(path, config, remember):
    """Encrypt before writing and atomically replace; never persist plaintext."""
    path = Path(path)
    data = {key: config.get(key, '') for key in FIELDS}
    data['remember_password'] = bool(remember)
    if remember:
        data['password_dpapi'] = protect(config.get('password', ''))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='connection-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
