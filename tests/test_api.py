import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import parse
from api_client import ApiClient, ApiError, MAX_RESPONSE, template
from api_config import load_api_requests, bind_api_parameters, ProfileStore


class FixtureHandler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self,*args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.calls.append((self.command,self.path,dict(self.headers),body))
        path=parse.urlsplit(self.path).path
        status,headers,payload=200,{},{}
        if path=='/login-token':
            headers['access-token']='fixture-session-secret'
            payload={'accessToken':'fixture-session-secret'}
        elif path=='/login-cookie':
            headers['Set-Cookie']='SID=fixture-cookie-secret; Path=/; HttpOnly'
            payload={'code':200}
        elif path=='/bad-login':
            payload={'code':500}
            headers['Set-Cookie']='SID=bad; Path=/'
        elif path=='/redirect':
            status=302
            headers['Location']=self.server.redirect_target
        elif path=='/error':
            status=401
            payload={'message':'unauthorized','password':'echo-secret'}
        elif path=='/large':
            payload=b'x'*(MAX_RESPONSE+1)
        else:
            payload={'path':self.path,'headers':dict(self.headers),'body':body.decode('utf-8')}
        data=payload if isinstance(payload,bytes) else json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        for key,value in headers.items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(data)


class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
        cls.other=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        threading.Thread(target=cls.other.serve_forever,daemon=True).start()
        cls.server.redirect_target=f'http://127.0.0.1:{cls.other.server_port}/must-not-receive'

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server,cls.other):
            server.shutdown()
            server.server_close()

    def setUp(self):
        FixtureHandler.calls=[]

    def profile(self,**kwargs):
        return dict(base_url=self.base,auth_type='none',timeout=5,**kwargs)

    def test_query_path_and_json_types(self):
        client=ApiClient(self.profile())
        spec=dict(method='POST',path='/echo/{{id}}',query={'q':'{{q}}'},body_type='json',body={'n':'{{n}}','enabled':'{{enabled}}'})
        response=client.send(spec,dict(id='a/b',q='中文 &?',n=5,enabled=True))
        self.assertEqual(response.status,200)
        call=FixtureHandler.calls[-1]
        self.assertIn('/echo/a%2Fb',call[1])
        self.assertEqual(parse.parse_qs(parse.urlsplit(call[1]).query)['q'],['中文 &?'])
        self.assertEqual(json.loads(call[3]),{'n':5,'enabled':True})

    def test_auth_modes(self):
        for auth in ('bearer','api_header','api_query','basic'):
            profile=self.profile(token='fixture-key',key_name='X-Test-Key',username='admin',password='password')
            profile['auth_type']=auth
            client=ApiClient(profile)
            client.send(dict(path='/echo'))
            _,url,headers,_=FixtureHandler.calls[-1]
            if auth=='bearer':
                self.assertEqual(headers['Authorization'],'Bearer fixture-key')
            elif auth=='api_header':
                self.assertEqual(headers['X-Test-Key'],'fixture-key')
            elif auth=='api_query':
                self.assertEqual(parse.parse_qs(parse.urlsplit(url).query)['X-Test-Key'],['fixture-key'])
            else:
                self.assertEqual(base64.b64decode(headers['Authorization'].split()[1]).decode(),'admin:password')

    def test_login_token_md5_and_redaction(self):
        profile=self.profile(username='admin',password='original',key_name='access-token',login=dict(method='POST',path='/login-token',body_type='form',body={'username':'{{username}}','password':'{{password_md5}}'},token_header='access-token'))
        profile['auth_type']='login_token'
        client=ApiClient(profile)
        with self.assertRaises(ApiError):
            client.send(dict(path='/echo'))
        client.login()
        self.assertEqual(parse.parse_qs(FixtureHandler.calls[-1][3].decode())['password'],[hashlib.md5(b'original').hexdigest()])
        response=client.send(dict(path='/echo'))
        self.assertEqual(FixtureHandler.calls[-1][2]['Access-Token'],'fixture-session-secret')
        self.assertNotIn('fixture-session-secret',client.display(response))
        self.assertNotIn('fixture-session-secret',client.request_display(client.prepare(dict(path='/echo'))))

    def test_cookie_session_and_business_login_failure(self):
        profile=self.profile(login=dict(method='POST',path='/login-cookie',success_path='code',success_value=200))
        profile['auth_type']='login_cookie'
        client=ApiClient(profile)
        client.login()
        client.send(dict(path='/echo'))
        self.assertIn('SID=fixture-cookie-secret',FixtureHandler.calls[-1][2]['Cookie'])
        client.profile['login']['path']='/bad-login'
        with self.assertRaises(ApiError):
            client.login()
        self.assertFalse(client.logged_in)
        self.assertEqual(list(client.cookies),[])

    def test_redirect_does_not_leak_or_replay(self):
        profile=self.profile(token='private')
        profile['auth_type']='bearer'
        response=ApiClient(profile).send(dict(method='POST',path='/redirect',body_type='json',body={'action':'fixture'}))
        self.assertEqual(response.status,302)
        self.assertEqual(len(FixtureHandler.calls),1)

    def test_http_error_response_and_size_limit(self):
        client=ApiClient(self.profile())
        response=client.send(dict(path='/error'))
        self.assertEqual(response.status,401)
        self.assertNotIn('echo-secret',client.display(response))
        with self.assertRaises(ApiError):
            client.send(dict(path='/large'))

    def test_request_preview_redacts_form_and_query_keys(self):
        client=ApiClient(self.profile())
        req=client.prepare(dict(method='POST',path='/echo',query={'access_token':'hidden-one'},body_type='form',body={'password':'hidden-two'}))
        display=client.request_display(req)
        self.assertNotIn('hidden-one',display)
        self.assertNotIn('hidden-two',display)
        self.assertEqual(FixtureHandler.calls,[])


