import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from core import load_queries, run_query, export_csv, MAX_ROWS


class CoreTests(unittest.TestCase):
    def test_queries(self):
        self.assertTrue(load_queries(Path(__file__).parents[1] / 'queries.json'))

    def test_reject_unsafe_and_mismatched_queries(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'queries.json'
            for sql in ['DELETE FROM video', 'SELECT 1; DELETE FROM video', 'SELECT 1 INTO OUTFILE \'/tmp/x\'', 'SELECT %(missing)s', 'SELECT /*comment*/ 1']:
                p.write_text(json.dumps([{'name': 'bad', 'sql': sql}]))
                with self.assertRaises(ValueError):
                    load_queries(p)

    def test_parameter_binding_limit_and_cleanup(self):
        connect = MagicMock()
        conn = connect.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.description = [('value',)]
        cur.fetchmany.return_value = [(i,) for i in range(MAX_ROWS + 1)]
        config = dict(host='localhost', port='3306', user='reader', password='secret', database='demo')
        params = {'value': "' OR 1=1 --"}
        cols, rows, truncated = run_query(config, 'SELECT %(value)s AS value', params, connect)
        self.assertEqual(cols, ['value'])
        self.assertEqual(len(rows), MAX_ROWS)
        self.assertTrue(truncated)
        cur.execute.assert_any_call('START TRANSACTION READ ONLY')
        cur.execute.assert_any_call('SELECT %(value)s AS value LIMIT 2001', params)
        conn.rollback.assert_not_called()
        conn.close.assert_called_once()
        self.assertFalse(connect.call_args.kwargs['local_infile'])

    def test_failure_still_closes_connection(self):
        connect = MagicMock()
        conn = connect.return_value
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError('failed')
        config = dict(host='h', port='3306', user='u', password='p', database='d')
        with self.assertRaises(RuntimeError):
            run_query(config, 'SELECT 1', {}, connect)
        conn.rollback.assert_not_called()
        conn.close.assert_called_once()

    def test_csv_unicode_null_and_formula_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'result.csv'
            export_csv(p, ['名称', '=header'], [('测试,换行\n内容', '=1+1'), (None, ' @SUM(1)'), ('正常', '123')])
            with p.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], ['名称', "'=header"])
            self.assertEqual(rows[1], ['测试,换行\n内容', "'=1+1"])
            self.assertEqual(rows[2], ['', "' @SUM(1)"])


if __name__ == '__main__':
    unittest.main()
