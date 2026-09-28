import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from settings import load_settings, save_settings, protect, unprotect


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'connection.json'
        self.config = dict(host='10.0.0.1', port='3306', database='video', user='reader', password=' 密码-secret ', ssl_ca='')

    def test_new_and_legacy(self):
        self.assertEqual(load_settings(self.path), ({}, ''))
        self.path.write_text(json.dumps({'host': 'server', 'user': 'reader'}))
        data, warning = load_settings(self.path)
        self.assertEqual(data['host'], 'server')
        self.assertEqual(data['password'], '')
        self.assertFalse(warning)

    def test_save_load_and_forget(self):
        with patch('settings.protect', return_value='encrypted') as encrypt:
            save_settings(self.path, self.config, True)
            encrypt.assert_called_once_with(self.config['password'])
        data = json.loads(self.path.read_text())
        self.assertNotIn('password', data)
        self.assertNotIn('secret', self.path.read_text())
        with patch('settings.unprotect', return_value=self.config['password']):
            restored, warning = load_settings(self.path)
        self.assertEqual(restored['password'], self.config['password'])
        self.assertFalse(warning)
        save_settings(self.path, self.config, False)
        self.assertNotIn('password_dpapi', self.path.read_text())
        self.assertFalse(load_settings(self.path)[0]['remember_password'])

    def test_encrypt_failure_preserves_previous_settings(self):
        save_settings(self.path, self.config, False)
        previous = self.path.read_bytes()
        with patch('settings.protect', side_effect=OSError('failure')):
            with self.assertRaises(OSError):
                save_settings(self.path, self.config, True)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_unreadable_password_keeps_connection(self):
        with patch('settings.protect', return_value='broken'):
            save_settings(self.path, self.config, True)
        with patch('settings.unprotect', side_effect=OSError('wrong user')):
            restored, warning = load_settings(self.path)
        self.assertEqual(restored['host'], self.config['host'])
        self.assertEqual(restored['password'], '')
        self.assertTrue(warning)

    @unittest.skipUnless(os.name == 'nt', 'Real DPAPI requires Windows; runs in Windows CI')
    def test_real_dpapi_across_process_restart(self):
        for password in ('', ' 密码-secret 🔒 '):
            self.config['password'] = password
            save_settings(self.path, self.config, True)
            command = 'import json,sys; from settings import load_settings; print(json.dumps(load_settings(sys.argv[1])[0]["password"]))'
            result = subprocess.check_output([sys.executable, '-c', command, str(self.path)], text=True)
            self.assertEqual(json.loads(result), password)
        with self.assertRaises((OSError, ValueError)):
            unprotect('bm90LWEtZHBhcGktYmxvYg==')


if __name__ == '__main__':
    unittest.main()
