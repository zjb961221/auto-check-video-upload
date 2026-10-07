"""Deployable mine catalogue and current-user encrypted local overrides."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from settings import FIELDS, protect, unprotect

CONNECTION_FIELDS = FIELDS + ('password',)


class ProfileError(ValueError):
    pass


def load_profiles(path):
    path = Path(path)
    from site_profiles import load_sites
    try:sites, selected = load_sites(path.parent / 'site_profiles.json')
    except (OSError,ValueError,TypeError) as exc:
        from diagnostics import configuration_error
        raise ProfileError(configuration_error('site_profiles.json',exc)) from None
    central = [dict(s['database'], id=s['id'], name=s['name']) for s in sites if 'database' in s]
    try:
        data = dict(version=1, profiles=central, default_profile=selected if selected in {s['id'] for s in central} else '') if central else json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return [], ''
    except (OSError, ValueError):
        raise ProfileError('database_profiles.json 无法读取，请检查 JSON 格式、编码和文件权限；原连接未改变。') from None
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ProfileError('database_profiles.json 必须包含 version: 1 和 profiles 数组')
    if set(data) - {'version', 'profiles', 'default_profile'}:
        raise ProfileError('煤矿配置存在未知顶层字段')
    profiles = data.get('profiles')
    if not isinstance(profiles, list) or len(profiles) > 200:
        raise ProfileError('profiles 必须是最多 200 项的数组')
    ids, names, normalized = set(), set(), []
    for index, item in enumerate(profiles, 1):
        if not isinstance(item, dict) or set(item) - set(CONNECTION_FIELDS) - {'id', 'name', 'password_env'}:
            raise ProfileError(f'第 {index} 项煤矿配置格式错误或存在未知字段')
        profile = deepcopy(item)
        pid, name = profile.get('id'), profile.get('name')
        if not isinstance(pid, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', pid) or pid in ids:
            raise ProfileError(f'第 {index} 项 id 无效或重复，请使用英文、数字、下划线或短横线')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or name.strip() in names:
            raise ProfileError(f'第 {index} 项煤矿名称为空、重复或过长')
        profile['name'] = name.strip()
        ids.add(pid); names.add(profile['name'])
        for key in ('host', 'database', 'user'):
            if not isinstance(profile.get(key), str) or not profile[key].strip():
                raise ProfileError(f'第 {index} 项必须填写 {key}')
            profile[key] = profile[key].strip()
        port = profile.get('port', '3306')
        if type(port) not in (str, int) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ProfileError(f'第 {index} 项端口必须为 1–65535')
        profile['port'] = str(int(port))
        for key in ('password', 'ssl_ca', 'password_env'):
            if key in profile and not isinstance(profile[key], str):
                raise ProfileError(f'第 {index} 项 {key} 必须为文本')
        if 'password' in profile and 'password_env' in profile:
            raise ProfileError(f'第 {index} 项 password 与 password_env 只能选一个')
        if 'password_env' in profile and not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', profile['password_env']):
            raise ProfileError(f'第 {index} 项 password_env 必须为环境变量名称')
        if profile.get('ssl_ca') and not Path(profile['ssl_ca']).is_absolute():
            profile['ssl_ca'] = str((path.parent / profile['ssl_ca']).resolve())
        profile.setdefault('ssl_ca', '')
        normalized.append(profile)
    default = data.get('default_profile', '')
    if not isinstance(default, str) or default and default not in ids:
        raise ProfileError('default_profile 必须为空或为已配置的煤矿 id')
    return normalized, default


def fingerprint(profile):
    # Stored inside the encrypted payload when secrets are remembered.
    return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resolve_profile(profile):
    config = {key: str(profile.get(key, '')) for key in CONNECTION_FIELDS}
    warning = ''
    if 'password_env' in profile:
        value = os.environ.get(profile['password_env'])
        if value is None:
            warning = '该煤矿密码环境变量未设置，请手动填写密码或保存本机煤矿连接。'
        config['password'] = value or ''
    return config, warning


class LocalProfiles:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return dict(version=1, profiles={})
        except (OSError, ValueError):
            raise ProfileError('本机煤矿连接文件无法读取，请检查文件或恢复备份；不会回填其他煤矿密码。') from None
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('profiles'), dict):
            raise ProfileError('本机煤矿连接文件格式错误')
        return data

    def load(self, profile):
        config, warning = resolve_profile(profile)
        record = self.read()['profiles'].get(profile['id'])
        if record is None:
            return config, warning
        try:
            if not isinstance(record, dict):
                raise ValueError()
            payload = json.loads(unprotect(record['encrypted'])) if record.get('encrypted') else record['public']
            if not isinstance(payload, dict) or not isinstance(payload.get('config'), dict):
                raise ValueError()
            if payload.get('fingerprint') != fingerprint(profile):
                return config, '煤矿文件已变化，旧本机覆盖配置不再使用；已回填新文件配置，请核对并重新保存。'
            values = payload['config']
            if any(not isinstance(values.get(k, ''), str) for k in CONNECTION_FIELDS):
                raise ValueError()
            return {k: values.get(k, '') for k in CONNECTION_FIELDS}, ''
        except (OSError, ValueError, TypeError, KeyError):
            config['password'] = ''  # Never keep the previous mine's password or silently fall back.
            return config, '该煤矿本机配置无法解密或损坏，已清空密码；请重新填写并保存。'

    def save(self, profile, config, remember):
        data = self.read()
        values = {key: str(config.get(key, '')) for key in CONNECTION_FIELDS}
        if not remember:
            values['password'] = ''
        payload = dict(fingerprint=fingerprint(profile), config=values)
        record = dict(encrypted=protect(json.dumps(payload, ensure_ascii=False))) if remember else dict(public=payload)
        data['profiles'][profile['id']] = record
        data['selected_id'] = profile['id']
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
            temporary.replace(self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
