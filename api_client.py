"""HTTP client with explicit auth, bounded responses and no automatic replay."""
import base64
import codecs
from email.message import Message
from dataclasses import dataclass
import hashlib
import http.cookiejar
import json
import re
import socket
import ssl
import time
from urllib import error, parse, request

MAX_RESPONSE = 2 * 1024 * 1024
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
AUTH_TYPES = {'none', 'bearer', 'api_header', 'api_query', 'basic', 'login_token', 'login_cookie'}
TOKEN = re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}')
SECRET_NAME = re.compile(r'password|passwd|secret|token|authorization|cookie|api.?key', re.I)


class ApiError(ValueError):
    pass


def http_error_reason(status):
    reasons = {
        401: '登录凭据或 Token 无效，请核对账号密码并重新登录',
        403: '当前账号没有接口权限，请联系服务管理员授权',
        404: '接口路径不存在，请核对应用路径及现场版本',
        405: '请求方法不被支持，请核对 GET/POST 等方法配置',
        408: '服务端请求超时，请检查服务负载；修改操作需先核实结果',
        429: '请求过于频繁，请等待服务端允许的间隔后再操作',
        502: '网关未获得正常响应，请检查反向代理及后端服务',
        503: '服务暂不可用，请检查服务运行状态和负载',
        504: '网关等待后端超时；修改操作需先核实是否已执行',
    }
    reason = reasons.get(status, '服务端返回失败，请联系服务管理员检查日志' if status >= 500 else
                         '响应发生重定向，请核对服务地址、登录状态和接口路径' if 300 <= status < 400 else
                         '请求未被接受，请核对参数和服务端响应')
    return f'HTTP {status}：{reason}。本步骤未完成。'


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials or replay writes to a redirected URL.


def template(value, variables, path=False):
    if isinstance(value, dict):
        return {k: template(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [template(v, variables) for v in value]
    if not isinstance(value, str):
        return value
    def lookup(match):
        if match[1] not in variables:
            raise ApiError(f'缺少模板参数：{match[1]}')
        v = variables[match[1]]
        return parse.quote(str(v), safe='') if path else str(v)
    whole = TOKEN.fullmatch(value)
    if whole and not path:
        if whole[1] not in variables:
            raise ApiError(f'缺少模板参数：{whole[1]}')
        return variables[whole[1]]
    return TOKEN.sub(lookup, value)


def json_path(value, path):
    try:
        for part in path.split('.') if path else []:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError):
        raise ApiError('登录返回结果中找不到配置的字段，请核对 token_path / success_path') from None


def validate_profile(profile):
    base = profile.get('base_url', '').strip().rstrip('/')
    url = parse.urlsplit(base)
    try:
        port = url.port
    except ValueError:
        raise ApiError('服务端口无效') from None
    if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
        raise ApiError('服务地址必须为 http(s)://主机[:端口][/路径]，不能包含账号、查询串或片段')
    auth = profile.get('auth_type', 'none')
    if auth not in AUTH_TYPES:
        raise ApiError('不支持的鉴权类型')
    try:
        timeout = int(profile.get('timeout', 20))
    except (ValueError, TypeError):
        raise ApiError('超时必须是 1–120 秒的整数') from None
    if not 1 <= timeout <= 120:
        raise ApiError('超时必须是 1–120 秒的整数')
    return dict(profile, base_url=base, timeout=timeout, auth_type=auth)


def redact(value, secrets=(), names=()):
    sensitive_names = {n.lower() for n in names}
    if isinstance(value, dict):
        return {k: '***' if SECRET_NAME.search(str(k)) or str(k).lower() in sensitive_names else redact(v, secrets, names) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets, names) for v in value]
    if isinstance(value, str):
        for secret in sorted({str(s) for s in secrets if s is not None and str(s)}, key=len, reverse=True):
            value = value.replace(secret, '***')
        return value
    return value


@dataclass
class ApiResponse:
    status: int
    headers: dict
    body: bytes
    elapsed: float

    def text(self):
        message = Message()
        message['Content-Type'] = next((v for k,v in self.headers.items() if k.lower() == 'content-type'), '')
        charset = message.get_content_charset() or 'utf-8-sig'
        try:
            codecs.lookup(charset)
        except LookupError:
            charset = 'utf-8-sig'
        return self.body.decode(charset, errors='replace')


