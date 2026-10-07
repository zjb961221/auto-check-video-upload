"""Named per-mine API connections, separate from request templates."""
from copy import deepcopy
import hashlib
import json
from api_client import ApiError

DEFAULT = '接口默认连接'


def validate_connections(item):
    connections = item.get('connections', [])
    if not isinstance(connections, list) or len(connections) > 200:
        raise ApiError('connections 必须为最多 200 项的数组')
    ids, names = set(), set()
    for conn in connections:
        if not isinstance(conn, dict) or set(conn) != {'id', 'name', 'profile'}:
            raise ApiError('每个煤矿 API 连接必须包含 id、name、profile')
        if any(not isinstance(conn.get(k), str) or not conn[k].strip() for k in ('id','name')):
            raise ApiError('煤矿 API 连接 id 和 name 不能为空')
        if conn['id'] in ids or conn['name'] in names or conn['name']==DEFAULT:
            raise ApiError('煤矿 API 连接 id 或名称重复')
        if not isinstance(conn['profile'], dict) or not isinstance(conn['profile'].get('base_url'), str) or not conn['profile']['base_url'].strip():
            raise ApiError('煤矿 API profile 必须填写 base_url')
        for key in ('username','password','token'):
            if key in conn['profile'] and not isinstance(conn['profile'][key],str):
                raise ApiError('煤矿 API 账号和密钥必须为文本')
        ids.add(conn['id']);names.add(conn['name'])
    default=item.get('default_connection','')
    if not isinstance(default,str) or default and default not in ids:
        raise ApiError('default_connection 必须为已配置煤矿连接的 id 或空文本')


def resolve_connection(preset, selected=''):
    profile=deepcopy(preset.get('profile',{}))
    service=preset.get('profile_name','默认服务')
    if not selected:
        return profile,service,preset['name']
    conn=next((c for c in preset.get('connections',[]) if c['id']==selected),None)
    if conn is None:
        raise ApiError('选中的煤矿 API 连接不存在，请重新选择')
    for key in ('base_url','username','password','token'):
        profile.pop(key,None)
    profile.update(deepcopy(conn['profile']))
    digest=hashlib.sha256(json.dumps([service,selected,profile],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return profile,'mine-api:'+digest,'mine-draft:'+hashlib.sha256((preset['name']+digest).encode()).hexdigest()
