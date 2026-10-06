"""Customer-facing, sequential workflow panel; all I/O runs off the Tk thread."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from workflows import load_workflows, WorkflowRun, WorkflowError, api_outcome
from queries import bind_parameters
from database import run_query
from updates import preview_update, apply_update, bind_update_parameters, display_update_value, UpdateError, CommitUncertain
from api_client import ApiClient, ApiError
from api_config import ProfileStore, bind_api_parameters
from core import export_csv
from diagnostics import error_message

STATE_LABELS = {'pending': '待办理', 'ready': '待确认', 'done': '已完成',
                'skipped': '已跳过', 'failed': '失败', 'uncertain': '结果待核实', 'stale': '需重新办理'}
TYPE_LABELS = {'note': '说明与准备', 'query': '数据库查询', 'update': '数据库修改', 'api': 'API 调用'}


class WorkflowPanel(ttk.Frame):
    def __init__(self, parent, app, root, settings_path):
        super().__init__(parent, padding=12)
        self.app, self.root, self.settings_path = app, root, settings_path
        self.store = ProfileStore(settings_path.parent / 'api_profiles.json')
        self.busy = False
        self.loading = False
        self.run = None
        self.flows = []
        self.drafts, self.results, self.clients, self.profile_cache = {}, {}, {}, {}
        self.preview = None
        self.jobs = queue.Queue()
        self.controls = []
        self.parameters, self.modes = {}, {}
        title = ttk.Frame(self)
        title.pack(fill='x')
        self.flow_title = ttk.Label(title, text='客户操作向导', style='Heading.TLabel')
        self.flow_title.pack(side='left')
        self.selector = ttk.Combobox(title, state='readonly', width=24)
        self.selector.pack(side='left', padx=16, fill='x', expand=True)
        self.selector.bind('<<ComboboxSelected>>', self.select_flow)
        self.reload_button = ttk.Button(title, text='重新加载流程', command=self.reload)
        self.reload_button.pack(side='left')
        self.description = ttk.Label(self, wraplength=1060, style='Muted.TLabel')
        self.description.pack(anchor='w', pady=(6, 10))
        self.step_picker = ttk.Combobox(self, state='readonly')
        self.step_picker.bind('<<ComboboxSelected>>', lambda e: self.move(self.step_picker.current()) if not self.busy else None)
        self.overall = ttk.Progressbar(self, mode='determinate', maximum=100)
        self.overall.pack(fill='x', pady=(0, 10))
        main = self.main = ttk.Frame(self)
        main.pack(fill='both', expand=True)
        left = self.sidebar = ttk.Frame(main, width=270)
        left.pack_propagate(False)
        left.pack(side='left', fill='y', padx=(0, 12))
        self.steps = tk.Listbox(left, width=24, exportselection=False, activestyle='none')
        self.steps.pack(side='left', fill='both', expand=True, pady=10)
        step_scroll = ttk.Scrollbar(left, command=self.steps.yview)
        step_scroll.pack(side='right', fill='y')
        self.steps.configure(yscrollcommand=step_scroll.set)
        self.steps.bind('<<ListboxSelect>>', self.select_step)
        right = self.right = ttk.Frame(main)
        right.pack(side='left', fill='both', expand=True)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self.heading = ttk.Label(right, style='Heading.TLabel', wraplength=750)
        self.heading.grid(row=0,column=0,sticky='ew',pady=(0,6))
        scroller = ttk.Frame(right)
        scroller.grid(row=1,column=0,sticky='nsew')
        self.canvas = tk.Canvas(scroller, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(scroller, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(self.canvas, padding=(0, 0, 8, 8))
        self.body_id = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', self.body_layout)
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.body_id, width=e.width))
        self.status = ttk.Label(right, wraplength=780, text='请加载流程配置')
        self.status.grid(row=2,column=0,sticky='ew',pady=8)
        self.progress = ttk.Progressbar(right, mode='indeterminate')
        self.progress.grid(row=3,column=0,sticky='ew')
        self.progress.grid_remove()
        nav = ttk.Frame(right)
        nav.grid(row=4,column=0,sticky='ew',pady=(10,0))
        self.previous = ttk.Button(nav, text='上一步', command=lambda: self.navigate(-1))
        self.previous.pack(side='left')
        self.skip_button = ttk.Button(nav, text='跳过可选步骤', command=self.skip)
        self.skip_button.pack(side='left', padx=10)
        self.next_button = ttk.Button(nav, style='Primary.TButton', text='确认完成，下一步', command=self.next)
        self.next_button.pack(side='right')
        self.footnote = ttk.Label(self, text='进度仅保留在本次运行中 · 已执行的修改不会因返回而撤销 · F11 全屏 / Esc 退出', style='Muted.TLabel', wraplength=1080)
        self.footnote.pack(anchor='w', pady=(10, 0))
        self.bind('<Configure>', self.responsive)
        self.app.bind('<<DesignChanged>>', self.responsive, add='+')
        self.app.design.decorate(self.previous, 'back')
        self.app.design.decorate(self.next_button, 'next')
        self.app.design.decorate(self.reload_button, 'flow')
        for var in app.vars.values():
            var.trace_add('write', self.connection_changed)
        self.reload(initial=True)
        self.poll_id = self.after(100, self.poll)

    def responsive(self, event=None):
        if event is not None and event.widget not in (self, self.app):
            return
        width = max(self.winfo_width(), 1)
        compact = width < 1080 * self.app.design.zoom / 100
        if compact:
            self.sidebar.pack_forget()
            self.flow_title.pack_forget()
            if not self.step_picker.winfo_manager():
                self.step_picker.pack(fill='x', before=self.overall, pady=(0,8))
        else:
            self.step_picker.pack_forget()
            if not self.sidebar.winfo_manager():
                self.sidebar.pack(side='left',fill='y',padx=(0,12),before=self.right)
            if not self.flow_title.winfo_manager():
                self.flow_title.pack(side='left',before=self.selector)
        self.description.configure(wraplength=max(240,width-24))
        self.footnote.configure(wraplength=max(240,width-24))
        self.heading.configure(wraplength=max(240,self.right.winfo_width()-24))
        self.status.configure(wraplength=max(240,self.right.winfo_width()-24))
        if self.run:
            self.refresh_nav()
        self.body_layout()

    def reflow_fields(self, parent):
        columns = 1 if parent.winfo_width() < 650*self.app.design.zoom/100 else 2
        for i, (caption, entry) in enumerate(parent._fields):
            row, col = divmod(i, columns)
            caption.grid_configure(row=row*2, column=col)
            entry.grid_configure(row=row*2+1, column=col)
        parent.columnconfigure(0,weight=1)
        parent.columnconfigure(1,weight=1 if columns==2 else 0)

    def body_layout(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        width = max(240,self.canvas.winfo_width()-40)
        for widget in self.body.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=width)
            if hasattr(widget, '_fields'):
                self.reflow_fields(widget)

    def warn(self, text):
        messagebox.showerror('流程操作', text, parent=self)

    def may_reset(self):
        return self.run is None or not (self.drafts or self.results or any(s != 'pending' for s in self.run.states)) or messagebox.askyesno(
            '重新开始流程', '这会清空本次流程进度和输入。已经执行的修改不会撤销，重新执行可能重复操作。确认已核实当前状态并重新开始？', parent=self)

    def reload(self, initial=False):
        if self.busy:
            return
        try:
            flows = load_workflows(self.root / 'workflows.json')
        except (OSError, ValueError, TypeError) as exc:
            self.status.configure(text='流程配置加载失败；已有流程保持不变。请检查 workflows.json 及其引用。')
            if not initial:
                self.warn(str(exc) if isinstance(exc, WorkflowError) else '无法读取流程或操作配置，请检查 JSON 文件和引用名称。')
            self.refresh_nav()
            return
        if not initial and not self.may_reset():
            return
        self.flows = flows
        self.selector.configure(values=[f['name'] for f in flows])
        self.selector.current(0)
        self.start_flow(0)

    def select_flow(self, event=None):
        if self.busy:
            return
        index = self.selector.current()
        if not self.may_reset():
            self.selector.set(self.run.flow['name'])
            return
        self.start_flow(index)

    def start_flow(self, index):
        self.run = WorkflowRun(self.flows[index])
        self.drafts, self.results = {}, {}
        self.preview = None
        self.description.configure(text=self.run.flow.get('description', ''))
        self.render()

    def snapshot(self):
        if not self.run:
            return
        self.drafts[self.run.index] = dict(values={k: v.get() for k, v in self.parameters.items()},
                                          modes={k: v.get() for k, v in self.modes.items()},
                                          checks=[v.get() for v in getattr(self, 'checks', [])])
        if self.run.step['type'] == 'api':
            self.profile_cache[self.profile_name] = self.current_profile()

    def changed(self, *args):
        if self.loading or not self.run:
            return
        self.preview = None
        self.run.invalidate()
        self.status.configure(text='输入已变化，原结果仅供参考；请重新执行本步。后续已办步骤需重新核对。')
        self.refresh_nav()

    def connection_changed(self, *args):
        # A new target invalidates prior DB evidence and dependent confirmations.
        if self.loading or not self.run:
            return
        affected = [i for i, step in enumerate(self.run.flow['steps'])
                    if step['type'] in ('query', 'update') and
                    (self.run.states[i] != 'pending' or i == self.run.index)]
        if affected:
            for i in range(min(affected), len(self.run.states)):
                if self.run.states[i] not in ('pending', 'uncertain'):
                    self.run.states[i] = 'stale'
            if self.run.step['type'] in ('query', 'update'):
                self.changed()
            self.status.configure(text='数据库连接已变化，相关步骤结果已失效；请返回最早的未完成步骤重新核对。')
            self.refresh_nav()

    def field(self, parent, row, key, label, variable, secret=False):
        group, col = divmod(row, 2)
        caption = ttk.Label(parent, text=label)
        caption.grid(row=group*2, column=col, sticky='w')
        entry = ttk.Entry(parent, textvariable=variable, show='*' if secret else '')
        entry.grid(row=group*2+1, column=col, sticky='ew', padx=(0, 10), pady=(2, 6))
        parent.columnconfigure(col, weight=1)
        self.controls.append((entry, 'normal'))
        if not hasattr(parent, '_fields'):
            parent._fields = []
            parent.bind('<Configure>', lambda e: self.reflow_fields(parent))
        parent._fields.append((caption, entry))
        return entry

    def button(self, parent, label, command):
        widget = ttk.Button(parent, text=label, command=command)
        widget.pack(side='left', padx=(0, 8), pady=5)
        self.controls.append((widget, 'normal'))
        return widget

    def render(self):
        self.loading = True
        self.preview = None
        for child in self.body.winfo_children():
            child.destroy()
        self.controls, self.parameters, self.modes, self.checks = [], {}, {}, []
        self.apply_button = None
        self.action_button = None
        self.reconcile_button = None
        step = self.run.step
        i = self.run.index
        self.heading.configure(text=f'第 {i+1} / {len(self.run.states)} 步 · {step["title"]}')
        ttk.Label(self.body, text=TYPE_LABELS[step['type']] + (' · 可选' if step.get('optional') else ' · 必做')).pack(anchor='w')
        instructions = tk.Text(self.body, height=4, wrap='word', relief='flat')
        instructions.insert('1.0', step.get('instructions', '请核对本步骤信息后办理。'))
        instructions.configure(state='disabled')
        instructions.pack(fill='x', pady=8)
        saved = self.drafts.get(i, {})
        if step['type'] == 'note':
            for n, label in enumerate(step.get('checklist') or ['我已阅读说明并完成本步骤准备']):
                var = tk.BooleanVar(value=(saved.get('checks', [])[n] if n < len(saved.get('checks', [])) else False))
                check = ttk.Checkbutton(self.body, text=label, variable=var, command=self.note_changed)
                check.pack(anchor='w', pady=4)
                self.controls.append((check, 'normal'))
                self.checks.append(var)
        else:
            operation = step['operation']
            ttk.Label(self.body, text='操作：' + operation['name'], wraplength=740).pack(anchor='w', pady=(0, 8))
            if step['type'] in ('query', 'update'):
                self.db_form()
            else:
                self.api_form(operation)
            form = ttk.LabelFrame(self.body, text='本步骤参数', padding=8)
            form.pack(fill='x', pady=8)
            condition_params = {p for _, _, p in operation['compiled']['conditions']} if step['type'] == 'update' else set()
            for n, p in enumerate(operation.get('params', [])):
                value = saved.get('values', {}).get(p['name'], step.get('defaults', {}).get(p['name'], str(p.get('default', ''))))
                var = self.parameters[p['name']] = tk.StringVar(value=value)
                var.trace_add('write', self.changed)
                self.field(form, n, p['name'], p['label'], var, p.get('secret', False))
            if not self.parameters:
                ttk.Label(form, text='本步骤无需填写业务参数。').pack(anchor='w')
            if step['type'] == 'update':
                modes = ttk.Frame(self.body)
                modes.pack(fill='x')
                for p in operation['params']:
                    if p['name'] not in condition_params:
                        row = ttk.Frame(modes)
                        row.pack(fill='x')
                        ttk.Label(row, text=p['label'] + '赋值方式：').pack(side='left')
                        mode = self.modes[p['name']] = tk.StringVar(value=saved.get('modes', {}).get(p['name'], '输入值'))
                        choices = ['输入值', '空字符串', '数据库 NULL'] if p['type'] == 'text' else ['输入值', '数据库 NULL']
                        widget = ttk.Combobox(row, textvariable=mode, values=choices, state='readonly', width=15)
                        widget.pack(side='left')
                        self.controls.append((widget, 'readonly'))
                        mode.trace_add('write', self.changed)
                ttk.Label(self.body, text='选择空字符串或数据库 NULL 时，对应输入框内容不参与赋值。', wraplength=740).pack(anchor='w')
            actions = ttk.Frame(self.body)
            actions.pack(fill='x')
            label = {'query': '执行本步查询', 'update': '1. 预览本步修改', 'api': '发送本步接口'}[step['type']]
            self.action_button = self.button(actions, label, self.execute)
            self.action_button.configure(style='Primary.TButton')
            self.app.design.decorate(self.action_button, 'api' if step['type']=='api' else 'database')
            if step['type'] == 'update':
                self.apply_button = self.button(actions, '2. 确认并提交', self.submit)
            reconcile_row = ttk.Frame(self.body)
            reconcile_row.pack(fill='x')
            self.reconcile_button = self.button(reconcile_row, '已核实服务端，允许重新操作', self.reconcile)
            self.reconcile_button.configure(style='Danger.TButton')
            result_box = ttk.Frame(self.body)
            result_box.pack(fill='both', expand=True)
            result_box.columnconfigure(0, weight=1)
            result_box.rowconfigure(0, weight=1)
            self.output = tk.Text(result_box, wrap='none', height=12, width=50, state='disabled')
            self.output.grid(row=0, column=0, sticky='nsew')
            y = ttk.Scrollbar(result_box, command=self.output.yview)
            y.grid(row=0, column=1, sticky='ns')
            x = ttk.Scrollbar(result_box, orient='horizontal', command=self.output.xview)
            x.grid(row=1, column=0, sticky='ew')
            self.output.configure(xscrollcommand=x.set, yscrollcommand=y.set)
            if i in self.results:
                self.show_result(self.results[i]['text'])
            self.export_button = self.button(self.body, '导出本步查询 CSV', self.export)
            if step['type'] != 'query' or 'rows' not in self.results.get(i, {}):
                self.export_button.configure(state='disabled')
        self.loading = False
        self.status.configure(text='当前状态：' + STATE_LABELS[self.run.states[i]] + '。操作完成后，核对结果再点击下一步。')
        self.canvas.yview_moveto(0)
        self.refresh_nav()
        self.app.design.paint_widgets(self.body)
        self.responsive()

    def db_form(self):
        box = ttk.LabelFrame(self.body, text='数据库连接（与高级工具共享，可保存）', padding=8)
        box.pack(fill='x')
        for n, (key, label) in enumerate([('host', '地址'), ('port', '端口'), ('database', '数据库'), ('user', '用户名'), ('password', '密码'), ('ssl_ca', 'CA 证书路径（可选）')]):
            self.field(box, n, key, label, self.app.vars[key], key == 'password')
        row = ttk.Frame(self.body)
        row.pack(fill='x')
        check = ttk.Checkbutton(row, text='记住密码（Windows 加密保存）', variable=self.app.remember_password)
        check.pack(side='left')
        self.controls.append((check, 'normal'))
        self.button(row, '保存数据库连接', self.app.save)

    def api_form(self, operation):
        self.profile_name = operation.get('profile_name', '默认服务')
        profile = deepcopy(operation.get('profile', {}))
        try:
            profile.update(self.store.load(self.profile_name) or {})
        except (OSError, ValueError, TypeError):
            ttk.Label(self.body, text='已保存的 API 连接无法恢复，请重新填写。').pack(anchor='w')
        profile.update(self.profile_cache.get(self.profile_name, {}))
        self.api_profile = profile
        self.api_vars = {}
        box = ttk.LabelFrame(self.body, text=f'接口服务：{self.profile_name}（鉴权方式由配置指定）', padding=8)
        box.pack(fill='x')
        for n, (key, label) in enumerate([('base_url', '服务地址（含应用路径）'), ('username', '用户名'), ('password', '原始密码'), ('token', 'Token / API Key')]):
            var = self.api_vars[key] = tk.StringVar(value=str(profile.get(key, '')))
            var.trace_add('write', self.changed)
            self.field(box, n, key, label, var, key in ('password', 'token'))
        self.api_remember = tk.BooleanVar(value=profile.get('_remember_secrets', os.name == 'nt'))
        row = ttk.Frame(self.body)
        row.pack(fill='x')
        check = ttk.Checkbutton(row, text='记住密钥（Windows 加密保存）', variable=self.api_remember)
        check.pack(side='left')
        self.controls.append((check, 'normal'))
        self.button(row, '保存 API 连接', self.save_api)
        if profile.get('auth_type') in ('login_token', 'login_cookie'):
            self.button(row, '登录 / 获取会话', self.login)

    def current_profile(self):
        return dict(self.api_profile, **{k: v.get() for k, v in self.api_vars.items()})

    def api_client(self):
        profile = self.current_profile()
        key = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        if key not in self.clients:
            self.clients[key] = ApiClient(profile)
        return self.clients[key]

    def save_api(self):
        try:
            client = self.api_client()
            self.store.save(self.profile_name, client.profile, self.api_remember.get())
            self.status.configure(text='API 连接已保存。')
        except (OSError, ValueError, TypeError):
            self.warn('API 连接未能保存，请检查服务地址、Windows 加密环境和文件权限。')

    def note_changed(self):
        self.changed()
        if all(v.get() for v in self.checks):
            self.run.ready()
        self.refresh_nav()

    def refresh_nav(self):
        if not self.run:
            for widget in (self.previous, self.next_button, self.skip_button):
                widget.configure(state='disabled')
            return
        self.steps.delete(0, 'end')
        for n, (step, state) in enumerate(zip(self.run.flow['steps'], self.run.states)):
            self.steps.insert('end', f'{n+1}. {step["title"]} [{STATE_LABELS[state]}]')
        self.step_picker.configure(values=[f'{i+1}. {step["title"]} · {STATE_LABELS[state]}' for i, (step, state) in enumerate(zip(self.run.flow['steps'], self.run.states))])
        self.step_picker.current(self.run.index)
        self.step_picker.configure(state='disabled' if self.busy else 'readonly')
        self.overall.configure(value=100*sum(s in ('done','skipped') for s in self.run.states)/len(self.run.states))
        palette = self.app.design.colors
        for i, state in enumerate(self.run.states):
            self.steps.itemconfigure(i, foreground=palette['success'] if state=='done' else palette['danger'] if state in ('failed','uncertain') else palette['muted'] if state in ('pending','skipped') else palette['text'])
        self.steps.selection_set(self.run.index)
        self.steps.see(self.run.index)
        self.previous.configure(state='normal' if not self.busy and self.run.index else 'disabled')
        self.next_button.configure(text='确认完成流程' if self.run.index == len(self.run.states)-1 else '确认完成，下一步',
                                   state='normal' if not self.busy and all(s in ('done', 'skipped') for s in self.run.states[:self.run.index]) and self.run.states[self.run.index] in ('ready', 'done', 'skipped') else 'disabled')
        self.skip_button.configure(state='normal' if not self.busy and self.run.step.get('optional') and self.run.states[self.run.index] != 'uncertain' else 'disabled')
        if self.apply_button:
            self.apply_button.configure(state='normal' if not self.busy and self.preview is not None and self.preview.rows else 'disabled')
        if self.action_button:
            self.action_button.configure(state='disabled' if self.busy or self.run.states[self.run.index] == 'uncertain' else 'normal')
        if self.reconcile_button:
            self.reconcile_button.configure(state='normal' if not self.busy and self.run.states[self.run.index] == 'uncertain' else 'disabled')

    def select_step(self, event=None):
        selected = self.steps.curselection()
        if self.busy or not selected or selected[0] == self.run.index:
            return
        self.move(selected[0])

    def move(self, target):
        self.snapshot()
        try:
            self.run.go(target)
        except WorkflowError as exc:
            self.warn(str(exc))
            self.refresh_nav()
            return
        self.render()

    def navigate(self, delta):
        if not self.busy:
            self.move(self.run.index + delta)

    def next(self):
        if self.busy or not self.run:
            return
        try:
            self.run.complete()
        except WorkflowError as exc:
            self.warn(str(exc))
            return
        if self.run.index < len(self.run.states)-1:
            self.move(self.run.index + 1)
        else:
            self.refresh_nav()
            self.status.configure(text='本流程已完成。已跳过的可选步骤不代表已执行。')
            self.show_summary()

    def show_summary(self):
        text = '\n'.join(f'{i+1}. {step["title"]}：{STATE_LABELS[state]}' for i, (step, state) in enumerate(zip(self.run.flow['steps'], self.run.states)))
        messagebox.showinfo('流程办理结果', text, parent=self)

    def skip(self):
        if self.busy:
            return
        try:
            if messagebox.askyesno('跳过可选步骤', '确认不执行本步骤并继续？已经执行的操作不会撤销。', parent=self):
                self.run.skip()
                self.next()
        except WorkflowError as exc:
            self.warn(str(exc))

    def set_busy(self, busy):
        self.busy = busy
        for widget, state in self.controls:
            widget.configure(state='disabled' if busy else state)
        self.selector.configure(state='disabled' if busy else 'readonly')
        self.reload_button.configure(state='disabled' if busy else 'normal')
        self.app.navigation.tab(self.app.advanced_tab, state='disabled' if busy else 'normal')
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()
            if hasattr(self, 'export_button') and self.export_button.winfo_exists():
                self.export_button.configure(state='normal' if self.run.step['type'] == 'query' and 'rows' in self.results.get(self.run.index, {}) else 'disabled')
        self.refresh_nav()

    def launch(self, action, function, write=False):
        self.snapshot()
        if self.run.step['type'] in ('query', 'update'):
            self.target_label = self.app.vars['host'].get() + ' / ' + self.app.vars['database'].get()
        else:
            self.target_label = self.current_profile().get('base_url', '')
        self.set_busy(True)
        self.status.configure(text='正在执行，请等待返回；不会自动重试或自动进入下一步。')
        def worker():
            try:
                self.jobs.put((action, 'ok', function()))
            except CommitUncertain as exc:
                self.jobs.put((action, 'uncertain', str(exc)))
            except (ApiError, UpdateError) as exc:
                self.jobs.put((action, 'uncertain' if write and action == 'api' else 'failed', str(exc)))
            except Exception as exc:
                self.jobs.put((action, 'uncertain' if write and action == 'api' else 'failed', error_message(exc, self.app.logger)))
        threading.Thread(target=worker, daemon=True).start()

    def repeat_allowed(self):
        return self.run.states[self.run.index] not in ('ready', 'done', 'stale') or messagebox.askyesno(
            '再次执行', '本步骤已有执行记录。再次执行可能重复修改数据或触发任务。确认已核实并再次执行？', parent=self)

    def execute(self):
        if self.busy or self.run.states[self.run.index] == 'uncertain':
            return
        if any(s not in ('done', 'skipped') for s in self.run.states[:self.run.index]):
            self.warn('请先返回并完成前面失效的步骤。')
            return
        if not self.repeat_allowed():
            return
        try:
            step = deepcopy(self.run.step)
            operation = step['operation']
            values = {k: v.get() for k, v in self.parameters.items()}
            kind = step['type']
            if kind in ('query', 'update'):
                config = self.app.config()
                if kind == 'query':
                    params = bind_parameters(operation, values)
                    fn = lambda: run_query(config, operation['sql'], params)
                else:
                    mapping = {'输入值': 'value', '空字符串': 'empty', '数据库 NULL': 'null'}
                    modes = {k: mapping[v.get()] for k, v in self.modes.items()}
                    bind_update_parameters(operation, values, modes)
                    fn = lambda: preview_update(config, operation, values, modes=modes)
                self.run.invalidate()
                self.preview = None
                self.launch(kind, fn)
            else:
                client = self.api_client()
                params = bind_api_parameters(operation, values)
                spec = deepcopy(operation['request'])
                req = client.prepare(spec, params)
                write = req.method not in ('GET', 'HEAD', 'OPTIONS') or spec.get('confirm', False)
                if write and not messagebox.askyesno('确认发送接口', f'目标：{client.profile["base_url"]}\n步骤：{step["title"]}\n方法：{req.method}\n可能修改数据或触发任务。确认发送？', parent=self):
                    return
                secrets = [params[p['name']] for p in operation.get('params', []) if p.get('secret')]
                def request():
                    response = client.send(spec, params)
                    ok, status = api_outcome(response, step.get('success'))
                    return ok, status, client.display(response, secrets), write
                self.run.invalidate()
                self.launch('api', request, write)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.warn(str(exc) if isinstance(exc, (ApiError, UpdateError, WorkflowError, ValueError)) else '配置或输入格式错误，请联系配置人员。')

    def login(self):
        if self.busy:
            return
        try:
            client = self.api_client()
            self.launch('login', client.login)
        except (OSError, ValueError, TypeError):
            self.warn('登录配置无效，请检查服务地址和鉴权配置。')

    def submit(self):
        if self.busy or self.preview is None or not self.preview.rows:
            return
        snapshot = self.preview
        config = snapshot.config
        if not messagebox.askyesno('确认修改数据库', f'目标：{config["host"]} / {config["database"]}\n步骤：{self.run.step["title"]}\n匹配 {len(snapshot.rows)} 行。确认按预览提交？', parent=self):
            return
        self.preview = None
        self.launch('apply', lambda: apply_update(snapshot), write=True)

    def reconcile(self):
        if not self.busy and self.run.states[self.run.index] == 'uncertain' and messagebox.askyesno(
                '确认已核实服务端', '请先通过查询或服务端日志核实是否已经执行。此按钮只允许重新操作，不会把本步骤标为完成。确认需要重新操作？', parent=self):
            self.run.states[self.run.index] = 'failed'
            self.status.configure(text='已解除重复执行限制；请核实当前数据后再操作。')
            self.refresh_nav()

    def show_result(self, text):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('1.0', text)
        self.output.configure(state='disabled')

    def poll(self):
        try:
            action, state, data = self.jobs.get_nowait()
        except queue.Empty:
            self.poll_id = self.after(100, self.poll)
            return
        index = self.run.index
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' · ' + str(getattr(self, 'target_label', ''))
        if state != 'ok':
            if action != 'login':
                self.run.states[index] = state
            self.preview = None
            text = ('结果待核实，请勿直接重复操作。\n' if state == 'uncertain' else '本次操作未完成。\n') + data
            self.status.configure(text=text)
            if hasattr(self, 'output'):
                self.show_result(text)
            self.results[index] = {'text': text}
        elif action == 'login':
            self.status.configure(text='登录成功，可以发送本步骤接口。登录本身不会完成本步骤。')
        elif action == 'query':
            columns, rows, truncated = data
            text = f'{stamp} · 查询返回 {len(rows)} 行' + ('（已截断为 2,000 行）' if truncated else '') + '\n'
            text += '\t'.join(columns) + '\n' + '\n'.join('\t'.join('NULL' if v is None else str(v)[:1000] for v in row) for row in rows)
            self.results[index] = dict(text=text, columns=columns, rows=rows)
            self.show_result(text)
            self.run.ready()
            self.status.configure(text='查询成功，请核对结果后点击下一步。零行结果不代表业务检查通过。')
        elif action == 'update':
            self.preview = data
            lines = [f'{stamp} · 预览 {len(data.rows)} 行，尚未修改数据库。']
            for row in data.rows:
                key = ', '.join(f'{k}={display_update_value(row[data.columns.index(k)])}' for k in data.metadata[2])
                for column, param in data.operation['compiled']['changes']:
                    lines.append(f'{key} | {column}: {display_update_value(row[data.columns.index(column)])} → {display_update_value(data.params[param])}')
            text = '\n'.join(lines)
            self.results[index] = {'text': text}
            self.show_result(text)
            self.status.configure(text='预览已生成；核对后点击“确认并提交”。' if data.rows else '未匹配记录，不能提交或完成本步骤。')
        elif action == 'apply':
            text = f'{stamp} · 事务已提交：匹配 {data[0]} 行，实际修改 {data[1]} 行。'
            self.results[index] = {'text': text}
            self.show_result(text)
            self.run.ready()
            self.status.configure(text=text + ' 请确认后进入下一步。')
        elif action == 'api':
            ok, status, text, write = data
            self.results[index] = {'text': f'{stamp}\n{status}\n{text}'}
            self.show_result(self.results[index]['text'])
            if ok:
                self.run.ready()
            else:
                self.run.states[index] = 'uncertain' if write else 'failed'
            self.status.configure(text=status)
        self.set_busy(False)
        self.scroll_id = self.after_idle(lambda: self.canvas.yview_moveto(1))
        self.app.logger.info('event=workflow_operation action=%s state=%s', action, self.run.states[index])
        self.poll_id = self.after(100, self.poll)

    def export(self):
        result = self.results.get(self.run.index, {})
        if self.busy or 'rows' not in result:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile='流程查询结果.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                export_csv(path, result['columns'], result['rows'])
                self.status.configure(text='已导出本步骤当前查询结果。')
            except OSError:
                self.warn('无法导出，请关闭占用文件的 Excel 或选择其他目录。')


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
