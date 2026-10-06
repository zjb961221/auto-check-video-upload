import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import queue
from workflows import WorkflowRun, WorkflowError, load_workflows, api_outcome
from workflow_ui import WorkflowPanel

ROOT = Path(__file__).parents[1]


class WorkflowTests(unittest.TestCase):
    def test_shipped_flows_and_resolved_operations(self):
        flows = load_workflows(ROOT / 'workflows.json')
        self.assertEqual(flows[0]['steps'][0]['type'], 'note')
        self.assertIn('sql', flows[0]['steps'][1]['operation'])
        self.assertIn('request', flows[1]['steps'][1]['operation'])

    def test_sequential_gates_and_invalidated_downstream(self):
        run = WorkflowRun({'steps': [{'type': 'note'}, {'type': 'query'}, {'type': 'update'}]})
        with self.assertRaises(WorkflowError):
            run.go(1)
        with self.assertRaises(WorkflowError):
            run.complete()
        run.ready(); run.complete(); run.go(1)
        run.ready(); run.complete(); run.go(2)
        run.ready(); run.complete()
        run.go(0); run.invalidate()
        self.assertEqual(run.states, ['pending', 'stale', 'stale'])
        with self.assertRaises(WorkflowError):
            run.go(2)

    def test_uncertain_cannot_be_unlocked_by_edit_or_upstream_invalidation(self):
        run = WorkflowRun({'steps': [{'type': 'note'}, {'type': 'api', 'optional': True}]})
        run.ready(); run.complete(); run.go(1)
        run.states[1] = 'uncertain'
        run.invalidate()
        with self.assertRaises(WorkflowError):
            run.ready()
        with self.assertRaises(WorkflowError):
            run.skip()
        run.go(0); run.invalidate()
        self.assertEqual(run.states[1], 'uncertain')

    def test_optional_skip_only_and_prior_validation(self):
        run = WorkflowRun({'steps': [{'type': 'note'}, {'type': 'note', 'optional': True}]})
        with self.assertRaises(WorkflowError):
            run.skip()
        run.ready(); run.complete(); run.go(1); run.skip(); run.complete()
        self.assertEqual(run.states, ['done', 'skipped'])
        run.states[0] = 'stale'
        with self.assertRaises(WorkflowError):
            run.complete()

    def test_loader_rejects_broken_reference_duplicate_id_and_typo(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workflows.json'
            flow = {'version': 1, 'workflows': [{'id': 'one', 'name': 'One', 'steps': [
                {'id': 'one', 'title': 'First', 'type': 'note'}]}]}
            for step in [
                {'id': 'one', 'title': 'Bad', 'type': 'delete'},
                {'id': 'one', 'title': 'Bad', 'type': 'note', 'optional': 'false'},
                {'id': 'one', 'title': 'Bad', 'type': 'note', 'checklst': []},
                {'id': 'one', 'title': 'Bad', 'type': 'api', 'ref': 'missing'},
            ]:
                flow['workflows'][0]['steps'] = [step]
                path.write_text(json.dumps(flow))
                with self.assertRaises(ValueError):
                    load_workflows(path)
            note = {'id': 'same', 'title': 'X', 'type': 'note'}
            flow['workflows'][0]['steps'] = [note, note]
            path.write_text(json.dumps(flow))
            with self.assertRaises(WorkflowError):
                load_workflows(path)

    def test_http_status_is_not_business_success(self):
        def response(status, body):
            return SimpleNamespace(status=status, text=lambda: body)
        rule = {'path': 'data.0.code', 'equals': 200}
        self.assertTrue(api_outcome(response(200, '{"data":[{"code":200}]}'), rule)[0])
        for status, body in [(401, '{}'), (200, '<html>login</html>'), (200, '{}'),
                             (200, '{"data":[{"code":"200"}]}'), (200, '{"data":[{"code":500}]}')]:
            self.assertFalse(api_outcome(response(status, body), rule)[0])
        self.assertIn('人工核对', api_outcome(response(200, '{}'))[1])

    def test_preview_does_not_mark_step_ready_and_apply_does(self):
        ui = MagicMock()
        ui.run = WorkflowRun({'steps': [{'type': 'update'}]})
        ui.jobs = queue.Queue()
        ui.results = {}
        preview = SimpleNamespace(rows=[('D1', 'old')], columns=('id', 'name'), metadata=(None, None, ('id',)),
                                  operation={'compiled': {'changes': [('name', 'value')]}}, params={'value': None})
        ui.jobs.put(('update', 'ok', preview))
        WorkflowPanel.poll(ui)
        self.assertEqual(ui.run.states, ['pending'])
        self.assertIs(ui.preview, preview)
        ui.jobs.put(('apply', 'ok', (1, 1)))
        WorkflowPanel.poll(ui)
        self.assertEqual(ui.run.states, ['ready'])

    def test_failed_mutating_api_is_blocked_until_verification(self):
        ui = MagicMock()
        ui.run = WorkflowRun({'steps': [{'type': 'api'}]})
        ui.jobs = queue.Queue()
        ui.results = {}
        ui.jobs.put(('api', 'ok', (False, 'failed business check', '{}', True)))
        WorkflowPanel.poll(ui)
        self.assertEqual(ui.run.states, ['uncertain'])
        with self.assertRaises(WorkflowError):
            ui.run.complete()


import os
import time
import app
from updates import load_updates


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Desktop integration runs on Windows CI')
class WorkflowWindowTests(unittest.TestCase):
    def wait(self, window, panel):
        deadline = time.monotonic() + 5
        while panel.busy and time.monotonic() < deadline:
            window.update()
            time.sleep(0.01)
        self.assertFalse(panel.busy)

    def test_note_query_update_api_in_one_panel(self):
        with tempfile.TemporaryDirectory() as folder, patch('app.SETTINGS', Path(folder)/'connection.json'), patch('app.configure_logging', return_value=MagicMock()):
            window = app.App()
            try:
                window.withdraw()
                panel = window.workflow
                self.assertEqual(window.navigation.select(), str(window.workflow_tab))
                update = load_updates(ROOT/'updates.example.json')[0]
                query = load_workflows(ROOT/'workflows.json')[0]['steps'][1]['operation']
                api = {'name': 'Fixture API', 'profile_name': 'Fixture', 'profile': {'base_url': 'http://localhost', 'auth_type': 'none'},
                       'request': {'path': '/'}, 'params': []}
                panel.flows = [{'id': 'fixture', 'name': 'Fixture', 'steps': [
                    {'id': 'prep', 'title': '准备', 'type': 'note', 'checklist': ['已核对目标']},
                    {'id': 'read', 'title': '查询', 'type': 'query', 'operation': query},
                    {'id': 'write', 'title': '修改', 'type': 'update', 'operation': update},
                    {'id': 'api', 'title': '接口', 'type': 'api', 'operation': api, 'success': {'path': 'code', 'equals': 200}},
                    {'id': 'end', 'title': '完成说明', 'type': 'note'}]}]
                panel.start_flow(0)
                for name, value in {'host':'localhost', 'port':'3306', 'database':'fixture', 'user':'fixture'}.items():
                    window.vars[name].set(value)
                self.assertEqual(str(panel.next_button['state']), 'disabled')
                panel.checks[0].set(True); panel.note_changed(); panel.next()
                self.assertEqual(panel.run.index, 1)
                with patch('workflow_ui.run_query', return_value=(['id'], [('D1',)], False)) as execute:
                    panel.execute()
                    self.wait(window, panel)
                    execute.assert_called_once()
                self.assertEqual(panel.run.states[1], 'ready')
                self.assertIn('D1', panel.output.get('1.0', 'end'))
                panel.next()
                panel.parameters['channel_id'].set('D1')
                panel.parameters['new_name'].set('new')
                preview = SimpleNamespace(rows=[('D1', 'old')], columns=('id', 'name'), metadata=(None,None,('id',)),
                                          operation=update, params={'channel_id':'D1','new_name':'new'}, config=window.config())
                with patch('workflow_ui.preview_update', return_value=preview):
                    panel.execute(); self.wait(window, panel)
                self.assertEqual(str(panel.next_button['state']), 'disabled')
                self.assertEqual(str(panel.apply_button['state']), 'normal')
                with patch('workflow_ui.messagebox.askyesno', return_value=True), patch('workflow_ui.apply_update', return_value=(1,1)) as apply:
                    panel.submit(); self.wait(window, panel)
                    apply.assert_called_once_with(preview)
                panel.next()
                response = SimpleNamespace(status=200, text=lambda:'{"code":200}')
                with patch('workflow_ui.ApiClient.send', return_value=response), patch('workflow_ui.ApiClient.display', return_value='{"code":200}'):
                    panel.execute(); self.wait(window, panel)
                self.assertEqual(panel.run.states[3], 'ready')
                panel.next()
                panel.checks[0].set(True); panel.note_changed()
                with patch('workflow_ui.messagebox.showinfo'):
                    panel.next()
                self.assertEqual(panel.run.states, ['done']*5)
                # Changing a shared target invalidates earlier DB evidence and prevents finishing.
                window.vars['database'].set('different')
                self.assertEqual(panel.run.states[1], 'stale')
                self.assertEqual(str(panel.next_button['state']), 'disabled')
                panel.move(1)
                self.assertEqual(panel.run.index, 1)
            finally:
                window.destroy()