class ApiClient:
    def __init__(self, profile):
        self.profile = validate_profile(profile)
        self.cookies = http.cookiejar.CookieJar()
        ca = self.profile.get('ca_file', '').strip()
        try:
            context = ssl.create_default_context(cafile=ca or None)
        except (OSError, ssl.SSLError):
            raise ApiError('无法加载 CA 证书，请检查证书路径和格式') from None
        proxy = request.ProxyHandler() if self.profile.get('system_proxy', False) else request.ProxyHandler({})
        self.opener = request.build_opener(proxy, request.HTTPSHandler(context=context),
                                          request.HTTPCookieProcessor(self.cookies), NoRedirect())
        self.session_token = ''
        self.logged_in = False

    def variables(self, parameters=None):
        password = self.profile.get('password', '')
        return dict(parameters or {}, username=self.profile.get('username', ''), password=password,
                    password_md5=hashlib.md5(password.encode('utf-8')).hexdigest(),
                    token=self.session_token or self.profile.get('token', ''))

    def secret_values(self, parameters=None):
        secrets = [self.profile.get('password'), self.profile.get('token'), self.session_token]
        if self.profile.get('password'):
            secrets.append(self.variables()['password_md5'])
            raw = f"{self.profile.get('username', '')}:{self.profile['password']}"
            secrets.append(base64.b64encode(raw.encode('utf-8')).decode('ascii'))
        secrets.extend(cookie.value for cookie in self.cookies)
        if parameters:
            secrets.extend(parameters)
        # Also mask credentials if an API echoes URL/form-encoded values.
        return secrets + [parse.quote(str(s), safe='') for s in secrets if s]

    def prepare(self, spec, parameters=None, login=False):
        variables = self.variables(parameters)
        method = spec.get('method', 'GET').upper()
        if method not in METHODS:
            raise ApiError('不支持的 HTTP 方法')
        path = template(spec.get('path', ''), variables, path=True)
        if not isinstance(path, str) or path.startswith(('http:', 'https:', '//')) or '#' in path:
            raise ApiError('接口 path 必须为服务下的相对路径，不能指向其他主机')
        url = self.profile['base_url'] + '/' + path.lstrip('/')
        query = template(spec.get('query', {}), variables)
        headers = template(spec.get('headers', {}), variables)
        if not isinstance(query, dict) or not isinstance(headers, dict):
            raise ApiError('query 和 headers 必须是 JSON 对象')
        headers = {str(k): str(v) for k, v in headers.items()}
        auth = self.profile['auth_type']
        credential = self.profile.get('token', '')
        if not login:
            if auth in ('login_token', 'login_cookie') and not self.logged_in:
                raise ApiError('请先点击“登录 / 获取会话”')
            key = self.profile.get('key_name', '').strip()
            if auth == 'bearer':
                if not credential:
                    raise ApiError('请填写 token')
                headers['Authorization'] = 'Bearer ' + credential
            elif auth in ('api_header', 'api_query', 'login_token'):
                if not key:
                    raise ApiError('请填写鉴权字段名，例如 access-token 或 X-API-Key')
                credential = self.session_token if auth == 'login_token' else credential
                if not credential:
                    raise ApiError('鉴权值不能为空')
                credential = self.profile.get('prefix', '') + credential
                if auth == 'api_query':
                    query[key] = credential
                else:
                    headers[key] = credential
            elif auth == 'basic':
                raw = f"{self.profile.get('username', '')}:{self.profile.get('password', '')}"
                headers['Authorization'] = 'Basic ' + base64.b64encode(raw.encode('utf-8')).decode('ascii')
        if query:
            url += ('&' if '?' in url else '?') + parse.urlencode(query, doseq=True)
        body_type = spec.get('body_type', 'none')
        body = template(spec.get('body', {}), variables)
        if body_type == 'none':
            payload = None
        elif body_type == 'json':
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers.setdefault('Content-Type', 'application/json; charset=utf-8')
        elif body_type == 'form':
            if not isinstance(body, dict):
                raise ApiError('form 请求体必须是 JSON 对象')
            payload = parse.urlencode(body, doseq=True).encode('utf-8')
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
        elif body_type == 'raw':
            if not isinstance(body, str):
                raise ApiError('raw 请求体必须为文本')
            payload = body.encode('utf-8')
        else:
            raise ApiError('body_type 仅支持 none、json、form、raw')
        if payload is not None and len(payload) > MAX_RESPONSE:
            raise ApiError('请求体超过 2 MB')
        if any('\r' in k or '\n' in k or '\r' in v or '\n' in v for k, v in headers.items()):
            raise ApiError('请求头不能包含换行')
        if any(k.lower() in ('host', 'content-length', 'transfer-encoding', 'connection') for k in headers):
            raise ApiError('Host、Content-Length 等传输头由客户端管理，不能手动覆盖')
        return request.Request(url, data=payload, headers=headers, method=method)

    def send(self, spec, parameters=None, login=False):
        req = self.prepare(spec, parameters, login)
        started = time.monotonic()
        try:
            try:
                response = self.opener.open(req, timeout=self.profile['timeout'])
            except error.HTTPError as exc:
                response = exc  # HTTP 4xx/5xx are inspectable responses, never retried.
            with response:
                chunks, size = [], 0
                while True:
                    if time.monotonic()-started > self.profile['timeout']:
                        raise TimeoutError()
                    chunk = response.read1(min(65536, MAX_RESPONSE+1-size)) if hasattr(response,'read1') else response.read(min(65536,MAX_RESPONSE+1-size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise ApiError('响应超过 2 MB，已停止读取；请缩小请求范围')
                data = b''.join(chunks)
                return ApiResponse(response.code, dict(response.headers), data, time.monotonic()-started)
        except (socket.timeout, TimeoutError):
            raise ApiError('请求超时，结果可能尚未返回；修改类接口请先核实服务端状态，不要直接重发') from None
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                text = 'HTTPS 证书验证失败，请核对 CA 证书及服务地址与证书域名是否一致'
            elif isinstance(reason, ssl.SSLError):
                text = 'TLS 握手失败，请检查服务是否支持 HTTPS 及证书配置'
            elif isinstance(reason, socket.gaierror):
                text = '服务域名无法解析，请检查地址拼写、DNS 和现场网络'
            elif isinstance(reason, ConnectionRefusedError):
                text = '目标端口拒绝连接，请检查服务是否启动、端口和防火墙'
            elif isinstance(reason, (socket.timeout, TimeoutError)):
                text = '建立连接超时，请检查服务地址、网络和防火墙'
            else:
                text = '连接失败，请检查服务地址、端口、网络与 HTTPS 证书'
            raise ApiError(text + '；修改类接口请先核实服务端结果') from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, ApiError):
                raise
            raise ApiError('请求未完整返回，请检查网络；修改类接口需先核实服务端结果') from None

    def failure_detail(self, spec, response, stage='接口'):
        request_info=self.request_display(self.prepare(spec, login=stage=='登录')).split('\n\n',1)[0]
        # Details are UI only; logs/support exports never include this body.
        text=self.display(response)
        text=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','***',text)
        return f'{stage}失败：{http_error_reason(response.status)}\n实际请求：{request_info}\n服务端响应（已脱敏，最多 4000 字符）：\n{text[:4000]}'

    def login(self):
        spec = self.profile.get('login', {})
        if not isinstance(spec, dict) or not spec.get('path'):
            raise ApiError('请配置登录请求 JSON（至少包含 path）')
        self.session_token = ''
        self.logged_in = False
        self.cookies.clear()
        response = self.send(spec, login=True)
        if not 200 <= response.status < 300:
            raise ApiError(self.failure_detail(spec,response,'登录'))
        parsed = None
        if spec.get('success_path') or spec.get('token_path'):
            try:
                parsed = json.loads(response.text())
            except ValueError:
                raise ApiError('登录响应不是 JSON，请核对登录地址或返回格式') from None
        if spec.get('success_path') and json_path(parsed, spec['success_path']) != spec.get('success_value'):
            self.cookies.clear()
            raise ApiError('登录业务状态不符合 success_value，请核对账号和登录配置\n'+self.display(response)[:4000])
        if self.profile['auth_type'] == 'login_token':
            header = spec.get('token_header')
            token = next((v for k, v in response.headers.items() if k.lower() == header.lower()), '') if header else ''
            if not token and spec.get('token_path'):
                token = json_path(parsed, spec['token_path'])
            if not isinstance(token, str) or not token:
                raise ApiError('未取得 token，请配置 token_header 或 token_path')
            self.session_token = token
        elif self.profile['auth_type'] == 'login_cookie':
            if not list(self.cookies):
                raise ApiError('登录未返回 Cookie；请核对登录配置及业务状态')
        else:
            raise ApiError('登录按钮仅适用于登录后 token 或 Cookie 会话鉴权')
        self.logged_in = True

    def request_display(self, req, secret_parameters=()):
        secrets = self.secret_values(secret_parameters)
        names = [self.profile.get('key_name', '')]
        parts = parse.urlsplit(req.full_url)
        query = [(k, '***' if SECRET_NAME.search(k) or k in names else redact(v, secrets)) for k,v in parse.parse_qsl(parts.query,keep_blank_values=True)]
        url = parse.urlunsplit((parts.scheme,parts.netloc,parts.path,parse.urlencode(query),''))
        headers = redact(dict(req.header_items()), secrets, names)
        if list(self.cookies):
            headers['Cookie'] = '***（已登录会话）'
        body = req.data.decode('utf-8') if req.data else ''
        content = req.get_header('Content-type','')
        if 'application/x-www-form-urlencoded' in content:
            body = json.dumps(redact(dict(parse.parse_qsl(body,keep_blank_values=True)),secrets,names),ensure_ascii=False,indent=2)
        else:
            try:
                body = json.dumps(redact(json.loads(body),secrets,names),ensure_ascii=False,indent=2)
            except ValueError:
                body = redact(body,secrets)
        return f'{req.method} {url}\n\n{json.dumps(headers,ensure_ascii=False,indent=2)}\n\n{body}'

    def display(self, response, secret_parameters=()):
        secrets = self.secret_values(secret_parameters)
        key = self.profile.get('key_name', '')
        try:
            body = json.dumps(redact(json.loads(response.text()), secrets, [key]), ensure_ascii=False, indent=2)
        except ValueError:
            content = next((v for k, v in response.headers.items() if k.lower() == 'content-type'), '')
            body = redact(response.text(), secrets) if any(t in content.lower() for t in ('text', 'json', 'xml', 'javascript')) else f'[非文本响应：{len(response.body)} 字节，Content-Type={content}]'
        return json.dumps(redact(response.headers, secrets, [key]), ensure_ascii=False, indent=2) + '\n\n' + body
