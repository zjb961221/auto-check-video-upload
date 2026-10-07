from ui_recovery import guarded_poll
from ui_theme import ScrollFrame, size_window
"""Universal API workbench, independent of database connectivity."""
from copy import deepcopy
import hashlib
import json
import os
import queue
from pathlib import Path
from api_drafts import Drafts
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from diagnostics import error_message
from api_client import ApiClient, ApiError, validate_profile, http_error_reason
from api_config import ProfileStore, load_api_requests, bind_api_parameters

AUTH_LABELS = {'无鉴权':'none', 'Bearer Token':'bearer', 'API Key / 自定义 Header':'api_header',
               'API Key / 查询参数':'api_query', 'Basic 用户名密码':'basic',
               '登录后 Token':'login_token', '登录后 Cookie':'login_cookie'}


class ApiWindow(tk.Toplevel):
    def __init__(self, parent, catalogue_path, profile_path, logger):
        super().__init__(parent)
        self.title('通用 API 调用 · HTTP 接口')
        size_window(self, 1160, 850)
        self.transient(parent)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.catalogue_path = catalogue_path
        self.store = ProfileStore(profile_path)
        self.logger = logger
        self.drafts = Drafts(Path(profile_path).with_name("api_drafts.json"))
        self.active_name = None
        self.mine_selections = {}
        self.busy = False
        self.client = None
        self.client_key = None
        self.jobs = queue.Queue()
        self.presets = []
        self.parameters = {}
        self.parameter_widgets = []
        self.controls = []
        self.vars = {}
        viewport = ScrollFrame(self, padding=14)
        viewport.pack(fill='both', expand=True)
        body = viewport.content
        top = ttk.Frame(body)
        top.pack(fill='x', pady=(0, 10))
        self.choice = ttk.Combobox(top, state='readonly')
        self.choice.pack(side='left', fill='x', expand=True)
        self.choice.bind('<<ComboboxSelected>>', self.change_preset)
        reload_button = ttk.Button(top, text='重新加载接口配置', command=self.reload)
        reload_button.pack(side='left', padx=(8, 0))
        self.controls.append(reload_button)
        from api_connections import DEFAULT
        self.mine_choice = ttk.Combobox(body, state='readonly', values=[DEFAULT])
        self.mine_choice.pack(fill='x', pady=(0, 8))
        self.mine_choice.bind('<<ComboboxSelected>>', self.select_mine)
        self.description = ttk.Label(body, wraplength=1000)
        self.description.pack(anchor='w', pady=(0, 8))
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill='both', expand=True)
        profile_tab, request_tab, login_tab, response_tab = [ttk.Frame(self.tabs, padding=12) for _ in range(4)]
        for tab, title in zip((profile_tab, request_tab, login_tab, response_tab), ('服务与鉴权', '请求与参数', '登录请求配置', '响应结果')):
            self.tabs.add(tab, text=title)
        self.response_tab = response_tab
        fields = [('profile_name','连接配置名称'), ('base_url','服务地址（含应用路径）'),
                  ('username','用户名'), ('password','原始密码'), ('token','Token / API Key'),
                  ('key_name','鉴权字段名（例如 access-token）'), ('prefix','鉴权值前缀（可留空）'),
                  ('timeout','超时秒数（1–120）'), ('ca_file','HTTPS CA 证书路径（可选）')]
        for i, (key, label) in enumerate(fields):
            row, col = divmod(i, 2)
            ttk.Label(profile_tab, text=label).grid(row=row*2, column=col, sticky='w')
            var = self.vars[key] = tk.StringVar(value='20' if key == 'timeout' else '')
            entry = (ttk.Combobox(profile_tab, textvariable=var) if key == 'profile_name' else
                     ttk.Entry(profile_tab, textvariable=var, show='*' if key in ('password','token') else ''))
            if key == 'profile_name':
                self.profile_choice = entry
            entry.grid(row=row*2+1, column=col, sticky='ew', padx=(0, 12), pady=(3, 8))
            self.controls.append(entry)
            profile_tab.columnconfigure(col, weight=1)
        ttk.Label(profile_tab, text='鉴权方式').grid(row=8, column=1, sticky='w')
        self.auth = ttk.Combobox(profile_tab, state='readonly', values=list(AUTH_LABELS))
        self.auth.grid(row=9, column=1, sticky='ew', padx=(0,12), pady=(3,8))
        self.auth.current(0)
        options = ttk.Frame(profile_tab)
        options.grid(row=10, column=0, columnspan=2, sticky='ew', pady=8)
        self.remember = tk.BooleanVar(value=os.name == 'nt')
        self.system_proxy = tk.BooleanVar(value=False)
        for text, variable in [('记住密钥（Windows 加密保存）', self.remember), ('使用系统代理', self.system_proxy)]:
            checkbox = ttk.Checkbutton(options, text=text, variable=variable)
            checkbox.pack(side='left', padx=(0,12))
            self.controls.append(checkbox)
        actions = ttk.Frame(profile_tab)
        actions.grid(row=11, column=0, columnspan=2, sticky='w')
        for label, command in [('保存当前连接', self.save), ('恢复已保存连接', self.restore), ('选择 CA 证书', self.choose_ca)]:
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side='left', padx=(0,10))
            self.controls.append(button)
        ttk.Label(profile_tab, text='地址示例：http://服务器:8080/xxl-job-admin；接口 path 接在该地址后。HTTP 连接不加密，HTTPS 默认验证证书。', wraplength=980).grid(row=12, column=0, columnspan=2, sticky='w', pady=12)
        self.form = ttk.Frame(request_tab)
        self.form.pack(fill='x')
        ttk.Label(request_tab, text='请求 JSON：method / path / query / headers / body_type / body，可直接编辑。参数模板使用 {{参数名}}。').pack(anchor='w', pady=(8,6))
        self.request_text = tk.Text(request_tab, wrap='word', height=16, undo=True)
        self.request_text.pack(fill='both', expand=True)
        ttk.Label(login_tab, text='配置登录方法、路径、请求体，以及 token_header / token_path；Cookie 登录建议配置 success_path / success_value。', wraplength=980).pack(anchor='w', pady=(0,8))
        ttk.Label(login_tab, text='可用变量：{{username}}、{{password}}、{{password_md5}}。用户名密码填在“服务与鉴权”，不要写死在 JSON 中。', wraplength=980).pack(anchor='w', pady=(0,8))
        self.login_text = tk.Text(login_tab, wrap='word', undo=True)
        self.login_text.pack(fill='both', expand=True)
        self.output = tk.Text(response_tab, wrap='word', state='disabled')
        self.output.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(response_tab, command=self.output.yview)
        scroll.pack(side='right', fill='y')
        self.output.configure(yscrollcommand=scroll.set)
        toolbar = ttk.Frame(body)
        toolbar.pack(fill='x', pady=(10,6))
        for label, command in [('保存接口草稿（加密）',self.save_draft), ('预览请求',self.preview), ('发送接口',self.send), ('登录 / 获取会话',self.login), ('清除会话',self.clear_session)]:
            button = ttk.Button(toolbar, text=label, command=command)
            button.pack(side='left', padx=(0,8))
            self.controls.append(button)
        self.progress = ttk.Progressbar(body, mode='indeterminate')
        self.progress.pack(fill='x')
        self.status = ttk.Label(body, text='请配置服务地址与接口', wraplength=1000)
        self.status.pack(anchor='w', pady=(6,0))
        if hasattr(parent, "design"):
            parent.design.paint_widgets(self)
        self.refresh_profiles()
        self.reload()
        self.poll_id = self.after(100,self.poll)

    def put_json(self, widget, value):
        widget.delete('1.0','end')
        widget.insert('1.0',json.dumps(value, ensure_ascii=False, indent=2))

    def reload(self):
        if self.busy:
            return
        try:
            presets = load_api_requests(self.catalogue_path)
        except (OSError, ValueError, TypeError):
            messagebox.showerror('接口配置错误','无法读取 api_requests.json，请检查 JSON 格式和参数定义。',parent=self)
            return
        self.capture_draft()
        previous = self.presets[self.choice.current()]["name"] if self.presets and 0 <= self.choice.current() < len(self.presets) else None
        self.active_name = None
        self.presets = presets
        self.choice.configure(values=[p['name'] for p in presets])
        if presets:
            self.choice.current(next((i for i, p in enumerate(presets) if p["name"] == previous), 0))
            self.change_preset()
        else:
            self.status.configure(text='未配置接口，请参考 api_requests.json 示例填写。')

    def fill_profile(self, profile):
        if '_remember_secrets' in profile:
            self.remember.set(bool(profile['_remember_secrets']))
        for name, var in self.vars.items():
            if name != 'profile_name':
                var.set(str(profile.get(name, '20' if name == 'timeout' else '')))
        auth = profile.get('auth_type','none')
        self.auth.set(next((label for label, code in AUTH_LABELS.items() if code == auth), '无鉴权'))
        self.system_proxy.set(bool(profile.get('system_proxy',False)))
        self.put_json(self.login_text,profile.get('login',{}))
        self.client, self.client_key = None, None

    def change_preset(self,event=None):
        if not self.presets:
            return
        self.capture_draft()
        preset = self.presets[self.choice.current()]
        self.vars['profile_name'].set(preset.get('profile_name','默认服务'))
        from api_connections import resolve_connection, DEFAULT
        selected = self.mine_selections.get(preset['name'], preset.get('default_connection', ''))
        if selected not in {c['id'] for c in preset.get('connections', [])}:
            selected = ''
        self.mine_selections[preset['name']] = selected
        self.mine_choice.configure(values=[DEFAULT]+[c['name'] for c in preset.get('connections', [])])
        self.mine_choice.set(next((c['name'] for c in preset.get('connections', []) if c['id']==selected),DEFAULT))
        profile, self.mine_storage_key, draft_key = resolve_connection(preset, selected)
        try:
            stored = self.store.load(self.connection_key())
            if stored:
                profile.update(stored)
        except (OSError,ValueError,TypeError):
            self.status.configure(text='已保存连接无法恢复，请重新填写并保存。')
        self.fill_profile(profile)
        self.refresh_profiles()
        self.description.configure(text=preset.get('description',''))
        self.put_json(self.request_text,preset['request'])
        for widget in self.form.winfo_children():
            widget.destroy()
        self.parameters, self.parameter_widgets = {}, []
        for i, p in enumerate(preset.get('params',[])):
            row,col=divmod(i,2)
            ttk.Label(self.form,text=p['label']).grid(row=row*2,column=col,sticky='w')
            var = tk.StringVar(value=str(p.get('default','')))
            self.parameters[p['name']]=var
            entry=ttk.Entry(self.form,textvariable=var,show='*' if p.get('secret') else '')
            entry.grid(row=row*2+1,column=col,sticky='ew',padx=(0,12),pady=(3,6))
            self.form.columnconfigure(col,weight=1)
            self.parameter_widgets.append(entry)
        self.active_name = draft_key
        default = self.draft_snapshot()
        try:
            draft = self.drafts.open(self.active_name, default)
            for key, value in draft['fields'].items():
                if key in self.vars:
                    self.vars[key].set(value)
            if draft['auth'] in AUTH_LABELS:
                self.auth.set(draft['auth'])
            self.remember.set(draft['remember'])
            self.system_proxy.set(draft['system_proxy'])
            for key, value in draft['parameters'].items():
                if key in self.parameters:
                    self.parameters[key].set(value)
            for widget, key in ((self.request_text, 'request'), (self.login_text, 'login')):
                widget.delete('1.0', 'end')
                widget.insert('1.0', draft[key])
        except (OSError, ValueError, TypeError):
            self.drafts.values[self.active_name] = default
            self.status.configure(text='草稿无法恢复，已加载原始接口。请核对 Windows 账号；重新保存可覆盖该草稿。')

    def draft_snapshot(self):
        return dict(fields={k: v.get() for k, v in self.vars.items()}, auth=self.auth.get(),
                    remember=self.remember.get(), system_proxy=self.system_proxy.get(),
                    parameters={k: v.get() for k, v in self.parameters.items()},
                    request=self.request_text.get('1.0', 'end-1c'), login=self.login_text.get('1.0', 'end-1c'))

    def capture_draft(self):
        if self.active_name is not None:
            self.drafts.capture(self.active_name, self.draft_snapshot())

    def save_draft(self):
        if self.busy or self.active_name is None:
            return
        try:
            self.drafts.save(self.active_name, self.draft_snapshot())
            self.status.configure(text='接口草稿已加密保存，下次自动恢复。草稿包括参数和鉴权信息，仅当前 Windows 账号可解密。')
        except (OSError, ValueError, TypeError):
            messagebox.showerror('草稿未保存', '无法加密保存草稿，请检查 Windows 环境及文件权限；当前编辑内容仍在窗口中。', parent=self)

    def refresh_profiles(self):
        try:
            current = self.presets[self.choice.current()] if self.presets and self.choice.current()>=0 else {}
            selected = bool(self.mine_selections.get(current.get('name')))
            values = [current.get('profile_name','默认服务')] if selected else sorted(k for k in self.store.read() if not k.startswith(('mine-api:','mine-draft:')))
            self.profile_choice.configure(values=values, state='disabled' if self.busy else 'readonly' if selected else 'normal')
        except (OSError, ValueError, TypeError):
            self.status.configure(text='已保存连接列表无法读取，请检查配置文件。')

    def profile(self):
        result = {name:var.get() for name,var in self.vars.items() if name != 'profile_name'}
        result.update(auth_type=AUTH_LABELS[self.auth.get()], system_proxy=self.system_proxy.get())
        try:
            result['login']=json.loads(self.login_text.get('1.0','end'))
        except ValueError:
            raise ApiError('登录请求配置不是合法 JSON') from None
        return validate_profile(result)

    def client_for(self,profile):
        # Only a hash is used for equality; credentials never appear in logs.
        key=hashlib.sha256(json.dumps(profile,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
        if self.client_key != key:
            self.client=ApiClient(profile)
            self.client_key=key
        return self.client

    def connection_key(self):
        preset = self.presets[self.choice.current()] if self.presets else {}
        return self.mine_storage_key if self.mine_selections.get(preset.get('name')) else self.vars['profile_name'].get()

    def select_mine(self, event=None):
        if self.busy or not self.presets:
            return
        preset=self.presets[self.choice.current()]
        self.mine_selections[preset['name']]=next((c['id'] for c in preset.get('connections',[]) if c['name']==self.mine_choice.get()),'')
        self.change_preset()
        self.client, self.client_key = None, None
        self.output.configure(state='normal')
        self.output.delete('1.0','end')
        self.output.configure(state='disabled')
        self.status.configure(text='煤矿连接已切换，旧登录会话已清除；请核对目标并重新登录。')

    def save(self):
        try:
            self.store.save(self.connection_key(),self.profile(),self.remember.get())
            self.refresh_profiles()
            self.status.configure(text='当前服务连接已保存；请求修改可通过“保存接口草稿（加密）”保存。')
        except (OSError,ValueError,TypeError):
            messagebox.showerror('保存失败','无法保存 API 连接，请检查配置、文件权限及 Windows 加密环境。',parent=self)

    def restore(self):
        try:
            profile=self.store.load(self.connection_key())
            if profile is None:
                raise ApiError('没有这个名称的已保存连接')
            if not messagebox.askyesno('恢复连接', '将替换当前服务与鉴权设置，是否继续？', parent=self):
                return
            self.fill_profile(profile)
            self.status.configure(text='已恢复服务连接；登录会话需重新获取。')
        except (OSError,ValueError,TypeError):
            messagebox.showerror('恢复失败','找不到配置或无法解密，请核对配置名称和 Windows 账号。',parent=self)

    def choose_ca(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('CA证书','*.pem *.crt *.cer'),('所有文件','*.*')])
        if path:
            self.vars['ca_file'].set(path)

    def request_snapshot(self):
        if not self.presets:
            raise ApiError('请先配置接口')
        try:
            spec=json.loads(self.request_text.get('1.0','end'))
        except ValueError:
            raise ApiError('请求 JSON 格式错误') from None
        if not isinstance(spec,dict):
            raise ApiError('请求 JSON 必须为对象')
        preset=self.presets[self.choice.current()]
        parameters=bind_api_parameters(preset,{k:v.get() for k,v in self.parameters.items()})
        secrets=[parameters[p['name']] for p in preset.get('params',[]) if p.get('secret')]
        return spec,parameters,secrets

    def show(self,text):
        self.output.configure(state='normal')
        self.output.delete('1.0','end')
        self.output.insert('1.0',text)
        self.output.configure(state='disabled')
        self.tabs.select(self.response_tab)

    def preview(self):
        try:
            client=self.client_for(self.profile())
            spec,params,secrets=self.request_snapshot()
            req=client.prepare(spec,params)
            self.show(client.request_display(req,secrets))
            self.status.configure(text='这是请求预览，尚未发送。密钥已隐藏。')
        except (OSError,ValueError,TypeError,KeyError) as exc:
            messagebox.showerror('无法预览',str(exc) if isinstance(exc,ApiError) else '配置格式无效，请检查请求和鉴权字段。',parent=self)

    def set_busy(self,busy):
        self.busy=busy
        for widget in self.controls+self.parameter_widgets:
            widget.configure(state='disabled' if busy else 'normal')
        self.choice.configure(state='disabled' if busy else 'readonly')
        self.auth.configure(state='disabled' if busy else 'readonly')
        self.mine_choice.configure(state='disabled' if busy else 'readonly')
        self.refresh_profiles()
        for widget in (self.request_text,self.login_text):
            widget.configure(state='disabled' if busy else 'normal')
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def run(self,action,client,function,secrets=()):
        name=self.connection_key()
        remember=self.remember.get()
        self.set_busy(True)
        self.status.configure(text='正在登录…' if action=='login' else '正在请求，请勿重复发送…')
        def worker():
            try:
                data=function()
                text='登录成功，会话已保存在本次运行中。Token/Cookie 不在结果窗口显示。' if action=='login' else client.display(data,secrets)
                self.jobs.put(('ok',action,text,None if action=='login' else data,name,remember,client.profile))
            except Exception as exc:
                self.logger.error('event=api_failed type=%s',type(exc).__name__)
                message=str(exc) if isinstance(exc,ApiError) else '请求失败，请检查接口配置及网络。修改类接口请先核实服务端状态。'
                self.jobs.put(('error',action,message,None,name,remember,client.profile))
        threading.Thread(target=worker,daemon=True).start()

    def send(self):
        if self.busy:
            return
        try:
            client=self.client_for(self.profile())
            spec,params,secrets=self.request_snapshot()
            req=client.prepare(spec,params)
            if req.method not in ('GET','HEAD','OPTIONS') or spec.get('confirm',False):
                if not messagebox.askyesno('确认发送接口',f"目标：{client.profile['base_url']}\n方法：{req.method}\n\n接口可能修改数据或触发任务。确认发送？",parent=self):
                    return
            self.run('request',client,lambda:client.send(deepcopy(spec),deepcopy(params)),secrets)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            messagebox.showerror('无法发送',str(exc) if isinstance(exc,ApiError) else '配置格式无效，请检查请求和鉴权字段。',parent=self)

    def login(self):
        if self.busy:
            return
        try:
            client=self.client_for(self.profile())
            if client.profile['auth_type'] not in ('login_token','login_cookie'):
                raise ApiError('请先选择“登录后 Token”或“登录后 Cookie”鉴权')
            self.run('login',client,client.login)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            messagebox.showerror('无法登录',str(exc) if isinstance(exc,ApiError) else '登录配置无效。',parent=self)

    def clear_session(self):
        self.client,self.client_key=None,None
        self.status.configure(text='已清除本次登录会话，下次调用需要重新登录。')

    def poll(self):
        guarded_poll(self, self.consume_result, self.recover_result)

    def recover_result(self, exc):
        self.set_busy(False)
        self.status.configure(text='响应显示失败；修改接口可能已执行，请先核实服务端，勿直接重发。')
        messagebox.showerror('响应显示失败', error_message(exc, self.logger), parent=self)

    def consume_result(self):
        try:
            state,action,text,response,name,remember,profile=self.jobs.get_nowait()
        except queue.Empty:
            return
        self.set_busy(False)
        self.show(text)
        if state=='error':
            self.status.configure(text='请求未完整完成；修改类接口请先核实服务端结果。')
        else:
            self.status.configure(text='登录成功' if action=='login' else f'HTTP {response.status} · {response.elapsed:.2f} 秒 · 请检查响应中的业务状态' if 200 <= response.status < 300 else http_error_reason(response.status))
            self.logger.info('event=api_response status=%s',response.status if response else 'login')
            if action=='login' or 200<=response.status<300:
                try:
                    self.store.save(name,profile,remember)
                    self.refresh_profiles()
                except (OSError,ValueError,TypeError):
                    self.status.configure(text=self.status.cget('text')+' · 连接信息未能保存')

    def close(self):
        if self.busy:
            messagebox.showinfo('请求进行中','请等待请求返回；修改类接口中断后可能无法确认结果。',parent=self)
            return
        self.capture_draft()
        if self.drafts.dirty() and not messagebox.askyesno('存在未保存的接口修改', '有接口修改尚未保存为草稿。关闭将丢弃这些修改，是否关闭？\n如需保留，请取消后切换到修改过的接口并保存草稿。', parent=self):
            return
        self.clear_session()
        self.grab_release()
        self.destroy()


    def destroy(self):
        # Cancel callbacks before Tk deletes their Tcl commands.
        for name in ('poll_id', 'scroll_id'):
            callback = getattr(self, name, None)
            if callback is not None:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        super().destroy()
