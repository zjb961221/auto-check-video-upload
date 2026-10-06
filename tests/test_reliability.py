import json
import queue
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from urllib.error import URLError
from api_client import ApiClient, ApiError, http_error_reason
from ui_recovery import guarded_poll
from workflows import WorkflowRun, WorkflowError, load_workflows


class ReliabilityTests(unittest.TestCase):
    def test_can_walk_back_through_stale_steps_without_bypassing_forward_gate(self):
        run=WorkflowRun({'steps':[{'type':'note'} for _ in range(5)]})
        for i in range(4):
            run.ready();run.complete();run.go(i+1)
        run.states[1:]=['stale']*4
        run.go(3);run.go(2);run.go(1)
        self.assertEqual(run.index,1)
        with self.assertRaises(WorkflowError):
            run.go(4)

    def test_broken_json_and_missing_reference_file_explain_exact_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'workflows.json'
            with self.assertRaisesRegex(WorkflowError,'workflows.json.*文件不存在'):
                load_workflows(path)
            path.write_text('{\n"version": 1,\n}',encoding='utf-8')
            with self.assertRaisesRegex(WorkflowError,'第 3 行'):
                load_workflows(path)
            path.write_text(json.dumps({'version':1,'workflows':[{'id':'f','name':'F','steps':[{'id':'q','title':'Q','type':'query','ref':'missing'}]}]}))
            with self.assertRaisesRegex(WorkflowError,'queries.json.*文件不存在'):
                load_workflows(path)

    def test_poll_exception_does_not_stop_future_results(self):
        owner=SimpleNamespace(winfo_exists=lambda:True,after=MagicMock(return_value='scheduled'),poll=MagicMock())
        handler=MagicMock(side_effect=RuntimeError('private'))
        recovery=MagicMock()
        guarded_poll(owner,handler,recovery)
        recovery.assert_called_once()
        self.assertEqual(owner.poll_id,'scheduled')
        owner.after.assert_called_once_with(100,owner.poll)
        # Next cycle can consume a successful result; no worker replay is involved.
        handler.side_effect=None
        guarded_poll(owner,handler,recovery)
        self.assertEqual(handler.call_count,2)
        self.assertEqual(recovery.call_count,1)

    def test_http_and_network_errors_are_actionable_without_secret_echo(self):
        self.assertIn('重新登录',http_error_reason(401))
        self.assertIn('权限',http_error_reason(403))
        self.assertIn('路径',http_error_reason(404))
        self.assertIn('反向代理',http_error_reason(502))
        for exc, expected in [(socket.gaierror('private-secret'),'域名无法解析'),(ConnectionRefusedError('private-secret'),'端口拒绝连接')]:
            client=ApiClient({'base_url':'http://localhost'})
            with patch.object(client.opener,'open',side_effect=URLError(exc)):
                with self.assertRaises(ApiError) as caught:
                    client.send({'path':'/'})
                self.assertIn(expected,str(caught.exception))
                self.assertNotIn('private-secret',str(caught.exception))


import os
import app


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Runs on Windows CI')
class ReliabilityWindowTests(unittest.TestCase):
    def test_missing_config_keeps_window_open_and_reload_recovers(self):
        with tempfile.TemporaryDirectory() as folder,patch('app.ROOT',Path(folder)),patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()),patch('app.messagebox.showerror'):
            root=app.App()
            try:
                root.update()
                panel=root.workflow
                self.assertIsNone(panel.run)
                self.assertIn('workflows.json',panel.status.cget('text'))
                self.assertIn('文件不存在',panel.status.cget('text'))
                Path(folder,'workflows.json').write_text(json.dumps({'version':1,'workflows':[{'id':'fix','name':'Fixed','steps':[{'id':'note','title':'Ready','type':'note'}]}]}))
                panel.reload();root.update()
                self.assertEqual(panel.run.flow['id'],'fix')
                self.assertEqual(len(panel.checks),1)
            finally:
                root.destroy()

    def test_rendering_failure_releases_busy_and_marks_write_uncertain(self):
        with tempfile.TemporaryDirectory() as folder,patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
            root=app.App()
            try:
                panel=root.workflow
                panel.pending_action='apply'
                panel.set_busy(True)
                panel.after_cancel(panel.poll_id)
                with patch.object(panel,'consume_result',side_effect=RuntimeError('private-secret')):
                    panel.poll()
                self.assertFalse(panel.busy)
                self.assertEqual(panel.run.states[0],'uncertain')
                self.assertIn('诊断编号',panel.status.cget('text'))
                self.assertNotIn('private-secret',panel.status.cget('text'))
                self.assertNotIn('private-secret',str(root.logger.mock_calls))
                self.assertIn(panel.poll_id,root.tk.call('after','info'))
            finally:
                root.destroy()
