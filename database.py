"""Short-lived database sessions; no Tk or persistence dependencies."""
from pathlib import Path
from queries import MAX_ROWS, bounded_sql


def validate_connection(config):
    data = {k: str(config.get(k, '')).strip() for k in ('host', 'port', 'database', 'user', 'ssl_ca')}
    data['password'] = config.get('password', '')
    if not all(data[k] for k in ('host', 'database', 'user')):
        raise ValueError('请填写地址、数据库和用户名')
    try:
        port = int(data['port'])
    except ValueError:
        raise ValueError('端口必须是 1–65535 的整数') from None
    if not 1 <= port <= 65535:
        raise ValueError('端口必须是 1–65535 的整数')
    if data['ssl_ca'] and not Path(data['ssl_ca']).is_file():
        raise ValueError('CA 证书文件不存在')
    return data


def connect_database(config, connect=None):
    config = validate_connection(config)
    if connect is None:
        import pymysql
        connect = pymysql.connect
    options = dict(host=config['host'], port=int(config['port']), user=config['user'],
                   password=config['password'], database=config['database'], charset='utf8mb4',
                   connect_timeout=8, read_timeout=35, write_timeout=8,
                   autocommit=False, local_infile=False)
    if config.get('ssl_ca'):
        options.update(ssl_ca=config['ssl_ca'], ssl_verify_cert=True, ssl_verify_identity=True)
    return connect(**options)


def test_connection(config, connect=None):
    conn = connect_database(config, connect)
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT VERSION(), DATABASE()')
            return cursor.fetchone()
    finally:
        conn.close()


def run_query(config, sql, params, connect=None):
    sql = bounded_sql(sql)  # Validate before opening a connection.
    conn = connect_database(config, connect)
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=30000')
            cur.execute('START TRANSACTION READ ONLY')
            cur.execute(sql, params or None)
            columns = [c[0] for c in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
            return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS
    finally:
        # Closing the dedicated session rolls back the read-only transaction.
        # No rollback round-trip that can mask the original query failure.
        conn.close()
