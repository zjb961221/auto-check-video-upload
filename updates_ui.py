from ui_recovery import guarded_poll
from ui_theme import ScrollFrame, size_window
"""A modal update workflow isolated from the read-only query window."""
from copy import deepcopy
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from diagnostics import error_message
from updates import load_updates, preview_update, apply_update, UpdateError, CommitUncertain, bind_update_parameters, display_update_value


class UpdateWindow(tk.Toplevel):
    def __init__(self, parent, config, config_path, logger):
        super().__init__(parent)
        self.title('数据库更新 · 先预览，再确认提交')
        size_window(self, 1100, 820)
        self.config_snapshot = deepcopy(config)
        self.config_path = config_path
        self.logger = logger
        self.busy = False
        self.preview = None
        self.operations = []
        self.parameters = {}
        self.inputs = []
        self.modes, self.entries, self.mode_widgets = {}, {}, []
        self.jobs = queue.Queue()
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        viewport = ScrollFrame(self, padding=16)
        viewport.pack(fill='both', expand=True)
        body = viewport.content
        ttk.Label(body, text=f"目标：{config['host']}:{config['port']} / {config['database']} · 账号：{config['user']}", wraplength=950).pack(anchor='w')
        ttk.Label(body, text='此窗口会修改数据库。核对预览和目标库后再提交。').pack(anchor='w', pady=(4, 10))
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.choice = ttk.Combobox(row, state='readonly')
        self.choice.pack(side='left', fill='x', expand=True)
        self.choice.bind('<<ComboboxSelected>>', self.change_operation)
        self.reload_button = ttk.Button(row, text='重新加载更新配置', command=self.reload)
        self.reload_button.pack(side='left', padx=(8, 0))
        self.description = ttk.Label(body, wraplength=950)
        self.description.pack(anchor='w', pady=8)
        self.form = ttk.Frame(body)
        self.form.pack(fill='x')
        ttk.Label(body, text='配置的更新语句（值通过参数绑定）：').pack(anchor='w', pady=(8, 4))
        self.sql_text = tk.Text(body, height=3, wrap='word', state='disabled')
        self.sql_text.pack(fill='x')
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=10)
        self.preview_button = ttk.Button(controls, style='Primary.TButton', text='1. 预览更新', command=self.start_preview)
        self.preview_button.pack(side='left')
        self.submit_button = ttk.Button(controls, style='Danger.TButton', text='2. 确认并提交', command=self.submit, state='disabled')
        self.submit_button.pack(side='left', padx=10)
        self.status = ttk.Label(body, text='请先配置更新操作', wraplength=950)
        self.status.pack(anchor='w', pady=(0, 6))
        self.progress = ttk.Progressbar(body, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 8))
        result = ttk.Frame(body)
        result.pack(fill='both', expand=True)
        result.columnconfigure(0, weight=1)
        result.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(result, show='headings', columns=('key', 'column', 'before', 'after'))
        for col, label, width in [('key', '记录主键', 200), ('column', '字段', 130), ('before', '当前值', 300), ('after', '计划新值', 300)]:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, minwidth=100, stretch=False)
        self.table.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(result, orient='vertical', command=self.table.yview)
        y.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(result, orient='horizontal', command=self.table.xview)
        x.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        ttk.Label(body, text='预览不修改数据；计划值不是数据库转换后的值。预览有效期 5 分钟，修改参数后须重新预览。', wraplength=950).pack(anchor='w', pady=(8, 0))
        if hasattr(parent, "design"):
            parent.design.paint_widgets(self)
        self.reload()
        self.poll_id = self.after(100, self.poll)
        self.grab_set()

    def invalidate(self, *args):
        self.preview = None
        self.submit_button.configure(state='disabled')
        self.status.configure(text='配置或参数已变化，请重新预览；下方旧预览不能用于提交')

    def reload(self):
        if self.busy:
            return
        self.invalidate()
        try:
            operations = load_updates(self.config_path)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror('更新配置错误', str(exc), parent=self)
            return
        self.operations = operations
        self.choice.configure(values=[op['name'] for op in operations])
        self.preview_button.configure(state='normal' if operations else 'disabled')
        if operations:
            self.choice.current(0)
            self.change_operation()
        else:
            self.choice.set('')
            for widget in self.form.winfo_children():
                widget.destroy()
            self.parameters, self.inputs = {}, []
            self.modes, self.entries, self.mode_widgets = {}, {}, []
            self.sql_text.configure(state='normal')
            self.sql_text.delete('1.0', 'end')
            self.sql_text.configure(state='disabled')
            self.description.configure(text='未配置更新操作')
            self.status.configure(text='请参考 updates.example.json，把自己的配置写入 EXE 同目录的 updates.json，再点击重新加载。')

    def change_operation(self, event=None):
        self.invalidate()
        for widget in self.form.winfo_children():
            widget.destroy()
        self.parameters, self.inputs = {}, []
        self.modes, self.entries, self.mode_widgets = {}, {}, []
        operation = self.operations[self.choice.current()]
        self.description.configure(text=f"{operation.get('description', '')}（最多 {operation['max_rows']} 行）")
        self.sql_text.configure(state='normal')
        self.sql_text.delete('1.0', 'end')
        self.sql_text.insert('1.0', operation['sql'])
        self.sql_text.configure(state='disabled')
        condition_params = {p for _, _, p in operation['compiled']['conditions']}
        for index, spec in enumerate(operation['params']):
            row, col = divmod(index, 2)
            ttk.Label(self.form, text=spec['label']).grid(row=row*2, column=col, sticky='w')
            var = tk.StringVar(value=str(spec['default']))
            var.trace_add('write', self.invalidate)
            self.parameters[spec['name']] = var
            cell = ttk.Frame(self.form)
            cell.grid(row=row*2+1, column=col, sticky='ew', padx=(0, 12), pady=(3, 6))
            entry = ttk.Entry(cell, textvariable=var)
            entry.pack(side='left', fill='x', expand=True)
            self.entries[spec['name']] = entry
            if spec['name'] not in condition_params:
                choices = ['输入值', '空字符串', '数据库 NULL'] if spec['type'] == 'text' else ['输入值', '数据库 NULL']
                mode = tk.StringVar(value='输入值')
                self.modes[spec['name']] = mode
                selector = ttk.Combobox(cell, textvariable=mode, values=choices, state='readonly', width=12)
                selector.pack(side='left', padx=(6, 0))
                self.mode_widgets.append(selector)
                mode.trace_add('write', self.mode_changed)
            self.form.columnconfigure(col, weight=1)
            self.inputs.append(entry)

    def mode_changed(self, *args):
        self.invalidate()
        self.refresh_inputs()

    def refresh_inputs(self):
        for name, entry in self.entries.items():
            special = name in self.modes and self.modes[name].get() != '输入值'
            entry.configure(state='disabled' if self.busy or special else 'normal')
        for widget in self.mode_widgets:
            widget.configure(state='disabled' if self.busy else 'readonly')

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.inputs + [self.reload_button]:
            widget.configure(state='disabled' if busy else 'normal')
        self.refresh_inputs()
        self.choice.configure(state='disabled' if busy else 'readonly')
        self.preview_button.configure(state='disabled' if busy or not self.operations else 'normal')
        self.submit_button.configure(state='disabled' if busy or self.preview is None or not self.preview.rows else 'normal')
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def launch(self, action, function):
        self.set_busy(True)
        self.status.configure(text='正在读取预览…' if action == 'preview' else '正在核对并提交，请勿关闭程序或断开网络…')
        def worker():
            try:
                self.jobs.put((action, 'ok', function()))
            except CommitUncertain as exc:
                self.logger.error('event=update_commit_uncertain')
                self.jobs.put((action, 'uncertain', str(exc)))
            except UpdateError as exc:
                self.logger.warning('event=update_rejected')
                self.jobs.put((action, 'error', str(exc)))
            except Exception as exc:
                self.jobs.put((action, 'error', error_message(exc, self.logger)))
        threading.Thread(target=worker, daemon=True).start()

    def start_preview(self):
        if self.busy or not self.operations:
            return
        self.invalidate()
        operation = deepcopy(self.operations[self.choice.current()])
        values = {k: v.get() for k, v in self.parameters.items()}
        # User input validation errors should stay actionable instead of generic.
        mapping = {'输入值': 'value', '空字符串': 'empty', '数据库 NULL': 'null'}
        modes = {k: mapping[v.get()] for k, v in self.modes.items()}
        try:
            bind_update_parameters(operation, values, modes)
        except ValueError as exc:
            messagebox.showerror('请检查参数', str(exc), parent=self)
            return
        self.launch('preview', lambda: preview_update(self.config_snapshot, operation, values, modes=modes))

    def submit(self):
        if self.busy or self.preview is None or not self.preview.rows:
            return
        snapshot = self.preview
        target = f"{self.config_snapshot['host']} / {self.config_snapshot['database']}"
        if not messagebox.askyesno('确认修改数据库', f"目标：{target}\n操作：{snapshot.operation['name']}\n匹配记录：{len(snapshot.rows)} 行\n\n确认将预览中的字段更新为计划值？", parent=self):
            return
        self.preview = None  # Single-use preview: prevents accidental resubmission.
        self.launch('apply', lambda: apply_update(snapshot))

    def poll(self):
        guarded_poll(self, self.consume_result, self.recover_result)

    def recover_result(self, exc):
        self.preview = None
        self.set_busy(False)
        self.status.configure(text='更新结果显示失败；提交可能已经成功，请先查询核实，不要直接再次提交。')
        messagebox.showerror('更新结果待核实', error_message(exc, self.logger), parent=self)

    def consume_result(self):
        try:
            action, state, data = self.jobs.get_nowait()
        except queue.Empty:
            return
        if state == 'ok' and action == 'preview':
            self.preview = data
            self.table.delete(*self.table.get_children())
            compiled = data.operation['compiled']
            for row in data.rows:
                key = ', '.join(f'{k}={row[data.columns.index(k)]}' for k in data.metadata[2])
                for column, param in compiled['changes']:
                    before = row[data.columns.index(column)]
                    self.table.insert('', 'end', values=(key, column, display_update_value(before), display_update_value(data.params[param])))
            self.status.configure(text=f'已预览 {len(data.rows)} 条记录，尚未修改数据库。' if data.rows else '没有匹配记录，不可提交。')
        elif state == 'ok':
            matched, changed = data
            self.table.delete(*self.table.get_children())
            self.status.configure(text=f'提交成功：匹配 {matched} 行，实际修改 {changed} 行。')
            self.logger.info('event=update_committed matched=%d changed=%d', matched, changed)
            messagebox.showinfo('更新成功', f'事务已提交。匹配 {matched} 行，实际修改 {changed} 行。', parent=self)
        else:
            self.preview = None
            self.status.configure(text='提交结果无法确认，请查询核实，勿重复提交。' if state == 'uncertain' else '本次操作未完成；旧预览已失效，请处理错误后重新预览。')
            messagebox.showerror('提交结果待核实' if state == 'uncertain' else '更新操作失败', data, parent=self)
        self.set_busy(False)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请等待当前预览或提交返回，避免中断后无法判断提交结果。', parent=self)
            return
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
