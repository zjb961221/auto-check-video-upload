import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from api_connections import validate_connections,resolve_connection
from api_client import ApiError


def preset():
    return dict(name='执行任务',profile_name='XXL-JOB',profile=dict(auth_type='login_cookie',username='old-user',password='old-password',login=dict(method='POST',path='/login',body_type='form',body=dict(userName='{{username}}',password='{{password}}'),success_path='code',success_value=200)),
                connections=[dict(id='a',name='甲矿',profile=dict(base_url='http://127.0.0.1:18080/admin',username='a',password='password-a')),
                             dict(id='b',name='乙矿',profile=dict(base_url='http://127.0.0.1:28080/admin',username='b'))],
                request=dict(method='POST',path='/jobinfo/trigger',body_type='form',body=dict(id='{{id}}'),confirm=True),
                params=[dict(name='id',label='任务ID',type='integer',default='123')])


class ConnectionTests(unittest.TestCase):
    def test_service_and_mine_isolation_no_inherited_secrets(self):
        p=preset();validate_connections(p)
        a,ka,da=resolve_connection(p,'a');b,kb,db=resolve_connection(p,'b')
        self.assertEqual(a['password'],'password-a')
        self.assertNotIn('password',b)
        self.assertEqual(b['login']['path'],'/login')
        self.assertNotEqual(ka,kb);self.assertNotEqual(da,db)
        other=p|{'profile_name':'WVP'}
        self.assertNotEqual(resolve_connection(other,'a')[1],ka)
        self.assertNotIn('password-a',ka)

    def test_changed_config_invalidates_saved_connection_and_draft_keys(self):
        p=preset();before=resolve_connection(p,'a')[1:]
        p['connections'][0]['profile']['base_url']='http://127.0.0.1:38080/'
        self.assertNotEqual(resolve_connection(p,'a')[1:],before)
        with self.assertRaises(ApiError):resolve_connection(p,'missing')

    def test_validation_duplicates_invalid_default_and_bad_fields(self):
        for mutate in [lambda p:p.update(default_connection='missing'),lambda p:p.update(connections='bad'),
                       lambda p:p['connections'].append(p['connections'][0]),
                       lambda p:p['connections'][0]['profile'].update(password=None),
                       lambda p:p['connections'][0]['profile'].pop('base_url')]:
            p=preset();mutate(p)
            with self.assertRaises(ApiError):validate_connections(p)

    def test_existing_config_compatibility(self):
        p=preset();p.pop('connections');validate_connections(p)
        self.assertEqual(resolve_connection(p)[1:],('XXL-JOB','执行任务'))


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Actual Tk tests run on Windows')
class ConnectionWindowTests(unittest.TestCase):
    def test_both_selectors_clear_sessions_and_isolate_local_saves(self):
        import app
        from api_ui import ApiWindow
        from api_config import load_api_requests
        with tempfile.TemporaryDirectory() as folder,patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
            path=Path(folder)/'api_requests.json';p=preset();path.write_text(json.dumps([p]),encoding='utf-8')
            op=load_api_requests(path)[0]
            root=app.App();root.withdraw()
            ui=ApiWindow(root,path,Path(folder)/'api_profiles.json',MagicMock())
            try:
                ui.mine_choice.set('甲矿');ui.select_mine()
                self.assertEqual(ui.vars['password'].get(),'password-a')
                ui.client=object();ui.client_key='old'
                key_a=ui.connection_key()
                ui.vars['password'].set('local-a');ui.remember.set(True)
                ui.save()
                ui.mine_choice.set('乙矿');ui.select_mine()
                self.assertIsNone(ui.client)
                self.assertEqual(ui.vars['password'].get(),'')
                self.assertEqual(ui.vars['username'].get(),'b')
                self.assertNotEqual(ui.connection_key(),key_a)
                ui.mine_choice.set('甲矿');ui.select_mine()
                self.assertEqual(ui.vars['password'].get(),'local-a')
                ui.set_busy(True);self.assertEqual(str(ui.mine_choice['state']),'disabled');ui.set_busy(False)
                panel=root.workflow
                panel.flows=[dict(id='test',name='测试',steps=[dict(id='api',title='任务',type='api',operation=op)])]
                panel.start_flow(0)
                panel.api_mine_choice.set('甲矿');panel.select_api_mine()
                self.assertEqual(panel.api_vars['password'].get(),'local-a')
                panel.clients['cached']=object();panel.run.ready()
                panel.api_mine_choice.set('乙矿');panel.select_api_mine()
                self.assertEqual(panel.clients,{})
                self.assertEqual(panel.api_vars['password'].get(),'')
                self.assertEqual(panel.run.states,['pending'])
                self.assertEqual(str(panel.next_button['state']),'disabled')
                panel.run.states[0]='uncertain'
                panel.api_mine_choice.set('甲矿');panel.select_api_mine()
                self.assertEqual(panel.api_mine_choice.get(),'乙矿')
                self.assertEqual(panel.run.states,['uncertain'])
            finally:
                with patch('api_ui.messagebox.askyesno',return_value=True):ui.close()
                root.destroy()
