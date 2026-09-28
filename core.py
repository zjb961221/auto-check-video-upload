"""Fixed, parameterized MySQL queries. No credentials are stored here."""
import csv
import json
import re
from pathlib import Path

MAX_ROWS = 2000


def load_queries(path):
    items = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(items, list) or not items:
        raise ValueError('queries.json 必须是非空查询列表')
    seen = set()
    for item in items:
        if not isinstance(item.get('name'), str) or not item['name'].strip() or item['name'] in seen:
            raise ValueError('查询名称不能为空或重复')
        seen.add(item['name'])
        sql = item.get('sql', '').strip()
        # Deliberately small query language; database privileges are the security boundary.
        if not re.match(r'^SELECT\b', sql, re.I) or any(x in sql for x in (';', '--', '/*', '#')):
            raise ValueError(f"{item['name']}: 仅支持单条 SELECT，不允许分号或注释")
        if re.search(r'\b(INTO|OUTFILE|DUMPFILE|FOR\s+UPDATE|LOCK\s+IN\s+SHARE)\b', sql, re.I):
            raise ValueError('不允许文件写入或加锁查询')
        params = item.get('params', [])
        if not isinstance(params, list) or any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p) for p in params):
            raise ValueError('参数名必须为英文标识符')
        if len(set(params)) != len(params) or set(re.findall(r'%\((\w+)\)s', sql)) != set(params):
            raise ValueError('SQL 占位符与 params 不一致')
    return items


def run_query(config, sql, params, connect=None):
    if connect is None:
        import pymysql
        connect = pymysql.connect
    options = dict(host=config['host'], port=int(config['port']), user=config['user'],
                   password=config['password'], database=config['database'], charset='utf8mb4',
                   connect_timeout=8, read_timeout=35, write_timeout=8, autocommit=False,
                   local_infile=False)
    if config.get('ssl_ca'):
        options.update(ssl_ca=config['ssl_ca'], ssl_verify_cert=True, ssl_verify_identity=True)
    conn = connect(**options)
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=30000')
            cur.execute('SET SESSION SQL_SELECT_LIMIT=2001')
            cur.execute('START TRANSACTION READ ONLY')
            cur.execute(f'SELECT * FROM ({sql}) AS fixed_query LIMIT {MAX_ROWS + 1}', params or None)
            columns = [c[0] for c in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
            return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def export_csv(path, columns, rows):
    def safe(value):
        value = '' if value is None else str(value)
        # Prevent spreadsheet formula interpretation, including leading whitespace.
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow([safe(v) for v in columns])
        writer.writerows([safe(v) for v in row] for row in rows)
