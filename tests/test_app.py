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
            finally:
                window.destroy()
