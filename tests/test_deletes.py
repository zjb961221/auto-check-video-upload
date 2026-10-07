"""Deletion safeguards; fakes never connect to customer databases."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock
from updates import (parse_mutation, load_updates, preview_update, apply_update,
                     Preview, UpdateError, CommitUncertain, preview_fields)
from test_updates import CONFIG, METADATA


def operation(sql='DELETE FROM samples WHERE id=%(id)s', default=1):
    param = 'name' if 'name=%(name)s' in sql else 'id'
    item = dict(name='删除记录', sql=sql, max_rows=1,
                params=[dict(name=param, type='text' if param=='name' else 'integer', default=default)])
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'updates.json'
        path.write_text(json.dumps([item]),encoding='utf-8')
        return load_updates(path)[0]


def connection(rows=((1,'old'),), indexes=(('PRIMARY','id',None),), schema=METADATA[1]):
    factory=MagicMock()
    conn=factory.return_value
    cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[METADATA[0],('InnoDB',)]
    cur.fetchall.side_effect=[schema,[('id',)],indexes,rows]
    cur.rowcount=1
    cur.warning_count=0
    return factory,conn,cur


class DeleteTests(unittest.TestCase):
    def test_restricted_delete_parser(self):
        op=parse_mutation('DELETE FROM `9video` WHERE id=%(id)s AND name=%(name)s;')
        self.assertEqual(op['kind'],'delete')
        self.assertEqual(len(op['conditions']),2)
        for sql in ['DELETE FROM t', 'DELETE FROM t WHERE name LIKE %(name)s',
                    'DELETE FROM t WHERE id>%(id)s', 'DELETE FROM t WHERE id=%(id)s OR 1=1',
                    'DELETE FROM t WHERE id=%(id)s LIMIT 1', 'DELETE t FROM t JOIN u WHERE id=%(id)s',
                    'DELETE FROM t WHERE id=%(id)s; DELETE FROM u',
                    "DELETE FROM t WHERE id='%(id)s'", 'DELETE FROM t WHERE id=%(id)s -- x']:
            with self.subTest(sql=sql),self.assertRaises(UpdateError):
                parse_mutation(sql)

    def snapshot(self):
        factory,conn,cur=connection()
        result=preview_update(CONFIG,operation(),{'id':'1'},factory)
        conn.commit.assert_not_called()
        self.assertFalse(any(c.args[0].startswith('DELETE ') for c in cur.execute.call_args_list))
        return result

    def test_preview_all_fields_and_delete_only_locked_primary_key(self):
        snap=self.snapshot()
        self.assertEqual(preview_fields(snap),[('id',None),('name',None)])
        factory,conn,cur=connection()
        self.assertEqual(apply_update(snap,factory),(1,1))
        cur.execute.assert_any_call('DELETE FROM `samples` WHERE `id`=%s',(1,))
        self.assertTrue(any(c.args[0].endswith('FOR UPDATE') for c in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    def test_nonunique_name_rejected_even_when_one_row_matches(self):
        factory,conn,cur=connection()
        with self.assertRaisesRegex(UpdateError,'完整主键'):
            preview_update(CONFIG,operation('DELETE FROM samples WHERE name=%(name)s','old'),{'name':'old'},factory)
        conn.commit.assert_not_called()

    def test_unique_nonnullable_name_allowed_but_write_uses_primary_key(self):
        schema=(METADATA[1][0],('name','varchar(100)','NO','',None))
        indexes=(('PRIMARY','id',None),('name_unique','name',None))
        factory,_,_=connection(indexes=indexes,schema=schema)
        snap=preview_update(CONFIG,operation('DELETE FROM samples WHERE name=%(name)s','old'),{'name':'old'},factory)
        factory,conn,cur=connection(indexes=indexes,schema=schema)
        apply_update(snap,factory)
        cur.execute.assert_any_call('DELETE FROM `samples` WHERE `id`=%s',(1,))

    def test_nullable_unique_and_partial_composite_key_rejected(self):
        for indexes in [(('name_unique','name',None),), (('composite','id',None),('composite','name',None))]:
            factory,_,_=connection(indexes=indexes)
            with self.assertRaises(UpdateError):
                preview_update(CONFIG,operation('DELETE FROM samples WHERE name=%(name)s','old'),{'name':'old'},factory)

    def test_limit_and_concurrent_change_reject_all_writes(self):
        snap=self.snapshot()
        for rows in [((1,'changed'),),(),((1,'old'),(2,'old'))]:
            factory,conn,cur=connection(rows)
            with self.assertRaises(UpdateError):
                apply_update(snap,factory)
            self.assertFalse(any(c.args[0].startswith('DELETE ') for c in cur.execute.call_args_list))
            conn.commit.assert_not_called()
            conn.rollback.assert_called_once()

    def test_delete_zero_rowcount_and_warning_roll_back(self):
        snap=self.snapshot()
        for count,warnings in [(0,0),(2,0),(1,1)]:
            factory,conn,cur=connection()
            cur.rowcount=count;cur.warning_count=warnings
            with self.assertRaises(UpdateError):
                apply_update(snap,factory)
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_expired_empty_and_uncertain_commit(self):
        snap=self.snapshot()
        for rows,created in [((),snap.created),(snap.rows,time.monotonic()-301)]:
            factory=MagicMock()
            with self.assertRaises(UpdateError):
                apply_update(Preview(snap.operation,snap.params,snap.config,snap.metadata,snap.columns,rows,created),factory)
            factory.assert_not_called()
        factory,conn,_=connection()
        conn.commit.side_effect=OSError('lost acknowledgement')
        with self.assertRaises(CommitUncertain):
            apply_update(snap,factory)
        self.assertEqual(conn.commit.call_count,1)
        conn.rollback.assert_not_called()

    def test_typed_defaults_preserved_and_invalid_defaults_rejected(self):
        self.assertEqual(operation(default=42)['params'][0]['default'],42)
        for default in [True,None,'bad integer']:
            with self.assertRaises(UpdateError):
                operation(default=default)

    def test_workflow_delete_type_and_default_override(self):
        from workflows import load_workflows,WorkflowError
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            op=operation()
            op.pop('compiled')
            (path/'updates.json').write_text(json.dumps([op]),encoding='utf-8')
            step=dict(id='remove',title='删除',type='delete',ref=op['name'],defaults={'id':'42'})
            data=dict(version=1,workflows=[dict(id='cleanup',name='清理',steps=[step])])
            (path/'workflows.json').write_text(json.dumps(data),encoding='utf-8')
            result=load_workflows(path/'workflows.json')[0]['steps'][0]
            self.assertEqual(result['defaults']['id'],'42')
            self.assertEqual(result['operation']['params'][0]['default'],1)
            step['type']='update'
            (path/'workflows.json').write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(WorkflowError):
                load_workflows(path/'workflows.json')

import os
import queue
from unittest.mock import patch
from types import SimpleNamespace


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Windows CI runs actual Tk windows')
class DeleteWindowTests(unittest.TestCase):
    def test_defaults_delete_preview_confirmation_and_uncertain_gate(self):
        import app
        from updates_ui import UpdateWindow
        with tempfile.TemporaryDirectory() as folder, patch('app.SETTINGS',Path(folder)/'settings.json'), patch('app.configure_logging',return_value=MagicMock()):
            root=app.App()
            root.withdraw()
            path=Path(folder)/'updates.json'
            op=operation(default=42)
            raw=dict(op);raw.pop('compiled')
            path.write_text(json.dumps([raw]),encoding='utf-8')
            ui=UpdateWindow(root,CONFIG,path,MagicMock())
            try:
                self.assertEqual(ui.parameters['id'].get(),'42')
                self.assertEqual(ui.modes,{})
                self.assertIn('删除',ui.submit_button['text'])
                snap=Preview(op,{'id':42},CONFIG,METADATA+((),),('id','name'),((42,'old'),),time.monotonic())
                ui.jobs.put(('preview','ok',snap));ui.consume_result()
                self.assertEqual(len(ui.table.get_children()),2)
                self.assertIn('删除整条记录',str(ui.table.item(ui.table.get_children()[0],'values')))
                with patch('updates_ui.messagebox.askyesno',return_value=False),patch.object(ui,'launch') as launch:
                    ui.submit();launch.assert_not_called()
                with patch('updates_ui.messagebox.askyesno',return_value=True) as confirm,patch.object(ui,'launch') as launch:
                    ui.submit();launch.assert_called_once()
                    self.assertIn('永久删除',confirm.call_args.args[1])
                    self.assertIsNone(ui.preview)
                ui.jobs.put(('apply','uncertain','提交结果待核实'))
                with patch('updates_ui.messagebox.showerror'):
                    ui.consume_result()
                ui.parameters['id'].set('43')
                self.assertTrue(ui.uncertain)
                with patch.object(ui,'launch') as launch:
                    ui.start_preview();launch.assert_not_called()
                with patch('updates_ui.messagebox.askyesno',return_value=True):
                    ui.reconcile()
                self.assertFalse(ui.uncertain)
                panel=root.workflow
                panel.flows=[dict(id='delete',name='删除',steps=[dict(id='one',title='删除',type='delete',operation=op,defaults={'id':'99'})])]
                panel.start_flow(0)
                self.assertEqual(panel.parameters['id'].get(),'99')
                self.assertIn('删除',panel.apply_button['text'])
                panel.jobs.put(('delete','ok',snap));panel.consume_result()
                self.assertIn('删除整条记录',panel.output.get('1.0','end'))
                self.assertEqual(panel.run.states,['pending'])
                self.assertEqual(str(panel.next_button['state']),'disabled')
                old_scroll = panel.scroll_id
                panel.jobs.put(('apply','ok',(1,1)));panel.consume_result()
                self.assertNotIn(old_scroll,root.tk.call('after','info'))
                self.assertEqual(panel.run.states,['ready'])
            finally:
                ui.close();root.destroy()


class BatchDeleteTests(unittest.TestCase):
    def batch(self, limit=3):
        op=operation('DELETE FROM samples WHERE name=%(name)s','AudioOut1')
        op.update(delete_mode='matched',max_rows=limit)
        return op

    def snapshot(self):
        factory,conn,cur=connection(((1,'AudioOut1'),(2,'AudioOut1')))
        snap=preview_update(CONFIG,self.batch(),{'name':'AudioOut1'},factory)
        conn.commit.assert_not_called()
        return snap

    def test_batch_preview_and_writes_use_only_previewed_keys(self):
        snap=self.snapshot()
        factory,conn,cur=connection(snap.rows)
        self.assertEqual(apply_update(snap,factory),(2,2))
        deletes=[c.args for c in cur.execute.call_args_list if c.args[0].startswith('DELETE ')]
        self.assertEqual(deletes,[('DELETE FROM `samples` WHERE `id`=%s',(1,)),('DELETE FROM `samples` WHERE `id`=%s',(2,))])
        conn.commit.assert_called_once()

    def test_batch_limit_rejects_entire_preview(self):
        factory,conn,_=connection(((1,'AudioOut1'),(2,'AudioOut1')))
        with self.assertRaisesRegex(UpdateError,'超过上限'):
            preview_update(CONFIG,self.batch(1),{'name':'AudioOut1'},factory)
        conn.commit.assert_not_called()

    def test_changed_or_added_matching_rows_block_all_deletes(self):
        snap=self.snapshot()
        for rows in [((1,'AudioOut1'),),((1,'AudioOut1'),(2,'AudioOut1'),(3,'AudioOut1'))]:
            factory,conn,cur=connection(rows)
            with self.assertRaises(UpdateError):apply_update(snap,factory)
            conn.commit.assert_not_called()
            self.assertFalse(any(c.args[0].startswith('DELETE ') for c in cur.execute.call_args_list))

    def test_second_delete_failure_rolls_back_transaction(self):
        snap=self.snapshot()
        factory,conn,cur=connection(snap.rows)
        def execute(sql,values=None):
            if sql.startswith('DELETE ') and values==(2,):raise RuntimeError('fixture failure')
        cur.execute.side_effect=execute
        with self.assertRaises(RuntimeError):apply_update(snap,factory)
        conn.rollback.assert_called_once();conn.commit.assert_not_called()

    def test_batch_mode_validation_and_ceiling(self):
        from updates import validate_mutation_limits
        for mode,limit in [('matched',1001),('matched',True),('unsafe',3),('unique',101)]:
            op=self.batch(limit);op['delete_mode']=mode
            with self.assertRaises(UpdateError):validate_mutation_limits(op)
        op=self.batch(1000);validate_mutation_limits(op)
        with tempfile.TemporaryDirectory() as folder:
            raw=dict(op);raw.pop('compiled')
            path=Path(folder)/'updates.json';path.write_text(json.dumps([raw]))
            self.assertEqual(load_updates(path)[0]['delete_mode'],'matched')
