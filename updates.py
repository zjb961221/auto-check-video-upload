"""Restricted parameterized UPDATEs with preview, optimistic checks and transactions."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
from database import connect_database
from queries import bind_parameters

IDENT = r'(?:`[A-Za-z_0-9]+`|[A-Za-z_][A-Za-z_0-9]*)'
PARAM = r'%\(([A-Za-z_][A-Za-z_0-9]*)\)s'
PREVIEW_TTL = 300


class UpdateError(ValueError):
    pass


class CommitUncertain(UpdateError):
    pass


def quote(name):
    return '`' + name.replace('`', '``') + '`'


def parse_update(sql):
    if not isinstance(sql, str):
        raise UpdateError('更新 SQL 必须为文本')
    sql = sql.strip().removesuffix(';').rstrip()
    match = re.fullmatch(rf'UPDATE\s+({IDENT})\s+SET\s+(.+?)\s+WHERE\s+(.+)', sql, re.I | re.S)
    if not match:
        raise UpdateError('仅支持 UPDATE 单表 SET 字段=%(参数)s WHERE 条件，不支持多表或无 WHERE 更新')
    table, assignments, where = match.groups()
    changes = []
    for part in assignments.split(','):
        item = re.fullmatch(rf'\s*({IDENT})\s*=\s*{PARAM}\s*', part)
        if not item:
            raise UpdateError('SET 每项必须为 字段=%(参数)s；不支持表达式、子查询或直接写入字面量')
        column, parameter = item.groups()
        changes.append((column.strip('`'), parameter))
    if len({c.lower() for c, _ in changes}) != len(changes):
        raise UpdateError('SET 中不能重复设置同一个字段')
    conditions = []
    for part in re.split(r'\s+AND\s+', where, flags=re.I):
        item = re.fullmatch(rf'\s*({IDENT})\s*(=|<>|!=|<=|>=|<|>|LIKE)\s*{PARAM}\s*', part, re.I)
        if not item:
            raise UpdateError('WHERE 仅支持 字段 比较符 %(参数)s，多条件用 AND；不支持 OR、注释、函数或多语句')
        column, operator, parameter = item.groups()
        conditions.append((column.strip('`'), operator.upper(), parameter))
    return dict(table=table.strip('`'), changes=changes, conditions=conditions,
                set_sql=', '.join(f'{quote(c)}=%({p})s' for c, p in changes),
                where_sql=' AND '.join(f'{quote(c)} {op} %({p})s' for c, op, p in conditions))


def load_updates(path):
    path = Path(path)
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(items, list):
        raise UpdateError('updates.json 必须为更新操作数组')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip() or item['name'] in seen:
            raise UpdateError('更新操作名称不能为空或重复')
        seen.add(item['name'])
        item['compiled'] = parse_update(item.get('sql'))
        limit = item.get('max_rows', 1)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise UpdateError('max_rows 必须为 1–100 的整数，默认 1')
        item['max_rows'] = limit
        # Reuse catalogue validation without interpreting UPDATE as SELECT.
        validate_parameter_spec(item)
    return items


def validate_parameter_spec(item):
    from queries import IDENTIFIER
    params = item.get('params', [])
    if not isinstance(params, list) or not 1 <= len(params) <= 8:
        raise UpdateError('更新操作需要 1–8 个参数')
    normalized = []
    for value in params:
        p = {'name': value} if isinstance(value, str) else dict(value) if isinstance(value, dict) else {}
        name = p.get('name')
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise UpdateError('参数名必须为英文标识符')
        p.setdefault('label', name)
        p.setdefault('type', 'text')
        p.setdefault('default', '')
        if p['type'] not in ('text', 'integer', 'datetime') or not isinstance(p['label'], str) or not isinstance(p['default'], (str, int)):
            raise UpdateError('参数仅支持 text、integer、datetime，标签必须为文本')
        normalized.append(p)
    names = [p['name'] for p in normalized]
    compiled = item['compiled']
    required = {p for _, p in compiled['changes']} | {p for _, _, p in compiled['conditions']}
    if len(names) != len(set(names)) or required != set(names):
        raise UpdateError('更新 SQL 占位符与 params 不一致')
    if not isinstance(item.get('description', ''), str):
        raise UpdateError('description 必须为文本')
    if item.get('date_ranges'):
        raise UpdateError('更新配置暂不支持 date_ranges')
    item['params'] = normalized
    item['date_ranges'] = []


@dataclass(frozen=True)
class Preview:
    operation: dict = field(repr=False)
    params: dict = field(repr=False)
    config: dict = field(repr=False)
    metadata: tuple = field(repr=False)
    columns: tuple
    rows: tuple = field(repr=False)
    created: float


def inspect_table(cursor, operation):
    table = operation['compiled']['table']
    cursor.execute('SELECT @@server_uuid, DATABASE()')
    identity = tuple(cursor.fetchone())
    cursor.execute('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', (table,))
    row = cursor.fetchone()
    if not row or row[0] != 'InnoDB':
        raise UpdateError('更新仅支持当前数据库中存在的 InnoDB 实体表')
    cursor.execute('SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, EXTRA, COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION', (table,))
    schema = tuple(tuple(r) for r in cursor.fetchall())
    cursor.execute("SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION", (table,))
    keys = tuple(r[0] for r in cursor.fetchall())
    if not keys:
        raise UpdateError('该表没有主键，无法可靠定位预览记录；请先由管理员确认表结构')
    columns = {r[0] for r in schema}
    changes = {c for c, _ in operation['compiled']['changes']}
    conditions = {c for c, _, _ in operation['compiled']['conditions']}
    if not (changes | conditions) <= columns:
        raise UpdateError('SQL 中存在不匹配的字段名，请使用数据库实际字段名及大小写')
    if changes & set(keys):
        raise UpdateError('不允许修改主键字段')
    if changes & {r[0] for r in schema if any(flag in r[3].upper() for flag in ('VIRTUAL GENERATED', 'STORED GENERATED'))}:
        raise UpdateError('不允许修改生成列')
    return identity, schema, keys


def select_rows(cursor, operation, params, metadata, lock=False):
    compiled = operation['compiled']
    columns = tuple(r[0] for r in metadata[1])
    sql = (f"SELECT {', '.join(quote(c) for c in columns)} FROM {quote(compiled['table'])} "
           f"WHERE {compiled['where_sql']} ORDER BY {', '.join(quote(k) for k in metadata[2])} "
           f"LIMIT {operation['max_rows'] + 1}" + (' FOR UPDATE' if lock else ''))
    cursor.execute(sql, params)
    rows = tuple(tuple(r) for r in cursor.fetchall())
    if len(rows) > operation['max_rows']:
        raise UpdateError(f"匹配记录超过上限 {operation['max_rows']} 行，已拒绝更新；请缩小 WHERE 范围")
    return columns, rows


def preview_update(config, operation, raw_params, connect=None):
    operation = deepcopy(operation)
    operation['compiled'] = parse_update(operation['sql'])
    params = bind_parameters(operation, raw_params)
    conn = connect_database(config, connect)
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=30000')
            cur.execute('START TRANSACTION READ ONLY')
            metadata = inspect_table(cur, operation)
            columns, rows = select_rows(cur, operation, params, metadata)
            return Preview(operation, deepcopy(params), deepcopy(config), metadata, columns, rows, time.monotonic())
    finally:
        conn.close()


def apply_update(preview, connect=None):
    if time.monotonic() - preview.created > PREVIEW_TTL:
        raise UpdateError('预览已超过 5 分钟，请重新预览')
    if not preview.rows:
        raise UpdateError('没有匹配记录，未执行更新')
    conn = connect_database(preview.config, connect)
    committing = False
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION innodb_lock_wait_timeout=10')
            cur.execute('SET SESSION MAX_EXECUTION_TIME=30000')
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute('START TRANSACTION')
            metadata = inspect_table(cur, preview.operation)
            if metadata != preview.metadata:
                raise UpdateError('数据库或表结构已变化，请重新预览')
            columns, rows = select_rows(cur, preview.operation, preview.params, metadata, lock=True)
            if columns != preview.columns or rows != preview.rows:
                raise UpdateError('记录自预览后已发生变化，请重新预览，避免覆盖他人修改')
            compiled = preview.operation['compiled']
            key_positions = [columns.index(k) for k in metadata[2]]
            # Execute only locked, previewed primary keys; never re-evaluate a broad WHERE for writes.
            sql = (f"UPDATE {quote(compiled['table'])} SET " +
                   ', '.join(f'{quote(c)}=%s' for c, _ in compiled['changes']) +
                   ' WHERE ' + ' AND '.join(f'{quote(k)}=%s' for k in metadata[2]))
            values = tuple(preview.params[p] for _, p in compiled['changes'])
            changed = 0
            for row in rows:
                cur.execute(sql, values + tuple(row[i] for i in key_positions))
                if cur.rowcount not in (0, 1):
                    raise UpdateError('主键更新影响数量异常，已停止提交')
                changed += cur.rowcount
                if cur.warning_count:
                    raise UpdateError('数据库产生转换或截断警告，已停止提交；请核对字段类型和值')
            committing = True
            conn.commit()
            return len(rows), changed
    except Exception as exc:
        if committing:
            raise CommitUncertain('提交时连接异常，结果无法确认。请先查询核实，禁止直接重复提交。') from exc
        try:
            conn.rollback()
        except Exception:
            pass  # Session close is the final rollback safeguard for InnoDB.
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass  # A close error must not turn a confirmed commit into an apparent failure.
