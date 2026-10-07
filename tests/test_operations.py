import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from api_client import ApiClient,ApiResponse,ApiError
from operations import Audit,check_configs,backup_configs,restore_configs,export_support
from site_profiles import load_sites
from task_checks import validate_verification,verification_params,completion
from delete_review import confirm_large_delete
from types import SimpleNamespace

ROOT=Path(__file__).parents[1]


def files(folder):
    import shutil
    for name in ('queries.json','updates.json','api_requests.json','workflows.json','database_profiles.json','site_profiles.json','ops_settings.json'):
        shutil.copyfile(ROOT/name,Path(folder)/name)


class OperationsTests(unittest.TestCase):
    def test_unified_database_and_service_resolution(self):
        from database_profiles import load_profiles
        from api_config import load_api_requests
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            data=json.loads((ROOT/'site_profiles.example.json').read_text())
            (path/'site_profiles.json').write_text(json.dumps(data),encoding='utf-8')
            db,_=load_profiles(path/'database_profiles.json')
            self.assertEqual(db[0]['id'],'limin')
            operation=dict(name='WVP',profile_name='WVP',site_service='WVP',profile={},request=dict(method='GET',path='/api/server/config'),params=[])
            (path/'api_requests.json').write_text(json.dumps([operation]))
            op=load_api_requests(path/'api_requests.json')[0]
            self.assertEqual(op['connections'][0]['profile']['login']['method'],'GET')
            self.assertEqual(op['connections'][0]['id'],db[0]['id'])
            operation['connections']=[dict(id='one',name='One',profile={'base_url':'http://localhost'})]
            (path/'api_requests.json').write_text(json.dumps([operation]))
            with self.assertRaises(ValueError):load_api_requests(path/'api_requests.json')

    def test_invalid_central_file_is_recoverable_profile_error(self):
        from database_profiles import load_profiles,ProfileError
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'site_profiles.json').write_text('{\n invalid')
            with self.assertRaisesRegex(ProfileError,'site_profiles.json.*第 2 行'):
                load_profiles(path/'database_profiles.json')

    def test_config_checks_and_diagnostic_exports_exclude_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);files(path)
            self.assertTrue(all(r['ok'] for r in check_configs(path)))
            (path/'api_requests.json').write_text('{bad secret-password')
            audit=Audit(path);audit.record('api','failed');audit.record('SQL-secret','password-secret')
            audit.path.write_text(audit.path.read_text()+json.dumps(dict(time='2026-10-07T01:00:00+00:00',id='abcdefabcdef',event='api',status='ok',password='do-not-export'))+'\n{broken\n')
            export_support(path,audit,path/'support.json')
            output=(path/'support.json').read_text()
            self.assertNotIn('secret-password',output)
            self.assertNotIn('do-not-export',output)
            self.assertNotIn('SQL-secret',output)
            self.assertIn('configuration_checks',output)

    def test_encrypted_configuration_backup_restore_and_rejection(self):
        with tempfile.TemporaryDirectory() as folder,patch('operations.protect',side_effect=lambda s:'cipher:'+s),patch('operations.unprotect',side_effect=lambda s:s.removeprefix('cipher:')):
            path=Path(folder);files(path)
            backup_configs(path,path/'backup.json')
            old=(path/'updates.json').read_text()
            (path/'updates.json').write_text('[]\n')
            restore_configs(path,path/'backup.json')
            self.assertEqual((path/'updates.json').read_text(),old)
            payload={'version':1,'files':{'../escape.json':'{}'}}
            (path/'bad.json').write_text(json.dumps({'encrypted':'cipher:'+json.dumps(payload)}))
            with self.assertRaises(ValueError):restore_configs(path,path/'bad.json')
            payload['files']={'updates.json':'{invalid'}
            (path/'bad.json').write_text(json.dumps({'encrypted':'cipher:'+json.dumps(payload)}))
            with self.assertRaises(ValueError):restore_configs(path,path/'bad.json')
            self.assertEqual((path/'updates.json').read_text(),old)

    def test_large_delete_second_confirmation(self):
        preview=SimpleNamespace(operation={'compiled':{'kind':'delete'}},rows=[()] * 30)
        with patch('delete_review.simpledialog.askinteger',return_value=29):self.assertFalse(confirm_large_delete(None,preview))
        with patch('delete_review.simpledialog.askinteger',return_value=30):self.assertTrue(confirm_large_delete(None,preview))
        with patch('delete_review.simpledialog.askinteger') as ask:
            preview.rows=[()];self.assertTrue(confirm_large_delete(None,preview));ask.assert_not_called()

    def test_login_failure_details_redact_credentials_and_identify_method(self):
        client=ApiClient(dict(base_url='http://localhost',auth_type='login_token',password='private-password',key_name='access-token',login=dict(method='POST',path='/api/user/login',body_type='form',body={'password':'{{password_md5}}'})))
        response=ApiResponse(400,{'Content-Type':'application/json','access-token':'private-token'},json.dumps({'message':'Missing username','password':'private-password','token':'private-token'}).encode(),0.1)
        with patch.object(client,'send',return_value=response):
            with self.assertRaises(ApiError) as caught:client.login()
        message=str(caught.exception)
        self.assertIn('Missing username',message);self.assertIn('POST http://localhost/api/user/login',message)
        self.assertNotIn('private-password',message);self.assertNotIn('private-token',message)

    def test_task_check_requires_final_business_value_and_captures_id(self):
        op=dict(verification=dict(request=dict(method='GET',path='/tasks/{{task_id}}'),task_id_path='data.id',success=dict(path='data.status',equals='DONE')))
        validate_verification(op)
        response=ApiResponse(200,{},b'{"data":{"id":123,"status":"RUNNING"}}',0)
        self.assertEqual(verification_params(op,{},response)['task_id'],123)
        self.assertFalse(completion(response,op['verification']['success']))
        self.assertTrue(completion(ApiResponse(200,{},b'{"data":{"status":"DONE"}}',0),op['verification']['success']))
        op['verification']['request']['method']='POST'
        with self.assertRaises(ApiError):validate_verification(op)


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Windows CI runs real UI')
class OperationsWindowTests(unittest.TestCase):
    def test_customer_mode_hides_advanced_after_operations_and_task_check_gate(self):
        import app
        from workflows import WorkflowRun
        from workflow_ui import WorkflowPanel
        with tempfile.TemporaryDirectory() as folder,patch('app.ROOT',Path(folder)),patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
            files(folder);Path(folder,'ops_settings.json').write_text(json.dumps(dict(version=1,customer_mode=True,delete_confirm_threshold=20)))
            root=app.App();root.withdraw()
            try:
                self.assertEqual(root.navigation.tab(root.advanced_tab,'state'),'hidden')
                panel=root.workflow;panel.set_busy(True);panel.set_busy(False)
                self.assertEqual(root.navigation.tab(root.advanced_tab,'state'),'hidden')
                op=dict(name='任务测试',profile_name='Task',profile={'base_url':'http://localhost','auth_type':'none'},request={'method':'POST','path':'/trigger'},params=[],verification={'request':{'method':'GET','path':'/status'},'success':{'path':'done','equals':True}})
                panel.flows=[dict(id='tasks',name='任务',steps=[dict(id='one',title='任务',type='api',operation=op)])]
                panel.start_flow(0)
                response=ApiResponse(200,{},b'{"done":false}',0)
                client=MagicMock();client.send.return_value=response;client.display.return_value='running'
                context=dict(client=client,operation=op,params={})
                panel.jobs.put(('api','ok',(True,'accepted','accepted',True,context)));panel.consume_result()
                self.assertEqual(panel.run.states,['pending'])
                self.assertEqual(str(panel.next_button['state']),'disabled')
                self.assertEqual(str(panel.action_button['state']),'disabled')
                panel.jobs.put(('verify','ok',(False,'running')));panel.consume_result()
                self.assertEqual(panel.run.states,['pending'])
                panel.jobs.put(('verify','ok',(True,'done')));panel.consume_result()
                self.assertEqual(panel.run.states,['ready'])
                self.assertFalse(panel.pending_checks)
            finally:root.destroy()


