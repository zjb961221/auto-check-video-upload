from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock
from updates import (parse_update, load_updates, preview_update, apply_update,
                     Preview, UpdateError, CommitUncertain)

EXAMPLE = dict(name='修改名称', sql='UPDATE `samples` SET name=%(new_name)s WHERE id=%(id)s',
               max_rows=1, params=[dict(name='new_name', type='text'), dict(name='id', type='integer')])
CONFIG = dict(host='localhost', port='3306', user='reader', password='secret', database='fixture')
METADATA = (('server-uuid', 'fixture'), (('id', 'int', 'NO', '', None), ('name', 'varchar(100)', 'YES', '', 'utf8mb4_general_ci')), ('id',))


def operation():
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder) / 'updates.json'
        p.write_text(json.dumps([EXAMPLE]), encoding='utf-8')
        return load_updates(p)[0]


def connection(rows=((1, 'old'),)):
    factory = MagicMock()
    conn = factory.return_value
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [('server-uuid', 'fixture'), ('InnoDB',)]
    cursor.fetchall.side_effect = [METADATA[1], [('id',)], rows]
    cursor.rowcount = 1
    cursor.warning_count = 0
    return factory, conn, cursor


class UpdateTests(unittest.TestCase):
    def test_parser_and_rejections(self):
        parsed = parse_update('UPDATE `9video` SET `name` = %(new)s WHERE id=%(id)s AND name LIKE %(old)s;')
        self.assertEqual(parsed['table'], '9video')
        self.assertEqual(len(parsed['conditions']), 2)
        for sql in ['UPDATE t SET a=%(a)s', 'UPDATE t SET a=1 WHERE id=%(id)s',
                    'UPDATE t SET a=%(a)s WHERE 1=1', 'UPDATE t SET a=%(a)s WHERE id=%(id)s OR 1=1',
                    'UPDATE t SET a=%(a)s WHERE id=%(id)s;DELETE FROM t',
                    'UPDATE t SET a=%(a)s WHERE id=%(id)s -- comment',
                    'UPDATE t JOIN u SET a=%(a)s WHERE id=%(id)s',
                    'UPDATE t SET a=%(a)s,A=%(b)s WHERE id=%(id)s']:
            with self.subTest(sql=sql), self.assertRaises(UpdateError):
                parse_update(sql)

    def test_preview_never_writes(self):
        factory, conn, cursor = connection()
        result = preview_update(CONFIG, operation(), {'new_name': 'new', 'id': '1'}, factory)
        self.assertEqual(result.rows, ((1, 'old'),))
        self.assertFalse(any(call.args[0].startswith('UPDATE ') for call in cursor.execute.call_args_list))
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    def snapshot(self):
        return Preview(operation(), dict(new_name='new', id=1), CONFIG, METADATA, ('id', 'name'), ((1, 'old'),), time.monotonic())

    def test_apply_uses_locked_primary_key(self):
        factory, conn, cursor = connection()
        self.assertEqual(apply_update(self.snapshot(), factory), (1, 1))
        cursor.execute.assert_any_call('UPDATE `samples` SET `name`=%s WHERE `id`=%s', ('new', 1))
        self.assertTrue(any(c.args[0].endswith('FOR UPDATE') for c in cursor.execute.call_args_list))
        conn.commit.assert_called_once()

    def test_changed_record_prevents_writes(self):
        factory, conn, cursor = connection(((1, 'someone else changed it'),))
        with self.assertRaises(UpdateError):
            apply_update(self.snapshot(), factory)
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
        self.assertFalse(any(c.args[0].startswith('UPDATE ') for c in cursor.execute.call_args_list))

    def test_limit_rejected_without_commit(self):
        factory, conn, cursor = connection(((1, 'a'), (2, 'b')))
        with self.assertRaises(UpdateError):
            preview_update(CONFIG, operation(), {'new_name':'new', 'id':'1'}, factory)
        conn.commit.assert_not_called()

    def test_warning_rolls_back(self):
        factory, conn, cursor = connection()
        cursor.warning_count = 1
        with self.assertRaises(UpdateError):
            apply_update(self.snapshot(), factory)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_lost_commit_ack_is_not_reported_as_rollback(self):
        factory, conn, cursor = connection()
        conn.commit.side_effect = OSError('connection dropped')
        with self.assertRaises(CommitUncertain):
            apply_update(self.snapshot(), factory)
        conn.rollback.assert_not_called()

    def test_stale_preview_does_not_connect(self):
        snapshot = self.snapshot()
        expired = Preview(snapshot.operation, snapshot.params, snapshot.config, snapshot.metadata,
                          snapshot.columns, snapshot.rows, time.monotonic()-301)
        factory = MagicMock()
        with self.assertRaises(UpdateError):
            apply_update(expired, factory)
        factory.assert_not_called()
