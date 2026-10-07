"""Configuration validation, encrypted backups and minimal audit/support data."""
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import threading
from uuid import uuid4
from settings import protect,unprotect

CONFIG_FILES=('site_profiles.json','database_profiles.json','queries.json','updates.json','api_requests.json','workflows.json','ops_settings.json')


def load_ops(path):
    try:data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return dict(customer_mode=False,delete_confirm_threshold=20)
    if not isinstance(data,dict) or data.get('version')!=1 or set(data)-{'version','customer_mode','delete_confirm_threshold'}:raise ValueError('ops_settings.json 格式错误')
    mode=data.get('customer_mode',False);limit=data.get('delete_confirm_threshold',20)
    if type(mode) is not bool or type(limit) is not int or not 1<=limit<=1000:raise ValueError('客户模式或二次删除确认阈值格式错误')
    return dict(customer_mode=mode,delete_confirm_threshold=limit)


def check_configs(folder):
    from queries import load_queries
    from updates import load_updates
    from api_config import load_api_requests
    from database_profiles import load_profiles
    from site_profiles import load_sites
    from workflows import load_workflows
    from diagnostics import configuration_error
    results=[]
    for name,loader in [('site_profiles.json',load_sites),('database_profiles.json',load_profiles),('queries.json',load_queries),('updates.json',load_updates),('api_requests.json',load_api_requests),('workflows.json',load_workflows),('ops_settings.json',load_ops)]:
        try:
            loader(Path(folder)/name)
            results.append(dict(file=name,ok=True,detail='校验通过'))
        except (OSError,ValueError,TypeError) as exc:
            # Schema validators may mention business names; UI only, never included in support exports.
            results.append(dict(file=name,ok=False,detail=configuration_error(name,exc)))
    return results


def atomic_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(text)
        temporary.replace(path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def atomic_bytes(path,data):
    path=Path(path);temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(data)
        temporary.replace(path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


def backup_configs(folder,destination):
    folder=Path(folder)
    files={name:(folder/name).read_text(encoding='utf-8-sig') for name in CONFIG_FILES if (folder/name).is_file()}
    payload=json.dumps(dict(version=1,files=files),ensure_ascii=False)
    atomic_text(destination,json.dumps(dict(version=1,encrypted=protect(payload)),ensure_ascii=False))


def restore_configs(folder,source):
    wrapper=json.loads(Path(source).read_text(encoding='utf-8'))
    payload=json.loads(unprotect(wrapper['encrypted']))
    if payload.get('version')!=1 or not isinstance(payload.get('files'),dict) or set(payload['files'])-set(CONFIG_FILES) or any(not isinstance(v,str) for v in payload['files'].values()):raise ValueError('备份内容无效')
    folder=Path(folder)
    with tempfile.TemporaryDirectory() as staging:
        stage=Path(staging)
        for name in CONFIG_FILES:
            if (folder/name).is_file():(stage/name).write_bytes((folder/name).read_bytes())
        for name,value in payload['files'].items():(stage/name).write_text(value,encoding='utf-8')
        if any(not r['ok'] for r in check_configs(stage)):raise ValueError('备份配置校验失败，原配置未更改')
    previous={name:(folder/name).read_bytes() if (folder/name).exists() else None for name in payload['files']}
    changed=[]
    try:
        for name,value in payload['files'].items():
            changed.append(name);atomic_text(folder/name,value)
    except OSError:
        for name in reversed(changed):
            if previous[name] is None:(folder/name).unlink(missing_ok=True)
            else:atomic_bytes(folder/name,previous[name])
        raise


class Audit:
    def __init__(self,folder):
        self.path=Path(folder)/'operations.jsonl';self.lock=threading.Lock();self.warning=''
    def record(self,event,status,rows=None):
        if event not in ('query','preview','apply','api','login','verify','connection','configuration'):event='operation'
        if status not in ('ok','error','failed','uncertain','started'):status='unknown'
        record=dict(time=datetime.now(timezone.utc).isoformat(),id=uuid4().hex[:12],event=event,status=status)
        if type(rows) is int:record['rows']=rows
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.exists() and self.path.stat().st_size>512000:
                    self.path.with_suffix('.previous.jsonl').unlink(missing_ok=True);self.path.replace(self.path.with_suffix('.previous.jsonl'))
                with self.path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(record)+'\n')
        except OSError:self.warning='操作记录无法写入，请检查本机目录权限。'
    def read(self):
        try:lines=self.path.read_text(encoding='utf-8').splitlines()[-500:]
        except FileNotFoundError:return []
        import re
        records=[]
        for line in lines:
            try:row=json.loads(line)
            except ValueError:
                self.warning='部分操作记录不完整，已跳过损坏条目。';continue
            if not isinstance(row,dict) or row.get('event') not in ('query','preview','apply','api','login','verify','connection','configuration','operation') or row.get('status') not in ('ok','error','failed','uncertain','started','unknown'):continue
            if not re.fullmatch('[a-f0-9]{12}',str(row.get('id',''))) or not re.fullmatch('[0-9T:+.Z-]{1,40}',str(row.get('time',''))):continue
            safe={key:row[key] for key in ('time','id','event','status')}
            if type(row.get('rows')) is int:safe['rows']=row['rows']
            records.append(safe)
        return records


def export_support(folder,audit,destination):
    from diagnostics import VERSION
    # No raw configuration, exception text, URL, response, SQL, credentials or log file.
    checks=[dict(file=r['file'],ok=r['ok']) for r in check_configs(folder)]
    data=dict(version=VERSION,configuration_checks=checks,events=audit.read(),audit_warning=bool(audit.warning))
    atomic_text(destination,json.dumps(data,ensure_ascii=False,indent=2))
