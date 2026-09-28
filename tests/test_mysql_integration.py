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
