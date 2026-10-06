"""A shared selector above both database forms; selection never connects."""
import tkinter as tk
from tkinter import ttk, messagebox
import weakref
from database_profiles import load_profiles, LocalProfiles, ProfileError

MANUAL = '手动连接（未选择煤矿）'


class MineSelector:
    def __init__(self, app, catalogue, local_path):
        self.app, self.catalogue = app, catalogue
        self.store = LocalProfiles(local_path)
        self.profiles, self.active_id = [], ''
        self.name = tk.StringVar(value=MANUAL)
        self.status = tk.StringVar(value='可手动连接，也可在 database_profiles.json 中配置煤矿。')
        self.widgets = weakref.WeakSet()
        self.combos = weakref.WeakSet()
        self.dirty, self.applying = False, False
        for var in app.vars.values():
            var.trace_add('write', self.edited)
        self.reload(initial=True)

    def values(self):
        return [MANUAL] + [p['name'] for p in self.profiles]

    def add(self, parent):
        box = ttk.Frame(parent)
        ttk.Label(box, text='选择煤矿').grid(row=0, column=0, sticky='w')
        combo = ttk.Combobox(box, textvariable=self.name, values=self.values(), state='readonly', width=24)
        combo.grid(row=0, column=1, sticky='ew', padx=8)
        combo.bind('<<ComboboxSelected>>', self.choose)
        reload_button = ttk.Button(box, text='重载煤矿配置', command=self.reload)
        reload_button.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        save_button = ttk.Button(box, text='保存当前煤矿连接', command=self.save)
        save_button.grid(row=1, column=2, sticky='e', pady=(4, 0))
        ttk.Label(box, textvariable=self.status, wraplength=650, style='Muted.TLabel').grid(row=2, column=0, columnspan=3, sticky='w', pady=(3, 6))
        box.columnconfigure(1, weight=1)
        self.widgets.update((combo, reload_button, save_button))
        self.combos.add(combo)
        return box

    def blocked(self):
        return self.app.busy or bool(getattr(getattr(self.app, 'workflow', None), 'busy', False))

    def edited(self, *args):
        if self.applying:
            return
        self.dirty = True
        if self.active_id:
            self.status.set('当前煤矿连接已手动修改；保存后仅覆盖本机该煤矿配置。')

    def current(self):
        return next((p for p in self.profiles if p['id'] == self.active_id), None)

    def discard_allowed(self):
        return not self.dirty or messagebox.askyesno('切换连接配置', '当前连接字段有未保存的修改。确认用选中煤矿配置覆盖这些字段？', parent=self.app)

    def choose(self, event=None):
        old = self.current()
        target = next((p for p in self.profiles if p['name'] == self.name.get()), None)
        if self.blocked() or not self.discard_allowed():
            self.name.set(old['name'] if old else MANUAL)
            return
        self.apply(target)

    def apply(self, profile):
        if profile:
            try:
                config, warning = self.store.load(profile)
            except ProfileError as exc:
                from database_profiles import resolve_profile
                config, _ = resolve_profile(profile)
                config['password'] = ''
                warning = str(exc)
        else:
            config = dict(host='', port='3306', database='', user='', password='', ssl_ca='')
            warning = ''
        self.applying = True
        self.app.mine_applying = True
        try:
            for key, var in self.app.vars.items():
                var.set(config.get(key, ''))
            self.active_id = profile['id'] if profile else ''
            self.name.set(profile['name'] if profile else MANUAL)
            self.dirty = False
            target = f"{config['host']}:{config['port']} / {config['database']}"
            self.status.set(warning or (f'已回填：{target}；尚未连接数据库。' if profile else '请手动填写数据库连接信息。'))
        finally:
            self.app.mine_applying = False
            self.applying = False
        if hasattr(self.app, 'workflow'):
            self.app.workflow.connection_changed()
        if self.app.result_context is not None:
            self.app.result_label.configure(text='数据库连接已变化，下方为旧目标的查询结果，请重新执行查询。')

    def reload(self, initial=False):
        if self.blocked() or not initial and not self.discard_allowed():
            return
        try:
            profiles, default = load_profiles(self.catalogue)
        except ProfileError as exc:
            self.status.set(str(exc))
            if not initial:
                messagebox.showerror('煤矿配置错误', str(exc), parent=self.app)
            return
        old_id = self.active_id
        self.profiles = profiles
        for combo in tuple(self.combos):
            if combo.winfo_exists():
                combo.configure(values=self.values())
        if initial:
            try:
                old_id = self.store.read().get('selected_id', '') or default
            except ProfileError as exc:
                self.status.set(str(exc))
                old_id = default
        target = next((p for p in profiles if p['id'] == old_id), None)
        if target:
            self.apply(target)
        elif old_id:
            self.apply(None)  # Removed profiles must not leave old credentials selected.
            self.status.set('原煤矿配置已移除，连接字段已清空；请选择其他煤矿或手动填写。')

    def save(self):
        if self.blocked():
            return
        profile = self.current()
        if profile is None:
            messagebox.showinfo('请先选择煤矿', '手动连接请使用“保存数据库连接”；如需保存到煤矿，请先在文件中配置并选择煤矿。', parent=self.app)
            return
        try:
            self.store.save(profile, self.app.config(), self.app.remember_password.get())
        except (OSError, ValueError, TypeError):
            messagebox.showerror('煤矿连接未保存', '保存失败，请检查文件权限和 Windows 密码加密功能；原保存文件未被替换。', parent=self.app)
            return
        self.dirty = False
        self.status.set('已保存本机当前煤矿连接；其他煤矿配置未改变。' + ('密码已加密保存。' if self.app.remember_password.get() else '未保存密码。'))

    def set_busy(self, busy):
        for widget in tuple(self.widgets):
            if widget.winfo_exists():
                widget.configure(state='disabled' if busy else 'readonly' if widget in self.combos else 'normal')
