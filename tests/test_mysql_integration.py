"""Runs against an isolated MySQL CI fixture only, never a customer database."""
import os
import unittest
from database import run_query, test_connection


@unittest.skipUnless(os.environ.get('VIDEO_CHECK_MYSQL_TEST') == '1', 'Requires isolated MySQL integration fixture')
class MySQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pymysql
        cls.config = dict(host='127.0.0.1', port='3306', database='video_check_test',
                          user='fixture', password='fixture-test-only')
        conn = pymysql.connect(**(cls.config | {'port': 3306}), autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute('CREATE TABLE IF NOT EXISTS samples (id INT PRIMARY KEY, value VARCHAR(100))')
                cur.execute('TRUNCATE TABLE samples')
                cur.executemany('INSERT INTO samples VALUES (%s, %s)', [(i, f'值{i}') for i in range(2105)])
        finally:
            conn.close()

    def test_order_row_cap_and_duplicate_columns(self):
        columns, rows, truncated = run_query(self.config, 'SELECT id, id FROM samples ORDER BY id DESC', {})
        self.assertEqual(columns, ['id', 'id'])
        self.assertEqual(rows[0], (2104, 2104))
        self.assertEqual(rows[-1], (105, 105))
        self.assertEqual(len(rows), 2000)
        self.assertTrue(truncated)

    def test_offset_and_parameter_injection(self):
        _, rows, cut = run_query(self.config, 'SELECT id FROM samples ORDER BY id LIMIT 10 OFFSET 7', {})
        self.assertEqual(rows[0], (7,))
        self.assertEqual(len(rows), 10)
        self.assertFalse(cut)
        _, rows, _ = run_query(self.config, 'SELECT id FROM samples WHERE value=%(value)s', {'value': "' OR 1=1 --"})
        self.assertEqual(len(rows), 0)

    def test_connection_and_failure_recovery(self):
        import pymysql
        self.assertEqual(test_connection(self.config)[1], 'video_check_test')
        with self.assertRaises(pymysql.MySQLError):
            run_query(self.config, 'SELECT absent FROM samples', {})
        self.assertEqual(run_query(self.config, 'SELECT 1', {})[1][0], (1,))

    def test_dropdown_query_filters_recorder_and_sorts_channels(self):
        import pymysql
        from pathlib import Path
        from queries import load_queries, bind_parameters
        query=load_queries(Path(__file__).parents[1]/'queries.json')[-1]
        sql=query['sql'].replace('`9video`','`dropdown9`').replace('`10video`','`dropdown10`')
        conn=pymysql.connect(**(self.config | {'port':3306}),autocommit=True)
        try:
            with conn.cursor() as cur:
                for table in ('dropdown9','dropdown10'):
                    cur.execute(f'CREATE TABLE IF NOT EXISTS {table} (id VARCHAR(20), name VARCHAR(100))')
                    cur.execute(f'TRUNCATE TABLE {table}')
                cur.executemany('INSERT INTO dropdown9 VALUES (%s,%s)',[('D10','150622000013200031-A'),('D2','150622000013200031-B'),('D1','150622000013200063-X')])
                cur.execute('INSERT INTO dropdown10 VALUES (%s,%s)',('D3','150622000013200031-C'))
            for recorder, expected in [('9',['D2','D10']),('10',['D3'])]:
                values=bind_parameters(query,{'mine':'150622000013200031','recorder':recorder})
                rows=run_query(self.config,sql,values)[1]
                self.assertEqual([r[0] for r in rows],expected)
        finally:
            with conn.cursor() as cur:
                cur.execute('DROP TABLE IF EXISTS dropdown9, dropdown10')
            conn.close()


@unittest.skipUnless(os.environ.get('VIDEO_CHECK_MYSQL_TEST') == '1', 'Requires isolated MySQL integration fixture')
class MySQLUpdateIntegrationTests(unittest.TestCase):
    def setUp(self):
        import pymysql
        from updates import parse_update, validate_parameter_spec
        self.config = dict(host='127.0.0.1', port='3306', database='video_check_test', user='fixture', password='fixture-test-only')
        self.conn = pymysql.connect(**(self.config | {'port':3306}), autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS updates_fixture')
            cur.execute('CREATE TABLE updates_fixture (id INT PRIMARY KEY, name VARCHAR(30) UNIQUE) ENGINE=InnoDB')
            cur.executemany('INSERT INTO updates_fixture VALUES (%s,%s)', [(1,'a'), (2,'b')])
        self.operation = dict(name='fixture', sql='UPDATE updates_fixture SET name=%(new)s WHERE id >= %(start)s',
                              max_rows=2, params=[dict(name='new',type='text'),dict(name='start',type='integer')])
        self.operation['compiled'] = parse_update(self.operation['sql'])
        validate_parameter_spec(self.operation)

    def tearDown(self):
        self.conn.close()

    def rows(self):
        with self.conn.cursor() as cur:
            cur.execute('SELECT id,name FROM updates_fixture ORDER BY id')
            return cur.fetchall()

    def test_preview_and_successful_commit(self):
        from updates import preview_update, apply_update
        snapshot = preview_update(self.config, self.operation, {'new':'new','start':'2'})
        self.assertEqual(self.rows(), ((1,'a'), (2,'b')))
        self.assertEqual(apply_update(snapshot), (1,1))
        self.assertEqual(self.rows(), ((1,'a'), (2,'new')))
        snapshot = preview_update(self.config, self.operation, {'new':'new','start':'2'})
        self.assertEqual(apply_update(snapshot), (1,0))

    def test_second_write_error_rolls_back_first(self):
        import pymysql
        from updates import preview_update, apply_update
        snapshot = preview_update(self.config, self.operation, {'new':'same','start':'1'})
        with self.assertRaises(pymysql.IntegrityError):
            apply_update(snapshot)
        self.assertEqual(self.rows(), ((1,'a'), (2,'b')))

    def test_concurrent_change_prevents_commit(self):
        from updates import preview_update, apply_update, UpdateError
        snapshot = preview_update(self.config, self.operation, {'new':'new','start':'2'})
        with self.conn.cursor() as cur:
            cur.execute("UPDATE updates_fixture SET name='changed' WHERE id=2")
        with self.assertRaises(UpdateError):
            apply_update(snapshot)
        self.assertEqual(self.rows(), ((1,'a'), (2,'changed')))

    def test_max_rows_and_primary_key_protection(self):
        from updates import preview_update, UpdateError, parse_update
        self.operation['max_rows'] = 1
        with self.assertRaises(UpdateError):
            preview_update(self.config, self.operation, {'new':'new','start':'1'})
        self.operation['sql'] = 'UPDATE updates_fixture SET id=%(new)s WHERE id >= %(start)s'
        self.operation['compiled'] = parse_update(self.operation['sql'])
        with self.assertRaises(UpdateError):
            preview_update(self.config, self.operation, {'new':'3','start':'2'})
        self.assertEqual(self.rows(), ((1,'a'), (2,'b')))

    def test_empty_string_null_and_literal_null_storage(self):
        from updates import preview_update, apply_update
        for mode, value, expected in [('empty','ignored',''), ('null','ignored',None), ('value','NULL','NULL')]:
            snapshot = preview_update(self.config, self.operation, {'new':value,'start':'2'}, modes={'new':mode})
            apply_update(snapshot)
            self.assertEqual(self.rows()[1][1], expected)

    def test_null_on_not_null_column_is_rejected_before_write(self):
        from updates import preview_update, UpdateError
        with self.conn.cursor() as cur:
            cur.execute('ALTER TABLE updates_fixture MODIFY name VARCHAR(30) NOT NULL')
        with self.assertRaises(UpdateError):
            preview_update(self.config, self.operation, {'new':'','start':'2'}, modes={'new':'null'})
        self.assertEqual(self.rows(), ((1,'a'), (2,'b')))