class ApiConfigTests(unittest.TestCase):
    def test_shipped_catalogue(self):
        presets=load_api_requests(Path(__file__).parents[1]/'api_requests.json')
        self.assertTrue(presets)
        typed=next(p for p in presets if p.get('params'))
        self.assertEqual(bind_api_parameters(typed,{'page':'2','count':'10'})['page'],2)

    def test_template_missing_and_type_preservation(self):
        self.assertEqual(template({'n':'{{n}}','text':'ID {{n}}'},{'n':5}),{'n':5,'text':'ID 5'})
        with self.assertRaises(ApiError):
            template('{{missing}}',{})

    def test_url_credentials_and_header_injection_rejected(self):
        for url in ('ftp://localhost','http://user:secret@localhost','http://localhost?token=x'):
            with self.assertRaises(ApiError):
                ApiClient({'base_url':url})
        client=ApiClient({'base_url':'http://localhost'})
        with self.assertRaises(ApiError):
            client.prepare(dict(path='https://other/echo'))
        with self.assertRaises(ApiError):
            client.prepare(dict(path='/',headers={'X-Header':'x\r\nInjected: x'}))

    def test_profiles_encrypted_and_forget_does_not_leave_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProfileStore(Path(folder)/'profiles.json')
            profile=dict(base_url='http://localhost',password='private-password',token='private-token',login={'body':{'secret':'private-body'}})
            with patch('api_config.protect',return_value='ciphertext'):
                store.save('fixture',profile,True)
            text=store.path.read_text()
            self.assertNotIn('private',text)
            with patch('api_config.unprotect',return_value=json.dumps(profile)):
                self.assertEqual(store.load('fixture')['token'],'private-token')
            store.save('fixture',profile,False)
            self.assertNotIn('private',store.path.read_text())
            self.assertNotIn('encrypted',store.path.read_text())
            self.assertEqual(store.load('fixture')['base_url'],'http://localhost')
