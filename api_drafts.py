"""Per-interface drafts: explicit encrypted persistence, no network side effects."""
import hashlib
import json
from copy import deepcopy
from api_config import ProfileStore
from api_client import ApiError


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Drafts:
    def __init__(self, path):
        self.store = ProfileStore(path)
        self.values = {}
        self.baselines = {}

    def open(self, name, default):
        if name not in self.values:
            stored = self.store.load(name)
            value = default if stored is None else stored.get('draft')
            if not isinstance(value, dict) or set(value) != set(default):
                raise ApiError('接口草稿格式不兼容，请使用原始接口配置')
            for key in default:
                if type(value[key]) is not type(default[key]):
                    raise ApiError('接口草稿格式无效')
            for key in ('fields', 'parameters'):
                if not all(isinstance(k, str) and isinstance(v, str) for k, v in value[key].items()):
                    raise ApiError('接口草稿字段必须为文本')
            self.values[name] = deepcopy(value)
            self.baselines[name] = fingerprint(value)
        return deepcopy(self.values[name])

    def capture(self, name, value):
        self.values[name] = deepcopy(value)

    def dirty(self):
        return any(fingerprint(value) != self.baselines.get(name) for name, value in self.values.items())

    def save(self, name, value):
        # Draft JSON and parameter values may contain arbitrary secrets. Never plaintext.
        self.store.save(name, {'draft': value}, True)
        self.capture(name, value)
        self.baselines[name] = fingerprint(value)
