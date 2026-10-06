import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from database_profiles import load_profiles, LocalProfiles, ProfileError, resolve_profile

MINE_A = dict(id='limin', name='利民煤矿', host='10.0.0.9', port=3306, database='wvp_a', user='ops_a', password='fixture-a')
MINE_B = dict(id='huangbaici', name='黄白茨煤矿', host='10.0.0.10', port=3307, database='wvp_b', user='ops_b')


class CatalogueTests(unittest.TestCase):
    def write(self, folder, profiles=None, **extra):
        path=Path(folder)/'database_profiles.json'
        path.write_text(json.dumps(dict(version=1, profiles=profiles or [MINE_A,MINE_B], **extra)),encoding='utf-8-sig')
        return path

    def test_normalization_defaults_and_relative_ca(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write(folder,[MINE_A|{'ssl_ca':'certs/ca.pem'}],default_profile='limin')
            profiles,default=load_profiles(path)
            self.assertEqual(default,'limin')
            self.assertEqual(profiles[0]['port'],'3306')
            self.assertEqual(profiles[0]['ssl_ca'],str((Path(folder)/'certs/ca.pem').resolve()))
            self.assertEqual(load_profiles(Path(folder)/'missing.json'),([],''))

    def test_invalid_and_duplicate_catalogue_never_exposes_password(self):
        with tempfile.TemporaryDirectory() as folder:
            for profiles in [[MINE_A,MINE_A], [MINE_A,MINE_B|{'name':MINE_A['name']}],
                             [MINE_A|{'port':True}], [MINE_A|{'port':0}], [MINE_A|{'host':''}],
                             [MINE_A|{'password':None}], [MINE_A|{'password_env':'SECRET'}],
                             [MINE_A|{'id':'bad id'}], [MINE_A|{'passwrod':'fixture-secret'}]]:
                with self.subTest(profiles=profiles),self.assertRaises(ProfileError) as caught:
                    load_profiles(self.write(folder,profiles))
                self.assertNotIn('fixture',str(caught.exception))
            with self.assertRaises(ProfileError):
                load_profiles(self.write(folder,default_profile='missing'))
            path=Path(folder)/'database_profiles.json';path.write_text('{bad')
            with self.assertRaises(ProfileError):
                load_profiles(path)

    def test_environment_password_and_missing_environment(self):
        profile={k:v for k,v in MINE_A.items() if k!='password'}|{'password_env':'MINE_TEST_PASSWORD'}
        with patch.dict(os.environ,{'MINE_TEST_PASSWORD':'env-secret'}):
            self.assertEqual(resolve_profile(profile)[0]['password'],'env-secret')
        with patch.dict(os.environ,{},clear=True):
            config,warning=resolve_profile(profile)
            self.assertEqual(config['password'],'')
            self.assertIn('环境变量未设置',warning)


class LocalProfileTests(unittest.TestCase):
    def test_per_mine_encrypted_roundtrip_and_public_password_removal(self):
        with tempfile.TemporaryDirectory() as folder,patch('database_profiles.protect',side_effect=lambda s:'encrypted:'+s),patch('database_profiles.unprotect',side_effect=lambda s:s.removeprefix('encrypted:')):
            store=LocalProfiles(Path(folder)/'local.json')
            # The fake cipher is test-only; assert real writer never creates a password field outside ciphertext.
            store.save(MINE_A,resolve_profile(MINE_A)[0]|{'password':'local-a'},True)
            store.save(MINE_B,resolve_profile(MINE_B)[0]|{'password':'local-b'},True)
            self.assertEqual(store.load(MINE_A)[0]['password'],'local-a')
            self.assertEqual(store.load(MINE_B)[0]['password'],'local-b')
            self.assertEqual(set(store.read()['profiles']['limin']),{'encrypted'})
            store.save(MINE_A,resolve_profile(MINE_A)[0],False)
            self.assertEqual(store.load(MINE_A)[0]['password'],'')
            self.assertNotIn('fixture-a',json.dumps(store.read()['profiles']['limin']))
            self.assertEqual(store.load(MINE_B)[0]['password'],'local-b')

    def test_changed_catalogue_invalidates_old_override(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LocalProfiles(Path(folder)/'local.json')
            store.save(MINE_A,resolve_profile(MINE_A)[0]|{'host':'local-override'},False)
            self.assertEqual(store.load(MINE_A)[0]['host'],'local-override')
            config,warning=store.load(MINE_A|{'host':'new-server','password':'new-password'})
            self.assertEqual(config['host'],'new-server')
            self.assertEqual(config['password'],'new-password')
            self.assertIn('文件已变化',warning)

    def test_decrypt_failure_clears_password(self):
        with tempfile.TemporaryDirectory() as folder,patch('database_profiles.protect',return_value='cipher'):
            store=LocalProfiles(Path(folder)/'local.json')
            store.save(MINE_A,resolve_profile(MINE_A)[0],True)
            with patch('database_profiles.unprotect',side_effect=OSError('sensitive-password')):
                config,warning=store.load(MINE_A)
            self.assertEqual(config['password'],'')
            self.assertEqual(config['host'],MINE_A['host'])
            self.assertNotIn('sensitive',warning)

    def test_encryption_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LocalProfiles(Path(folder)/'local.json')
            store.save(MINE_A,resolve_profile(MINE_A)[0],False)
            previous=store.path.read_bytes()
            with patch('database_profiles.protect',side_effect=OSError('unavailable')):
                with self.assertRaises(OSError):
                    store.save(MINE_A,resolve_profile(MINE_A)[0],True)
            self.assertEqual(store.path.read_bytes(),previous)


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Actual Windows desktop exercised by CI')
class MineWindowTests(unittest.TestCase):
    def setup_files(self,folder):
        import app
        from shutil import copyfile
        root=Path(folder)
        for filename in ('queries.json','updates.json','api_requests.json','workflows.json'):
            copyfile(Path(app.__file__).parent/filename,root/filename)
        (root/'database_profiles.json').write_text(json.dumps(dict(version=1,profiles=[MINE_A,MINE_B],default_profile='limin')),encoding='utf-8')
        return root

    def test_selection_both_forms_busy_invalidation_and_local_restore(self):
        import app
        from workflows import WorkflowRun
        with tempfile.TemporaryDirectory() as folder:
            rootpath=self.setup_files(folder)
            with patch('app.ROOT',rootpath),patch('app.SETTINGS',rootpath/'connection.json'),patch('app.configure_logging',return_value=MagicMock()),patch('app.test_connection') as connect:
                root=app.App();root.withdraw()
                try:
                    self.assertEqual(root.vars['password'].get(),'fixture-a')
                    self.assertEqual(root.vars['host'].get(),'10.0.0.9')
                    connect.assert_not_called()
                    panel=root.workflow
                    for check in panel.checks:check.set(True)
                    panel.note_changed();panel.next()
                    self.assertEqual(len([c for c in root.mines.combos if c.winfo_exists()]),2)
                    panel.run.ready()
                    panel.preview=object()
                    root.mines.name.set('黄白茨煤矿');root.mines.choose()
                    self.assertEqual(root.vars['host'].get(),'10.0.0.10')
                    self.assertEqual(root.vars['password'].get(),'')
                    self.assertIsNone(panel.preview)
                    self.assertNotEqual(panel.run.states[1],'ready')
                    self.assertTrue(all(c.get()=='黄白茨煤矿' for c in root.mines.combos if c.winfo_exists()))
                    panel.set_busy(True)
                    self.assertTrue(all(str(c['state'])=='disabled' for c in root.mines.combos if c.winfo_exists()))
                    old=root.vars['host'].get()
                    root.mines.name.set('利民煤矿');root.mines.choose()
                    self.assertEqual(root.vars['host'].get(),old)
                    panel.set_busy(False)
                    root.vars['password'].set('customer-b')
                    root.remember_password.set(True)
                    root.mines.save()
                    root.mines.name.set('利民煤矿');root.mines.choose()
                    self.assertEqual(root.vars['password'].get(),'fixture-a')
                    root.mines.name.set('黄白茨煤矿');root.mines.choose()
                    self.assertEqual(root.vars['password'].get(),'customer-b')
                    # Dirty cancellation keeps the previous mine and every edited field.
                    root.vars['host'].set('edited-b')
                    with patch('mine_widgets.messagebox.askyesno',return_value=False):
                        root.mines.name.set('利民煤矿');root.mines.choose()
                    self.assertEqual(root.mines.active_id,'huangbaici')
                    self.assertEqual(root.vars['host'].get(),'edited-b')
                    # Invalid reload retains the catalogue and credentials.
                    with patch('mine_widgets.messagebox.askyesno',return_value=True),patch('mine_widgets.messagebox.showerror'):
                        (rootpath/'database_profiles.json').write_text('{bad')
                        root.mines.reload()
                    self.assertEqual(root.vars['password'].get(),'customer-b')
                    (rootpath/'database_profiles.json').write_text(json.dumps(dict(version=1,profiles=[MINE_A,MINE_B])),encoding='utf-8')
                finally:
                    root.destroy()
                restored=app.App();restored.withdraw()
                try:
                    self.assertEqual(restored.mines.active_id,'huangbaici')
                    self.assertEqual(restored.vars['password'].get(),'customer-b')
                finally:
                    restored.destroy()

    def test_api_default_address_in_both_forms(self):
        import app
        from api_ui import ApiWindow
        with tempfile.TemporaryDirectory() as folder:
            rootpath=self.setup_files(folder)
            preset=dict(name='API默认地址测试',profile_name='测试服务',profile=dict(base_url='http://127.0.0.1:18080/xxl-job-admin',auth_type='none'),request=dict(method='GET',path='/',body_type='none'),params=[])
            path=rootpath/'api_requests.json';path.write_text(json.dumps([preset]),encoding='utf-8')
            # Preserve shipped workflow references at root startup; use preset directly after initialization.
            from shutil import copyfile
            copyfile(Path(app.__file__).parent/'api_requests.json',path)
            with patch('app.ROOT',rootpath),patch('app.SETTINGS',rootpath/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
                root=app.App();root.withdraw()
                try:
                    panel=root.workflow
                    panel.flows=[dict(id='api',name='API',steps=[dict(id='one',title='接口',type='api',operation=preset)])]
                    panel.start_flow(0)
                    self.assertEqual(panel.api_vars['base_url'].get(),preset['profile']['base_url'])
                    path.write_text(json.dumps([preset]),encoding='utf-8')
                    ui=ApiWindow(root,path,rootpath/'api_profiles.json',MagicMock())
                    try:
                        self.assertEqual(ui.vars['base_url'].get(),preset['profile']['base_url'])
                    finally:
                        with patch('api_ui.messagebox.askyesno',return_value=True):ui.close()
                finally:root.destroy()