class AdditionalOperationsTests(unittest.TestCase):
    def test_completion_correlates_exact_run_and_checks_error_field(self):
        from task_checks import task_completed
        op={'verification':{'request':{'method':'GET','path':'/status'},'success':{'path':'data.syncIng','equals':False},'conditions':[{'path':'data.errorMsg','equals':None}],'correlate':{'data.time':'data.time'}}}
        trigger=ApiResponse(200,{},b'{"data":{"time":"run-2"}}',0)
        params=verification_params(op,{},trigger)
        def response(run,error=None):return ApiResponse(200,{},json.dumps({'data':{'time':run,'errorMsg':error,'syncIng':False}}).encode(),0)
        self.assertFalse(task_completed(response('run-1'),op['verification'],params))
        self.assertFalse(task_completed(response('run-2','failed'),op['verification'],params))
        self.assertTrue(task_completed(response('run-2'),op['verification'],params))

    def test_restore_write_failure_rolls_back_previous_files(self):
        import operations
        with tempfile.TemporaryDirectory() as folder,patch('operations.unprotect',side_effect=lambda s:s):
            path=Path(folder);files(path)
            previous={name:(path/name).read_bytes() for name in ('ops_settings.json','updates.json')}
            backup=path/'backup.json'
            backup.write_text(json.dumps({'encrypted':json.dumps({'version':1,'files':{'ops_settings.json':'{"version":1,"customer_mode":true}','updates.json':'[]'}})}))
            real=operations.atomic_text
            def write(file,text):
                if Path(file).name=='updates.json':raise OSError('fixture disk error')
                real(file,text)
            with patch('operations.atomic_text',side_effect=write):
                with self.assertRaises(OSError):restore_configs(path,backup)
            for name,data in previous.items():self.assertEqual((path/name).read_bytes(),data)
