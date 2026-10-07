"""Support and implementation tools without exposing credentials in diagnostics."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from operations import check_configs,backup_configs,restore_configs,export_support


def show_text(app,title,text):
    window=tk.Toplevel(app);window.title(title);window.geometry('850x600')
    box=tk.Text(window,wrap='word');box.pack(fill='both',expand=True);box.insert('1.0',text);box.configure(state='disabled')
    ttk.Button(window,text='关闭',command=window.destroy).pack(pady=8)


def install_tools(app,root):
    menu=tk.Menu(app);tools=tk.Menu(menu,tearoff=False);menu.add_cascade(label='检查与支持',menu=tools);app.configure(menu=menu)
    def idle():
        return not app.busy and not app.workflow.busy and not any(getattr(w,'busy',False) for w in (app.api_window,app.update_window) if w and w.winfo_exists())
    def check():
        if not idle():return
        rows=check_configs(root)
        show_text(app,'配置检查','\n\n'.join(f'{r["file"]}：{r["detail"]}' for r in rows))
    def history():
        try:records=app.audit.read()
        except (OSError,ValueError):messagebox.showerror('操作记录','无法读取本机操作记录',parent=app);return
        show_text(app,'最近操作记录','\n'.join(f'{r["time"]} · {r["event"]} · {r["status"]} · 行数 {r.get("rows","—")} · 编号 {r["id"]}' for r in records) or '尚无操作记录')
    def support():
        path=filedialog.asksaveasfilename(parent=app,defaultextension='.json',initialfile='运维诊断包.json')
        if not path:return
        try:export_support(root,app.audit,path)
        except (OSError,ValueError):messagebox.showerror('诊断包','导出失败，请检查路径权限',parent=app)
        else:messagebox.showinfo('已导出','已导出版本、配置校验状态与操作计数，不包含原始配置或接口响应。',parent=app)
    def backup():
        if app.customer_mode or not idle():return
        path=filedialog.asksaveasfilename(parent=app,defaultextension='.json',initialfile='现场配置加密备份.json')
        if not path:return
        try:backup_configs(root,path)
        except (OSError,ValueError):messagebox.showerror('备份失败','加密备份失败，请检查 Windows 环境和目录权限',parent=app)
        else:messagebox.showinfo('备份完成','此备份仅当前 Windows 用户可解密，不是数据库业务数据备份。',parent=app)
    def restore():
        if app.customer_mode or not idle():return
        path=filedialog.askopenfilename(parent=app,filetypes=[('加密配置备份','*.json')])
        if not path:return
        if not messagebox.askyesno('恢复现场配置','恢复将替换对应配置文件。确认已做好备份？恢复后需重启程序。',parent=app):return
        try:restore_configs(root,path)
        except (OSError,ValueError,KeyError,TypeError):messagebox.showerror('恢复失败','备份无法解密、校验未通过或写入失败，请检查原 Windows 用户和目录权限。',parent=app)
        else:messagebox.showinfo('配置已恢复','请关闭并重新启动工具，当前运行不会自动执行恢复的配置。',parent=app)
    def mode():
        if not idle():return
        app.customer_mode=not app.customer_mode
        app.navigation.tab(app.advanced_tab,state='hidden' if app.customer_mode else 'normal')
        app.navigation.select(app.workflow_tab)
        app.title(('客户模式' if app.customer_mode else '实施模式')+' · 客户自查运维工具')
    for label,command in [('检查全部配置',check),('查看操作记录',history),('导出脱敏诊断包',support),('加密备份现场配置',backup),('恢复现场配置备份',restore),('切换客户 / 实施模式',mode)]:tools.add_command(label=label,command=command)
    if app.customer_mode:app.navigation.tab(app.advanced_tab,state='hidden')
