import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core import load_queries, run_query, export_csv
from settings import load_settings, save_settings

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
SETTINGS = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'VideoUploadCheck' / 'connection.json'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('视频上传检查 · 数据库查询工具')
        self.geometry('1100x740')
        self.minsize(850, 620)
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Treeview', rowheight=28)
        self.jobs = queue.Queue()
        self.busy = False
        self.columns, self.rows = [], []
        self.vars = {}
        self.parameters = {}
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='数据库固定查询', font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='连接 MySQL → 选择检查项 → 执行查询 → 导出结果').pack(anchor='w', pady=(4, 14))
        conn = ttk.LabelFrame(outer, text='数据库连接', padding=12)
        conn.pack(fill='x')
        defaults = {'host': '', 'port': '3306', 'database': '', 'user': '', 'password': '', 'ssl_ca': ''}
        settings_warning = ''
        remember = True
        try:
            saved, settings_warning = load_settings(SETTINGS)
            defaults.update({k: saved[k] for k in defaults if k in saved})
            remember = saved.get('remember_password', True)
        except (OSError, ValueError, TypeError):
            settings_warning = '保存的连接信息无法读取，请重新填写并保存。'
        self.remember_password = tk.BooleanVar(value=remember)
        labels = [('host', '地址'), ('port', '端口'), ('database', '数据库'), ('user', '用户名'), ('password', '密码'), ('ssl_ca', 'CA 证书路径（可选）')]
        for i, (key, label) in enumerate(labels):
            row, col = divmod(i, 3)
            ttk.Label(conn, text=label).grid(row=row*2, column=col, sticky='w', padx=6)
            var = self.vars[key] = tk.StringVar(value=defaults[key])
            ttk.Entry(conn, textvariable=var, show='*' if key == 'password' else '').grid(row=row*2+1, column=col, sticky='ew', padx=6, pady=(3, 8))
            conn.columnconfigure(col, weight=1)
        buttons = ttk.Frame(conn)
        buttons.grid(row=4, column=0, columnspan=3, sticky='w')
        self.test = ttk.Button(buttons, text='测试连接', command=lambda: self.start(True))
        self.test.pack(side='left')
        ttk.Button(buttons, text='保存连接信息', command=self.save).pack(side='left', padx=10)
        ttk.Checkbutton(buttons, text='记住密码（本机加密保存）', variable=self.remember_password).pack(side='left')
        panel = ttk.LabelFrame(outer, text='固定查询', padding=12)
        panel.pack(fill='x', pady=12)
        self.choice = ttk.Combobox(panel, state='readonly')
        self.choice.pack(fill='x')
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
        self.status = ttk.Label(toolbar, text='请配置连接；当前查询为示例')
        self.status.pack(side='left')
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
        try:
            self.queries = load_queries(ROOT / 'queries.json')
            self.choice.configure(values=[q['name'] for q in self.queries])
            self.choice.current(0)
            self.change_query()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.execute.configure(state='disabled')
            self.status.configure(text='查询配置无效')
            self.after(100, lambda msg=str(exc): messagebox.showerror('查询配置错误', msg))
        if settings_warning:
            self.after(150, lambda: messagebox.showwarning('连接配置', settings_warning))
        self.after(100, self.poll)

    def change_query(self, event=None):
        for widget in self.param_frame.winfo_children():
            widget.destroy()
        self.parameters = {}
        query = self.queries[self.choice.current()]
        self.description.configure(text=query.get('description', ''))
        for i, name in enumerate(query.get('params', [])):
            ttk.Label(self.param_frame, text=name).grid(row=i, column=0, sticky='w', pady=2)
            var = self.parameters[name] = tk.StringVar()
            ttk.Entry(self.param_frame, textvariable=var, width=45).grid(row=i, column=1, sticky='w', padx=8, pady=2)

    def config(self):
        result = {k: v.get().strip() if k != 'password' else v.get() for k, v in self.vars.items()}
        if not all(result[k] for k in ('host', 'database', 'user')):
            raise ValueError('请填写地址、数据库和用户名')
        try:
            port = int(result['port'])
        except ValueError:
            raise ValueError('端口必须是 1–65535 的整数') from None
        if not 1 <= port <= 65535:
            raise ValueError('端口必须是 1–65535 的整数')
        if result['ssl_ca'] and not Path(result['ssl_ca']).is_file():
            raise ValueError('CA 证书文件不存在')
        return result

    def save(self):
        try:
            data = self.config()
            save_settings(SETTINGS, data, self.remember_password.get())
            message = '连接信息及密码已保存，下次启动自动填入。' if self.remember_password.get() else '连接信息已保存，已移除之前保存的密码。'
            messagebox.showinfo('已保存', message)
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法保存', str(exc))

    def start(self, test=False):
        if self.busy:
            return
        try:
            config = self.config()
            query = {'sql': 'SELECT 1 AS 连接正常'} if test else self.queries[self.choice.current()]
            params = {} if test else {k: v.get() for k, v in self.parameters.items()}
            if any(not v.strip() for v in params.values()):
                raise ValueError('请填写所有查询参数')
        except (ValueError, IndexError) as exc:
            messagebox.showerror('请检查输入', str(exc))
            return
        try:
            save_settings(SETTINGS, config, self.remember_password.get())
        except (OSError, ValueError) as exc:
            messagebox.showerror('连接信息保存失败', str(exc))
            return
        self.busy = True
        self.test.configure(state='disabled')
        self.execute.configure(state='disabled')
        self.choice.configure(state='disabled')
        self.export.configure(state='disabled')
        self.columns, self.rows = [], []
        self.table.delete(*self.table.get_children())
        self.status.configure(text='正在测试连接…' if test else '正在查询，请稍候…')
        def worker():
            started = time.monotonic()
            try:
                data = run_query(config, query['sql'], params)
                self.jobs.put(('ok', data, time.monotonic() - started, test))
            except Exception as exc:
                # Do not display raw server errors that may echo query parameter data.
                code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
                messages = {1045: '账号或密码错误，或该账号没有远程连接权限。',
                            1049: '数据库不存在。', 1146: '查询表不存在，请核对 SQL 配置。',
                            1054: '查询字段不存在，请核对 SQL 配置。',
                            1142: '账号没有查询权限。', 1064: 'SQL 语法错误，请联系实施人员。',
                            2003: '无法连接，请检查地址、端口、网络及防火墙。',
                            2013: '连接中断或查询超时，请缩小范围并检查网络。',
                            3024: '查询超过 30 秒，请缩小范围。'}
                msg = messages.get(code, '连接或查询失败，请检查 MySQL 版本、证书、网络和查询配置。')
                self.jobs.put(('error', f'{msg}\n错误类型：{type(exc).__name__}；代码：{code or "无"}', 0, test))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            state, data, seconds, test = self.jobs.get_nowait()
        except queue.Empty:
            self.after(100, self.poll)
            return
        self.busy = False
        self.test.configure(state='normal')
        self.execute.configure(state='normal' if self.queries else 'disabled')
        self.choice.configure(state='readonly')
        if state == 'error':
            self.status.configure(text='操作失败，无本次查询结果')
            messagebox.showerror('操作失败', data)
        elif test:
            self.status.configure(text=f'连接测试通过 · {seconds:.2f} 秒')
        else:
            self.columns, self.rows, truncated = data
            ids = [str(i) for i in range(len(self.columns))]
            self.table.configure(columns=ids)
            for key, label in zip(ids, self.columns):
                self.table.heading(key, text=label)
                self.table.column(key, width=160, minwidth=90, stretch=False)
            for row in self.rows:
                self.table.insert('', 'end', values=['NULL' if v is None else str(v) for v in row])
            self.export.configure(state='normal')
            suffix = ' · 已截断，请缩小范围' if truncated else ''
            self.status.configure(text=f'{len(self.rows)} 行 · {seconds:.2f} 秒{suffix}')
        self.after(100, self.poll)

    def export_result(self):
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='查询结果.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                export_csv(path, self.columns, self.rows)
                messagebox.showinfo('导出成功', f'已导出 {len(self.rows)} 行。')
            except OSError as exc:
                messagebox.showerror('导出失败', str(exc))


if __name__ == '__main__':
    App().mainloop()
