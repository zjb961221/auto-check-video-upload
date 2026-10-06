"""Validated query catalogue and typed parameters, independent of the UI."""
from datetime import datetime
import json
from pathlib import Path
import re
from parameter_choices import normalize_options, validate_choice

MAX_ROWS = 2000
MAX_PARAMS = 8
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
PLACEHOLDER = re.compile(r'%\(([A-Za-z_][A-Za-z0-9_]*)\)s')


def sql_structure(sql):
    """Mask quoted text; reject comments and multiple statements conservatively.

    This is a restricted SELECT configuration validator, not an authorization
    boundary. A dedicated SELECT-only database account is still required.
    Backslash escapes in SQL literals are rejected to avoid sql_mode ambiguity;
    use bound parameters for values containing backslashes instead.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError('SQL 不能为空')
    sql = sql.strip().removesuffix(';').rstrip()
    mask = list(sql)
    i = 0
    while i < len(sql):
        if sql[i] in "'\"`":
            quote, start = sql[i], i
            i += 1
            while i < len(sql):
                if sql[i] == '\\':
                    raise ValueError('SQL 字面量中不支持反斜杠转义，请使用绑定参数')
                if sql[i] == quote:
                    if i + 1 < len(sql) and sql[i+1] == quote:
                        i += 2
                        continue
                    break
                i += 1
            if i == len(sql):
                raise ValueError('SQL 引号未闭合')
            mask[start:i+1] = ' ' * (i+1-start)
        elif sql[i] == ';' or sql.startswith(('--', '/*', '#'), i):
            raise ValueError('仅支持单条 SELECT，不支持 SQL 注释或多语句')
        i += 1
    structure = ''.join(mask)
    if not re.match(r'^SELECT\b', structure, re.I):
        raise ValueError('仅支持 SELECT 查询')
    if re.search(r'\b(INTO|OUTFILE|DUMPFILE|PROCEDURE|FOR\s+UPDATE|FOR\s+SHARE|LOCK\s+IN\s+SHARE)\b|:=', structure, re.I):
        raise ValueError('不支持写文件、变量赋值或加锁查询')
    depth, limits = 0, []
    for token in re.finditer(r'\(|\)|\bLIMIT\b', structure, re.I):
        if token[0] == '(':
            depth += 1
        elif token[0] == ')':
            depth -= 1
            if depth < 0:
                raise ValueError('SQL 括号不匹配')
        elif depth == 0:
            limits.append(token.start())
    if depth:
        raise ValueError('SQL 括号不匹配')
    if len(limits) > 1:
        raise ValueError('仅支持一个最外层 LIMIT')
    return sql, structure, limits


def bounded_sql(sql):
    """Preserve original SELECT/ORDER BY; cap its outer LIMIT, never wrap it."""
    sql, structure, limits = sql_structure(sql)
    cap = MAX_ROWS + 1
    if not limits:
        return f'{sql} LIMIT {cap}'
    start = limits[0]
    match = re.fullmatch(r'LIMIT\s+(\d+)\s*(?:(,|OFFSET)\s*(\d+)\s*)?', structure[start:], re.I)
    if not match:
        raise ValueError('最外层 LIMIT 仅支持非负整数，例如 LIMIT 100 或 LIMIT 100 OFFSET 20')
    first, operator, second = match.groups()
    if operator == ',':
        suffix = f'LIMIT {min(int(second), cap)} OFFSET {int(first)}'
    else:
        suffix = f'LIMIT {min(int(first), cap)}' + (f' OFFSET {int(second)}' if second else '')
    return sql[:start] + suffix


def load_queries(path):
    items = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(items, list) or not items:
        raise ValueError('queries.json 必须是非空查询列表')
    seen = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f'第 {index} 项必须是查询对象')
        name = item.get('name')
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ValueError('查询名称不能为空或重复')
        seen.add(name)
        if not isinstance(item.get('description', ''), str):
            raise ValueError(f'{name}：description 必须为文本')
        sql, structure, _ = sql_structure(item.get('sql'))
        bounded_sql(sql)
        params = item.get('params', [])
        if not isinstance(params, list) or len(params) > MAX_PARAMS:
            raise ValueError(f'{name}：params 必须为列表，最多 {MAX_PARAMS} 个参数')
        normalized = []
        for param in params:
            spec = {'name': param} if isinstance(param, str) else dict(param) if isinstance(param, dict) else {}
            key = spec.get('name')
            if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
                raise ValueError(f'{name}：参数名必须是英文标识符')
            spec.setdefault('label', key)
            spec.setdefault('type', 'text')
            spec.setdefault('default', '')
            if spec['type'] not in ('text', 'integer', 'datetime') or not isinstance(spec['label'], str):
                raise ValueError(f'{name}：参数类型或标签无效')
            if not isinstance(spec['default'], (str, int)) or isinstance(spec['default'], bool):
                raise ValueError(f'{name}：参数默认值必须是文本或整数')
            normalize_options(spec)
            normalized.append(spec)
        names = [p['name'] for p in normalized]
        if len(set(names)) != len(names) or set(PLACEHOLDER.findall(sql)) != set(names):
            raise ValueError(f'{name}：SQL 占位符与 params 不一致')
        # A placeholder inside quotes would be double-quoted by the driver.
        if PLACEHOLDER.findall(sql) != PLACEHOLDER.findall(structure):
            raise ValueError(f'{name}：参数占位符外不能加引号')
        ranges = item.get('date_ranges', [])
        if not isinstance(ranges, list):
            raise ValueError(f'{name}：date_ranges 必须为列表')
        types = {p['name']: p['type'] for p in normalized}
        for pair in ranges:
            if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(k, str) or types.get(k) != 'datetime' for k in pair):
                raise ValueError(f'{name}：时间范围必须引用两个 datetime 参数')
        item.update(sql=sql, params=normalized, date_ranges=ranges)
    return items


def bind_parameters(query, raw):
    values = {}
    for spec in query['params']:
        name, label = spec['name'], spec['label']
        value = raw.get(name, '')
        validate_choice(spec, value)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'请填写“{label}”')
        if len(value) > 4096:
            raise ValueError(f'“{label}”内容过长（最多 4096 字符）')
        if spec['type'] == 'integer':
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f'“{label}”必须是整数') from None
        elif spec['type'] == 'datetime':
            try:
                value = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                raise ValueError(f'“{label}”格式应为 2026-09-28 08:00:00') from None
        values[name] = value
    for start, end in query.get('date_ranges', []):
        if values[start] >= values[end]:
            raise ValueError('开始时间必须早于结束时间')
    return values
