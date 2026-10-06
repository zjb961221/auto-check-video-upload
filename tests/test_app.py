import os
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import app


class AppStateTests(unittest.TestCase):
    def task(self, test=True):
        ui = MagicMock()
        ui.jobs = queue.Queue()
        ui.active_task = dict(config={}, remember=True, test=test, query_name='example', started=time.monotonic())
        ui.columns, ui.rows = ['old'], [(1,)]
        ui.result_context = {'name': 'previous'}
        return ui

    def test_connection_test_keeps_existing_result(self):
        ui = self.task()
        ui.jobs.put(('ok', ('8.0', 'video'), 0.2))
        with patch('app.save_settings'):
            app.App.poll(ui)
        ui.table.delete.assert_not_called()
        self.assertEqual(ui.rows, [(1,)])

    def test_save_failure_does_not_discard_success(self):
        ui = self.task()
        ui.jobs.put(('ok', ('8.0', 'video'), 0.2))
        with patch('app.save_settings', side_effect=OSError('disk')), patch('app.messagebox.showwarning') as warning:
            app.App.poll(ui)
        warning.assert_called_once()
        self.assertIn('连接正常', ui.status.configure.call_args.kwargs['text'])
        self.assertEqual(ui.rows, [(1,)])

    def test_failed_query_preserves_and_labels_old_result(self):
        ui = self.task(False)
        ui.jobs.put(('error', 'connection failed', 0.2))
        with patch('app.messagebox.showerror'), patch('app.save_settings') as save:
            app.App.poll(ui)
        save.assert_not_called()
        ui.table.delete.assert_not_called()
        self.assertIn('上次结果', ui.status.configure.call_args.kwargs['text'])

    def test_logs_do_not_echo_exception_text(self):
        from diagnostics import error_message
        logger = MagicMock()
        message = error_message(RuntimeError('sensitive password sql text'), logger)
        self.assertNotIn('sensitive', message)
        self.assertNotIn('sensitive', str(logger.mock_calls))


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Requires a desktop; Windows CI runs this')
class WindowTests(unittest.TestCase):
    def test_real_window_and_typed_form(self):
        with tempfile.TemporaryDirectory() as folder, patch('app.SETTINGS', Path(folder) / 'connection.json'), patch('app.configure_logging', return_value=MagicMock()):
            window = app.App()
            try:
                window.withdraw()
                window.update_idletasks()
                self.assertTrue(window.queries)
                window.choice.current(1)
                window.change_query()
                self.assertIn('start_time', window.parameters)
                window.set_busy(True)
                self.assertEqual(str(window.inputs[0]['state']), 'disabled')
                window.set_busy(False)
                self.assertEqual(str(window.inputs[0]['state']), 'normal')
                from updates_ui import UpdateWindow
                update = UpdateWindow(window, dict(host='localhost', port='3306', database='fixture', user='fixture', password=''),
                                      Path(__file__).parents[1] / 'updates.example.json', MagicMock())
                try:
                    update.update_idletasks()
                    self.assertEqual(len(update.operations), 1)
                    self.assertIn('channel_id', update.parameters)
                    self.assertNotIn('channel_id', update.modes)
                    update.modes['new_name'].set('数据库 NULL')
                    self.assertEqual(str(update.entries['new_name']['state']), 'disabled')
                    update.set_busy(True)
                    self.assertEqual(str(update.mode_widgets[0]['state']), 'disabled')
                    update.set_busy(False)
                    self.assertEqual(str(update.entries['new_name']['state']), 'disabled')
                    update.modes['new_name'].set('输入值')
                    self.assertEqual(str(update.entries['new_name']['state']), 'normal')
                    update.preview = MagicMock()
                    update.parameters['channel_id'].set('D2')
                    self.assertIsNone(update.preview)
                    self.assertEqual(str(update.submit_button['state']), 'disabled')
                finally:
                    update.close()
                from api_ui import ApiWindow
                api = ApiWindow(window, Path(__file__).parents[1] / 'api_requests.json', Path(folder) / 'api_profiles.json', MagicMock())
                try:
                    api.update_idletasks()
                    api.vars['base_url'].set('http://127.0.0.1:12345')
                    profile = api.profile()
                    self.assertEqual(profile['auth_type'], 'none')
                    api.preview()
                    self.assertIn('GET http://127.0.0.1:12345/', api.output.get('1.0', 'end'))
                    api.set_busy(True)
                    self.assertEqual(str(api.auth['state']), 'disabled')
                    api.set_busy(False)
                    self.assertEqual(str(api.auth['state']), 'readonly')
                    api.choice.current(1)
                    api.change_preset()
                    self.assertEqual(api.auth.get(), '登录后 Token')
                    self.assertIn('page', api.parameters)
                    api.choice.current(0)
                    api.change_preset()
                    self.assertEqual(api.vars['base_url'].get(), 'http://127.0.0.1:12345')
                    with patch('api_ui.messagebox.askyesno', return_value=False):
                        api.close()
                    self.assertTrue(api.winfo_exists())
                    with patch('api_config.protect', return_value='ciphertext'):
                        api.save_draft()
                    self.assertTrue((Path(folder) / 'api_drafts.json').exists())
                finally:
                    with patch('api_ui.messagebox.askyesno', return_value=True):
                        api.close()
            finally:
                window.destroy()
