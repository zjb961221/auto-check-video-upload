"""Declarative guided workflows and a side-effect-free progression model."""
from copy import deepcopy
import json
from pathlib import Path
from queries import load_queries
from updates import load_updates
from api_config import load_api_requests
from diagnostics import configuration_error
from api_client import http_error_reason
from parameter_choices import validate_choice


class WorkflowError(ValueError):
    pass


def load_workflows(path):
    try:
        return _load_workflows(path)
    except (OSError, ValueError, TypeError) as exc:
        raise WorkflowError(configuration_error(path, exc)) from None


def _load_workflows(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1 or type(data.get('version')) is not int:
        raise WorkflowError('workflows.json 必须包含 version: 1 和 workflows 数组')
    flows = data.get('workflows')
    if not isinstance(flows, list) or not flows:
        raise WorkflowError('workflows 必须是非空数组')
    catalogues, ids = {}, set()
    loaders = {'query': (load_queries, 'queries.json'), 'update': (load_updates, 'updates.json'),
               'delete': (load_updates, 'updates.json'), 'api': (load_api_requests, 'api_requests.json')}
    for flow in flows:
        if not isinstance(flow, dict):
            raise WorkflowError('每个流程必须是对象')
        fid = flow.get('id')
        if not isinstance(fid, str) or not fid.strip() or fid in ids:
            raise WorkflowError('流程 id 不能为空或重复')
        ids.add(fid)
        if not isinstance(flow.get('name'), str) or not flow['name'].strip():
            raise WorkflowError(f'流程 {fid} 缺少 name')
        if not isinstance(flow.get('description', ''), str):
            raise WorkflowError(f'流程 {fid} 的 description 必须为文本')
        steps = flow.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise WorkflowError(f'流程 {fid} 必须包含 1–100 个步骤')
        step_ids = set()
        for number, step in enumerate(steps, 1):
            context = f'流程 {fid} 第 {number} 步'
            if not isinstance(step, dict):
                raise WorkflowError(context + '必须为对象')
            allowed = {'id', 'title', 'type', 'instructions', 'checklist', 'optional', 'ref', 'defaults', 'success'}
            if set(step) - allowed:
                raise WorkflowError(context + '存在未知字段：' + ', '.join(sorted(set(step) - allowed)))
            sid = step.get('id')
            if not isinstance(sid, str) or not sid.strip() or sid in step_ids:
                raise WorkflowError(context + '的 id 不能为空或重复')
            step_ids.add(sid)
            if not isinstance(step.get('title'), str) or not step['title'].strip():
                raise WorkflowError(context + '缺少 title')
            kind = step.get('type')
            if kind not in ('note', 'query', 'update', 'delete', 'api'):
                raise WorkflowError(context + '的 type 仅支持 note/query/update/delete/api')
            if not isinstance(step.get('instructions', ''), str) or type(step.get('optional', False)) is not bool:
                raise WorkflowError(context + '的说明或 optional 格式错误')
            checks = step.get('checklist', [])
            if not isinstance(checks, list) or len(checks) > 30 or any(not isinstance(x, str) or not x.strip() for x in checks):
                raise WorkflowError(context + '的 checklist 必须是最多 30 项的非空文本数组')
            defaults = step.get('defaults', {})
            if not isinstance(defaults, dict) or any(not isinstance(v, str) for v in defaults.values()):
                raise WorkflowError(context + '的 defaults 必须是参数名到文本值的对象')
            if kind == 'note':
                if any(k in step for k in ('ref', 'defaults', 'success')):
                    raise WorkflowError(context + '说明步骤不能配置操作字段')
                continue
            if checks:
                raise WorkflowError(context + '的 checklist 仅用于 note 步骤')
            if not isinstance(step.get('ref'), str):
                raise WorkflowError(context + '必须用 ref 引用操作名称')
            if kind not in catalogues:
                loader, filename = loaders[kind]
                try:
                    operations = loader(path.parent / filename)
                except (OSError, ValueError, TypeError) as exc:
                    raise WorkflowError(context + '：' + configuration_error(filename, exc)) from None
                if len({x['name'] for x in operations}) != len(operations):
                    raise WorkflowError(filename + '存在重复名称，无法引用')
                catalogues[kind] = {x['name']: x for x in operations}
            if step['ref'] not in catalogues[kind]:
                raise WorkflowError(context + f'引用不存在：{step["ref"]}')
            step['operation'] = deepcopy(catalogues[kind][step['ref']])
            if kind in ('update', 'delete') and step['operation']['compiled'].get('kind', 'update') != kind:
                raise WorkflowError(context + '的步骤类型与 SQL 不一致：DELETE 使用 type: delete，UPDATE 使用 type: update')
            if set(defaults) - {p['name'] for p in step['operation'].get('params', [])}:
                raise WorkflowError(context + '的 defaults 包含未定义参数')
            if kind == 'query':
                for param in step['operation'].get('params', []):
                    if param['name'] in defaults:
                        try:
                            validate_choice(param, defaults[param['name']])
                        except ValueError as exc:
                            raise WorkflowError(context + '：defaults 必须填写已配置的选项 value') from exc
            if 'success' in step:
                rule = step['success']
                if kind != 'api' or not isinstance(rule, dict) or set(rule) != {'path', 'equals'} or not isinstance(rule['path'], str) or not rule['path']:
                    raise WorkflowError(context + '的 success 必须为 API 的 {path, equals} 判断规则')
    return flows


class WorkflowRun:
    """No automatic execution, replay or persistence of write completion."""
    def __init__(self, flow):
        self.flow = deepcopy(flow)
        self.index = 0
        self.states = ['pending'] * len(flow['steps'])

    @property
    def step(self):
        return self.flow['steps'][self.index]

    def invalidate(self):
        # Uncertain writes must be verified explicitly, not unlocked by editing.
        if self.states[self.index] != 'uncertain':
            self.states[self.index] = 'pending'
        for i in range(self.index + 1, len(self.states)):
            if self.states[i] not in ('pending', 'uncertain'):
                self.states[i] = 'stale'

    def ready(self):
        if self.states[self.index] == 'uncertain':
            raise WorkflowError('请先核实结果不确定的操作')
        self.states[self.index] = 'ready'

    def complete(self):
        if any(s not in ('done', 'skipped') for s in self.states[:self.index]):
            raise WorkflowError('前面步骤的输入或结果已变化，请先返回核对')
        if self.states[self.index] not in ('ready', 'done', 'skipped'):
            raise WorkflowError('请先完成本步骤并核对结果')
        if self.states[self.index] != 'skipped':
            self.states[self.index] = 'done'

    def skip(self):
        if not self.step.get('optional') or self.states[self.index] == 'uncertain':
            raise WorkflowError('本步骤不能跳过')
        self.states[self.index] = 'skipped'

    def go(self, target):
        if not 0 <= target < len(self.states):
            raise WorkflowError('步骤不存在')
        if target > self.index and any(s not in ('done', 'skipped') for s in self.states[:target]):
            raise WorkflowError('请按顺序完成前面的步骤')
        self.index = target


def api_outcome(response, rule=None):
    """Return success plus customer-safe status, without exposing raw values."""
    if not 200 <= response.status < 300:
        return False, http_error_reason(response.status)
    if rule:
        try:
            value = json.loads(response.text())
            for key in rule['path'].split('.'):
                value = value[int(key)] if isinstance(value, list) else value[key]
        except (ValueError, KeyError, TypeError, IndexError):
            return False, '响应中无法读取配置的业务成功字段，本步骤未完成。'
        if type(value) is not type(rule['equals']) or value != rule['equals']:
            return False, 'HTTP 请求已返回，但业务成功条件未满足。请核对服务端结果后再操作。'
        return True, 'HTTP 与业务成功条件均已满足，请核对结果后继续。'
    return True, 'HTTP 请求成功；未配置业务成功规则，必须人工核对响应后再继续。'
