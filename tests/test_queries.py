from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from queries import bounded_sql, load_queries, bind_parameters


class QueryTests(unittest.TestCase):
    def test_order_and_limits_preserved(self):
        cases = {
            'SELECT id FROM video ORDER BY id DESC;': 'SELECT id FROM video ORDER BY id DESC LIMIT 2001',
            'SELECT 1 LIMIT 999999': 'SELECT 1 LIMIT 2001',
            'SELECT 1 LIMIT 0': 'SELECT 1 LIMIT 0',
            'SELECT id FROM video LIMIT 40, 9000': 'SELECT id FROM video LIMIT 2001 OFFSET 40',
            'SELECT id FROM video LIMIT 10 OFFSET 40': 'SELECT id FROM video LIMIT 10 OFFSET 40',
            "SELECT 'LIMIT 4; -- # INTO' AS x": "SELECT 'LIMIT 4; -- # INTO' AS x LIMIT 2001",
            'SELECT * FROM (SELECT 1 LIMIT 1) AS q': 'SELECT * FROM (SELECT 1 LIMIT 1) AS q LIMIT 2001',
        }
        for sql, expected in cases.items():
            with self.subTest(sql=sql):
                self.assertEqual(bounded_sql(sql), expected)

    def test_rejected_queries(self):
        for sql in [None, 5, '', 'DELETE FROM t', 'SELECT 1;;', 'SELECT 1;SELECT 2',
                    'SELECT 1 -- x', 'SELECT /*x*/ 1', 'SELECT 1 INTO OUTFILE "x"',
                    'SELECT 1 FOR UPDATE', 'SELECT 1 FOR SHARE', 'SELECT @x:=1',
                    'SELECT (1', "SELECT 'unclosed", 'SELECT 1 LIMIT %(n)s',
                    'SELECT 1 LIMIT -1', 'SELECT 1 LIMIT 1 LIMIT 2']:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                bounded_sql(sql)

    def load(self, data):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'q.json'
            p.write_text(json.dumps(data), encoding='utf-8')
            return load_queries(p)

    def test_legacy_parameters_and_quoted_placeholder_rejected(self):
        query = self.load([dict(name='q', sql='SELECT %(name)s', params=['name'])])[0]
        self.assertEqual(bind_parameters(query, {'name': " O'Reilly "}), {'name': " O'Reilly "})
        with self.assertRaises(ValueError):
            self.load([dict(name='q', sql="SELECT '%(name)s'", params=['name'])])

    def test_invalid_catalogue_gives_validation_error(self):
        for data in [[None], [1], [], {}, [dict(name='q', sql=123)],
                     [dict(name='q', sql='SELECT 1', params=[{}])],
                     [dict(name='q', sql='SELECT 1', description=3)]]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)

    def test_typed_parameters_and_date_range(self):
        query = self.load([dict(name='q', sql='SELECT %(start)s, %(end)s, %(n)s',
            params=[dict(name='start', type='datetime'), dict(name='end', type='datetime'), dict(name='n', type='integer')],
            date_ranges=[['start', 'end']])])[0]
        raw = dict(start='2026-09-28 00:00:00', end='2026-09-29 00:00:00', n='12')
        parsed = bind_parameters(query, raw)
        self.assertIsInstance(parsed['start'], datetime)
        self.assertEqual(parsed['n'], 12)
        for override in [dict(n='abc'), dict(start='wrong'), dict(start=raw['end']), dict(n='')]:
            with self.assertRaises(ValueError):
                bind_parameters(query, raw | override)

    def test_real_driver_escapes_injection_as_data(self):
        try:
            import pymysql
        except ImportError:
            self.skipTest('Requires PyMySQL, installed by CI')
        connection = pymysql.connect(defer_connect=True)
        connection.server_status = 0
        with connection.cursor() as cursor:
            sql = cursor.mogrify(bounded_sql('SELECT %(x)s AS x'), {'x': "x' OR 1=1 --"})
        self.assertIn("\\'", sql)
        self.assertTrue(sql.endswith('LIMIT 2001'))
