"""One deployable inventory for database and named API services."""
import json
from pathlib import Path
from api_client import validate_profile


def load_sites(path):
    path=Path(path)
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return [],''
    if not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1 or set(data)-{'version','sites','default_site'}:
        raise ValueError('site_profiles.json 必须包含 version: 1 和 sites 数组')
    sites=data.get('sites');ids=set();names=set()
    if not isinstance(sites,list) or len(sites)>200:raise ValueError('sites 必须为最多 200 项的数组')
    for i,site in enumerate(sites,1):
        if not isinstance(site,dict) or set(site)-{'id','name','database','services'}:raise ValueError(f'第 {i} 项煤矿字段无效')
        import re
        if not isinstance(site.get('id'),str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',site['id']) or site['id'] in ids:raise ValueError(f'第 {i} 项煤矿 id 无效或重复')
        if not isinstance(site.get('name'),str) or not site['name'].strip() or site['name'] in names:raise ValueError(f'第 {i} 项煤矿名称无效或重复')
        ids.add(site['id']);names.add(site['name'])
        if 'database' in site and not isinstance(site['database'],dict):raise ValueError(f'第 {i} 项 database 必须为对象')
        services=site.get('services',{})
        if not isinstance(services,dict) or any(not isinstance(k,str) or not k.strip() or not isinstance(v,dict) for k,v in services.items()):raise ValueError(f'第 {i} 项 services 必须为服务名到连接的对象')
        for profile in services.values():
            try:validate_profile(profile)
            except (ValueError,TypeError):raise ValueError(f'第 {i} 项 API 服务地址或鉴权配置无效') from None
    default=data.get('default_site','')
    if not isinstance(default,str) or default and default not in ids:raise ValueError('default_site 必须引用已有煤矿 id')
    return sites,default


def apply_site_service(item,path):
    service=item.get('site_service')
    if service is None:return
    if not isinstance(service,str) or not service.strip():raise ValueError('site_service 必须为非空服务名')
    if item.get('connections'):raise ValueError('使用 site_service 时请移除接口中重复的 connections')
    sites,default=load_sites(Path(path).parent/'site_profiles.json')
    connections=[dict(id=s['id'],name=s['name'],profile=s['services'][service]) for s in sites if service in s.get('services',{})]
    if not connections:raise ValueError('site_service 在统一煤矿文件中没有对应服务')
    item['connections']=connections
    item.setdefault('default_connection',default if default in {c['id'] for c in connections} else '')
