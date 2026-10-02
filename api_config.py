"""API presets and current-user encrypted connection profiles."""
import json
from pathlib import Path
import re
import tempfile
from api_client import ApiError, METHODS, TOKEN
from settings import protect, unprotect

RESERVED = {'username', 'password', 'password_md5', 'token'}
PUBLIC_FIELDS = ('base_url', 'auth_type', 'username', 'key_name', 'prefix', 'timeout', 'ca_file', 'system_proxy')


def load_api_requests(path):
    try:
        items = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return []
    if not isinstance(items, list):
        raise ApiError('api_requests.json 必须是接口配置数组')
    names = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip() or item['name'] in names:
            raise ApiError('接口名称不能为空或重复')
        names.add(item['name'])
        if not isinstance(item.get('description', ''), str) or not isinstance(item.get('profile', {}), dict):
            raise ApiError('description 必须为文本，profile 必须为对象')
        if not isinstance(item.get('profile_name', '默认服务'), str) or not item.get('profile_name', '默认服务').strip():
            raise ApiError('profile_name 不能为空')
        spec = item.get('request')
        if not isinstance(spec, dict) or spec.get('method', 'GET') not in METHODS or not isinstance(spec.get('path', ''), str):
            raise ApiError('request 必须包含合法的 method 和文本 path')
        if spec.get('body_type', 'none') not in ('none', 'json', 'form', 'raw'):
            raise ApiError('body_type 仅支持 none、json、form、raw')
        if not isinstance(spec.get('query', {}), dict) or not isinstance(spec.get('headers', {}), dict):
            raise ApiError('query、headers 必须为对象')
        params = item.get('params', [])
        if not isinstance(params, list) or len(params) > 8:
            raise ApiError('params 必须为数组，最多 8 个参数')
        keys = set()
        for p in params:
            if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', p['name']) or p['name'] in RESERVED | keys:
                raise ApiError('参数名无效、重复或使用了保留名称')
            keys.add(p['name'])
            p.setdefault('label', p['name'])
            p.setdefault('type', 'text')
            p.setdefault('default', '')
            p.setdefault('required', True)
            p.setdefault('secret', False)
            if p['type'] not in ('text', 'integer', 'boolean') or not isinstance(p['label'], str) or type(p['required']) is not bool or type(p['secret']) is not bool:
                raise ApiError('参数支持 text/integer/boolean，required 和 secret 必须为布尔值')
        needed = set(TOKEN.findall(json.dumps(spec, ensure_ascii=False)))
        if needed - (keys | RESERVED):
            raise ApiError('请求模板引用了未定义的参数')
    return items


def bind_api_parameters(preset, raw):
    values = {}
    for p in preset.get('params', []):
        value = raw.get(p['name'], str(p.get('default', '')))
        if not isinstance(value, str):
            raise ApiError('参数输入必须为文本')
        if p.get('required', True) and not value.strip():
            raise ApiError(f"请填写“{p['label']}”")
        if p.get('type', 'text') == 'integer':
            try:
                value = int(value)
            except ValueError:
                raise ApiError(f"“{p['label']}”必须为整数") from None
        elif p.get('type') == 'boolean':
            if value.strip().lower() not in ('true', 'false'):
                raise ApiError(f"“{p['label']}”请填写 true 或 false")
            value = value.strip().lower() == 'true'
        values[p['name']] = value
    return values


class ProfileStore:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or not isinstance(data.get('profiles', {}), dict):
            raise ApiError('API 连接配置格式错误')
        return data.get('profiles', {})

    def load(self, name):
        record = self.read().get(name)
        if record is None:
            return None
        if not isinstance(record, dict):
            raise ApiError('API 连接配置格式错误')
        if record.get('encrypted'):
            try:
                profile = json.loads(unprotect(record['encrypted']))
                if not isinstance(profile, dict):
                    raise ValueError('profile must be object')
                return dict(profile, _remember_secrets=record.get('remember', True))
            except (OSError, ValueError, TypeError):
                raise ApiError('此 API 配置无法解密，请在原 Windows 账号下打开或重新填写后保存') from None
        profile = record.get('public', {})
        if not isinstance(profile, dict):
            raise ApiError('API 连接配置格式错误')
        return dict(profile, _remember_secrets=False)

    def save(self, name, profile, remember):
        if not name.strip():
            raise ApiError('请填写连接配置名称')
        profiles = self.read()
        record = {'encrypted': protect(json.dumps(profile, ensure_ascii=False))} if remember else {'public': {k: profile.get(k, '') for k in PUBLIC_FIELDS}}
        record['remember'] = bool(remember)
        profiles[name] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'profiles': profiles}, stream, ensure_ascii=False, indent=2)
            temporary.replace(self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
