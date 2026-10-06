import os
from pathlib import Path
import queue
import sys
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core import load_queries, run_query, export_csv
from settings import load_settings, save_settings
from queries import bind_parameters
from database import validate_connection, test_connection
from diagnostics import VERSION, configure_logging, error_message

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
SETTINGS = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'VideoUploadCheck' / 'connection.json'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'视频上传检查 · v{VERSION}')
        self.geometry('1150x820')
        self.minsize(960, 760)
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Treeview', rowheight=28)
        self.logger = configure_logging(SETTINGS.parent)
        self.inputs = []
        self.parameter_inputs = []
        self.result_context = None
        self.active_task = None
        self.update_window = None
        self.api_window = None
        self.jobs = queue.Queue()
        self.busy = False
        self.columns, self.rows = [], []
        self.vars = {}
        self.parameters = {}
        self.navigation = ttk.Notebook(self)
        self.navigation.pack(fill='both', expand=True)
        self.workflow_tab = ttk.Frame(self.navigation)
        self.advanced_tab = ttk.Frame(self.navigation)
        self.navigation.add(self.workflow_tab, text='客户流程向导')
        self.navigation.add(self.advanced_tab, text='高级工具（实施人员）')
        outer = ttk.Frame(self.advanced_tab, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='数据库固定查询', font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='连接 MySQL → 选择检查项 → 执行查询 → 导出结果').pack(anchor='w', pady=(4, 14))
        conn = ttk.LabelFrame(outer, text='数据库连接', padding=12)
        conn.pack(fill='x')
        defaults = {'host': '', 'port': '3306', 'database': '', 'user': '', 'password': '', 'ssl_ca': ''}
        settings_warning = ''
        remember = os.name == 'nt'
        try:
            saved, settings_warning = load_settings(SETTINGS)
            defaults.update({k: saved[k] for k in defaults if k in saved})
            remember = saved.get('remember_password', os.name == 'nt')
        except (OSError, ValueError, TypeError):
            settings_warning = '保存的连接信息无法读取，请重新填写并保存。'
        self.remember_password = tk.BooleanVar(value=remember)
        labels = [('host', '地址'), ('port', '端口'), ('database', '数据库'), ('user', '用户名'), ('password', '密码'), ('ssl_ca', 'CA 证书路径（可选）')]
        for i, (key, label) in enumerate(labels):
            row, col = divmod(i, 3)
            ttk.Label(conn, text=label).grid(row=row*2, column=col, sticky='w', padx=6)
            var = self.vars[key] = tk.StringVar(value=defaults[key])
            entry = ttk.Entry(conn, textvariable=var, show='*' if key == 'password' else '')
            entry.grid(row=row*2+1, column=col, sticky='ew', padx=6, pady=(3, 8))
            self.inputs.append(entry)
            conn.columnconfigure(col, weight=1)
        buttons = ttk.Frame(conn)
        buttons.grid(row=4, column=0, columnspan=3, sticky='w')
        self.test = ttk.Button(buttons, text='测试连接', command=lambda: self.start(True))
        self.test.pack(side='left')
        save_button = ttk.Button(buttons, text='保存连接信息', command=self.save)
        save_button.pack(side='left', padx=10)
        remember_button = ttk.Checkbutton(buttons, text='记住密码（本机加密保存）', variable=self.remember_password)
        remember_button.pack(side='left')
        ca_button = ttk.Button(buttons, text='选择 CA 证书', command=self.choose_ca)
        ca_button.pack(side='left', padx=10)
        self.inputs.extend([save_button, remember_button, ca_button])
        panel = ttk.LabelFrame(outer, text='固定查询', padding=12)
        panel.pack(fill='x', pady=12)
        catalogue = ttk.Frame(panel)
        catalogue.pack(fill='x')
        self.choice = ttk.Combobox(catalogue, state='readonly')
        self.choice.pack(side='left', fill='x', expand=True)
        self.reload_button = ttk.Button(catalogue, text='重新加载查询', command=self.reload_queries)
        self.reload_button.pack(side='left', padx=(8, 0))
        self.inputs.append(self.reload_button)
        self.description = ttk.Label(panel, wraplength=950)
        self.description.pack(anchor='w', pady=6)
        self.param_frame = ttk.Frame(panel)
        self.param_frame.pack(fill='x')
        self.choice.bind('<<ComboboxSelected>>', self.change_query)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill='x', pady=(0, 10))
        self.execute = ttk.Button(toolbar, text='执行查询', command=self.start)
        self.execute.pack(side='left')
        self.export = ttk.Button(toolbar, text='导出 CSV', command=self.export_result, state='disabled')
        self.export.pack(side='left', padx=10)
        update_button = ttk.Button(toolbar, text='数据库更新…', command=self.open_updates)
        update_button.pack(side='left', padx=(0, 10))
        self.inputs.append(update_button)
        api_button = ttk.Button(toolbar, text='API 调用…', command=self.open_api)
        api_button.pack(side='left', padx=(0, 10))
        self.inputs.append(api_button)
        self.status = ttk.Label(toolbar, text='请配置连接；当前查询为示例')
        self.status.pack(side='left')
        self.progress = ttk.Progressbar(outer, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 5))
        self.result_label = ttk.Label(outer, text='尚无查询结果', wraplength=1080)
        self.result_label.pack(anchor='w', pady=(0, 6))
        result = ttk.Frame(outer)
        result.pack(fill='both', expand=True)
        result.columnconfigure(0, weight=1)
        result.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(result, show='headings', selectmode='browse')
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(result, orient='vertical', command=self.table.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(result, orient='horizontal', command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        ttk.Label(outer, text='最多显示 / 导出 2,000 行；超出请缩小查询范围。建议使用专用 SELECT 只读账号。').pack(anchor='w', pady=(8, 0))
        self.queries = []
        self.reload_queries(initial=True)
        if settings_warning:
            self.after(150, lambda: messagebox.showwarning('连接配置', settings_warning))
        from workflow_ui import WorkflowPanel
        self.workflow = WorkflowPanel(self.workflow_tab, self, ROOT, SETTINGS)
        self.workflow.pack(fill='both', expand=True)
        self.navigation.select(self.workflow_tab)
        self.protocol('WM_DELETE_WINDOW', self.close_app)
        self.poll_id = self.after(100, self.poll)

    def choose_ca(self):
        path = filedialog.askopenfilename(title='选择 CA 证书', filetypes=[('证书文件', '*.pem *.crt *.cer'), ('所有文件', '*.*')])
        if path:
            self.vars['ssl_ca'].set(path)

    def reload_queries(self, initial=False):
        if self.busy:
            return
        previous = self.choice.get()
        try:
            queries = load_queries(ROOT / 'queries.json')
        except (OSError, ValueError, TypeError) as exc:
            # Keep the last valid catalogue if a replacement is malformed.
            if not self.queries:
                self.execute.configure(state='disabled')
                self.status.configure(text='查询配置无效，请修复后重新加载')
            message = str(exc)
            if initial:
                self.after(100, lambda: messagebox.showerror('查询配置错误', message))
            else:
                messagebox.showerror('加载失败，保留原查询配置', message)
            return
        self.queries = queries
        names = [q['name'] for q in queries]
        self.choice.configure(values=names)
        self.choice.current(names.index(previous) if previous in names else 0)
        self.change_query()
        self.execute.configure(state='normal')
        if not initial:
            self.status.configure(text=f'已加载 {len(queries)} 个查询')

    def change_query(self, event=None):
        for widget in self.param_frame.winfo_children():
            widget.destroy()
        self.parameters = {}
        self.parameter_inputs = []
        query = self.queries[self.choice.current()]
        self.description.configure(text=query.get('description', ''))
        for i, spec in enumerate(query['params']):
            row, group = divmod(i, 2)
            label = spec['label'] + ('（年-月-日 时:分:秒）' if spec['type'] == 'datetime' else '')
            ttk.Label(self.param_frame, text=label).grid(row=row*2, column=group, sticky='w', padx=(0, 16))
            var = self.parameters[spec['name']] = tk.StringVar(value=str(spec['default']))
            entry = ttk.Entry(self.param_frame, textvariable=var, width=38)
            entry.grid(row=row*2+1, column=group, sticky='ew', padx=(0, 16), pady=(2, 6))
            self.param_frame.columnconfigure(group, weight=1)
            self.parameter_inputs.append(entry)

    def config(self):
        return validate_connection({k: v.get() for k, v in self.vars.items()})

    def save(self):
        try:
            save_settings(SETTINGS, self.config(), self.remember_password.get())
            message = '连接信息及密码已保存，下次启动自动填入。' if self.remember_password.get() else '连接信息已保存，已移除之前保存的密码。'
            messagebox.showinfo('已保存', message)
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法保存', str(exc))

    def set_busy(self, value):
        self.busy = value
        self.navigation.tab(self.workflow_tab, state='disabled' if value else 'normal')
        for widget in self.inputs + self.parameter_inputs:
            widget.configure(state='disabled' if value else 'normal')
        self.test.configure(state='disabled' if value else 'normal')
        self.execute.configure(state='disabled' if value or not self.queries else 'normal')
        self.choice.configure(state='disabled' if value else 'readonly')
        self.export.configure(state='disabled' if value or self.result_context is None else 'normal')
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start(self, test=False):
        if self.busy:
            return
        try:
            config = self.config()
            query = None if test else self.queries[self.choice.current()]
            params = {} if test else bind_parameters(query, {k: v.get() for k, v in self.parameters.items()})
        except (ValueError, IndexError) as exc:
            messagebox.showerror('请检查输入', str(exc))
            return
        # Snapshot the exact inputs; widgets remain frozen until completion.
        self.active_task = dict(config=config, remember=self.remember_password.get(),
                                query_name='连接测试' if test else query['name'], test=test,
                                started=time.monotonic())
        self.set_busy(True)
        self.status.configure(text='正在测试连接…' if test else '正在查询…')
        def worker():
            started = time.monotonic()
            try:
                data = test_connection(config) if test else run_query(config, query['sql'], params)
                self.jobs.put(('ok', data, time.monotonic() - started))
            except Exception as exc:
                self.jobs.put(('error', error_message(exc, self.logger), time.monotonic() - started))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            state, data, seconds = self.jobs.get_nowait()
        except queue.Empty:
            if self.busy:
                seconds = time.monotonic() - self.active_task['started']
                operation = '测试连接' if self.active_task['test'] else '查询'
                self.status.configure(text=f'正在{operation} · 已等待 {seconds:.0f} 秒')
            self.poll_id = self.after(100, self.poll)
            return
        task = self.active_task
        self.active_task = None
        self.set_busy(False)
        if state == 'error':
            suffix = '，下方保留上次结果' if self.result_context is not None else ''
            self.status.configure(text='本次操作失败' + suffix)
            messagebox.showerror('操作失败', data)
        else:
            if task['test']:
                self.status.configure(text=f'连接正常 · MySQL {data[0]} · {seconds:.2f} 秒')
            else:
                self.columns, self.rows, truncated = data
                self.result_context = dict(name=task['query_name'], database=task['config']['database'],
                                           host=task['config']['host'], timestamp=datetime.now(), truncated=truncated)
                self.table.delete(*self.table.get_children())
                ids = [str(i) for i in range(len(self.columns))]
                self.table.configure(columns=ids)
                for key, label in zip(ids, self.columns):
                    self.table.heading(key, text=label)
                    self.table.column(key, width=160, minwidth=90, stretch=False)
                for row in self.rows:
                    # Keep full values for CSV; bound individual on-screen cells.
                    self.table.insert('', 'end', values=['NULL' if v is None else str(v)[:1000] for v in row])
                self.export.configure(state='normal')
                context = self.result_context
                suffix = ' · 结果已截断，导出也仅含当前行' if truncated else ''
                self.result_label.configure(text=f"结果：{context['name']} · {context['host']} / {context['database']} · {context['timestamp']:%Y-%m-%d %H:%M:%S}{suffix}")
                self.status.configure(text=f'{len(self.rows)} 行 · {seconds:.2f} 秒' if self.rows else f'查询成功，无符合条件的数据 · {seconds:.2f} 秒')
            # Persistence must not turn a successful database operation into a failure.
            try:
                save_settings(SETTINGS, task['config'], task['remember'])
            except (OSError, ValueError):
                self.logger.warning('event=settings_save_failed')
                messagebox.showwarning('本次操作成功，连接信息未保存', '无法保存连接信息；查询结果仍可查看和导出。请检查本机文件权限或稍后点击“保存连接信息”。')
            self.logger.info('event=operation_success test=%s elapsed=%.2f', task['test'], seconds)
        self.poll_id = self.after(100, self.poll)

    def export_result(self):
        if self.result_context is None or self.busy:
            return
        timestamp = self.result_context['timestamp'].strftime('%Y%m%d_%H%M%S')
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=f'查询结果_{timestamp}.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                export_csv(path, self.columns, self.rows)
                suffix = '（结果已截断；如需更多数据请缩小范围分批查询）' if self.result_context['truncated'] else ''
                messagebox.showinfo('导出成功', f'已导出 {len(self.rows)} 行。{suffix}')
            except OSError:
                messagebox.showerror('导出失败', '无法写入文件，请关闭 Excel 中的同名文件，或选择可写目录重试。')

    def open_updates(self):
        if self.busy:
            return
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift()
            return
        try:
            config = self.config()
        except ValueError as exc:
            messagebox.showerror('请检查连接信息', str(exc))
            return
        from updates_ui import UpdateWindow
        self.update_window = UpdateWindow(self, config, ROOT / 'updates.json', self.logger)

    def open_api(self):
        if self.busy:
            return
        if self.api_window is not None and self.api_window.winfo_exists():
            self.api_window.lift()
            return
        from api_ui import ApiWindow
        self.api_window = ApiWindow(self, ROOT / 'api_requests.json', SETTINGS.parent / 'api_profiles.json', self.logger)

    def close_app(self):
        if self.workflow.busy:
            messagebox.showinfo('流程进行中', '请等待当前操作返回后再关闭，避免无法确认执行结果。')
            return
        if self.api_window is not None and self.api_window.winfo_exists():
            self.api_window.lift()
            return
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift()
            return
        if self.busy:
            messagebox.showinfo('操作进行中', '请等待本次操作完成后关闭；读取超时后会自动返回。')
            return
        if self.workflow.run and (self.workflow.drafts or self.workflow.results) and not messagebox.askyesno('关闭工具', '流程进度和本次输入不会保存，已执行的修改不会撤销。确认关闭？'):
            return
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


if __name__ == '__main__':
    App().mainloop()
