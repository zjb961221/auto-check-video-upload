import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from api_drafts import Drafts


class DraftTests(unittest.TestCase):
    def snapshot(self, request='draft password'):
        return dict(fields={'password': 'private'}, parameters={'id': 'D1'}, auth='无鉴权',
                    remember=False, system_proxy=False, request=request, login='{}')

    def test_switch_isolated_and_save_failure_preserves_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = Drafts(Path(folder)/'drafts.json')
            first = drafts.open('A', self.snapshot())
            self.assertFalse(drafts.dirty())
            first['request'] = 'unsaved'
            drafts.capture('A', first)
            drafts.open('B', self.snapshot('B'))
            self.assertEqual(drafts.open('A', self.snapshot())['request'], 'unsaved')
            self.assertTrue(drafts.dirty())
            with patch('api_config.protect', side_effect=OSError('unavailable')):
                with self.assertRaises(OSError):
                    drafts.save('A', first)
            self.assertTrue(drafts.dirty())
            self.assertFalse(drafts.store.path.exists())

    def test_encrypted_roundtrip_and_independent_dirty_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'drafts.json'
            drafts = Drafts(path)
            value = drafts.open('A', self.snapshot())
            value['request'] = 'changed'
            with patch('api_config.protect', return_value='cipher') as encrypt:
                drafts.save('A', value)
            encrypted_plaintext = encrypt.call_args.args[0]
            self.assertNotIn('private', path.read_text())
            self.assertNotIn('changed', path.read_text())
            self.assertFalse(drafts.dirty())
            with patch('api_config.unprotect', return_value=encrypted_plaintext):
                self.assertEqual(Drafts(path).open('A', self.snapshot()), value)
            drafts.open('B', self.snapshot())
            drafts.capture('B', self.snapshot('unsaved B'))
            self.assertTrue(drafts.dirty())

    def test_invalid_encrypted_draft_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = Drafts(Path(folder)/'drafts.json')
            with patch.object(drafts.store, 'load', return_value={'draft': {'fields': []}}):
                with self.assertRaises(ValueError):
                    drafts.open('A', self.snapshot())
