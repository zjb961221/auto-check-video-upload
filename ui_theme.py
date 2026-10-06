"""Native Tk design system: named fonts, scalable icons and local UI preferences."""
import ctypes
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk, font

PALETTES = {
    '曜石深色': dict(bg='#0b1220', panel='#111d30', field='#192840', text='#e5edf9', muted='#9bacc5',
                 accent='#38d9ef', accent_fg='#062330', violet='#9686ff', border='#2b405b', selected='#254f70',
                 danger='#ffad80', success='#59d9ac'),
    '清爽浅色': dict(bg='#edf2f8', panel='#ffffff', field='#f5f8fd', text='#152b46', muted='#526580',
                 accent='#076caa', accent_fg='#ffffff', violet='#6249b4', border='#c5d2e2', selected='#d1e7fa',
                 danger='#a54217', success='#13724f'),
}
ZOOMS = (85, 100, 115, 130, 150)


def enable_dpi_awareness():
    if os.name == 'nt':
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def read_preferences(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {}
        result = {}
        if type(data.get('zoom')) is int and data['zoom'] in ZOOMS:
            result['zoom'] = data['zoom']
        if data.get('theme') in PALETTES:
            result['theme'] = data['theme']
        return result
    except (OSError, ValueError, TypeError):
        return {}


def save_preferences(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def size_window(window, width, height):
    """Keep the initial window reachable on a 1366x768 laptop too."""
    window.update_idletasks()
    w = max(640, min(width, window.winfo_screenwidth() - 48))
    h = max(480, min(height, window.winfo_screenheight() - 100))
    window.geometry(f'{w}x{h}')
    window.minsize(min(820, w), min(540, h))


class DesignSystem:
    def __init__(self, root, path):
        self.root, self.path = root, path
        prefs = read_preferences(path)
        self.zoom = prefs.get('zoom', 100)
        self.theme = prefs.get('theme', '曜石深色')
        self.fonts = {}
        self.icons = {}
        self.icon_widgets = []
        self.fullscreen = False
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        for name, size, weight in [('AppFont', 10, 'normal'), ('AppSmall', 9, 'normal'),
                                   ('AppTitle', 21, 'bold'), ('AppHeading', 14, 'bold'), ('AppBold', 10, 'bold')]:
            self.fonts[name] = font.Font(root=root, name=name, family='Microsoft YaHei UI', size=size, weight=weight)
        self.apply()
        root.bind('<Control-plus>', lambda e: self.change_zoom(1))
        root.bind('<Control-equal>', lambda e: self.change_zoom(1))
        root.bind('<Control-minus>', lambda e: self.change_zoom(-1))
        root.bind('<Control-0>', lambda e: self.set_zoom(100))
        root.bind('<F11>', self.toggle_fullscreen)
        root.bind('<Escape>', self.exit_fullscreen)
        root.bind_all('<MouseWheel>', self.wheel, add='+')

    def wheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
            return  # Preserve native result/editor scrolling.
        while widget is not None:
            if isinstance(widget, tk.Canvas):
                widget.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                return 'break'
            widget = getattr(widget, 'master', None)

    def apply(self):
        p = self.colors = PALETTES[self.theme]
        scale = self.zoom / 100
        for name, size in [('AppFont',10),('AppSmall',9),('AppTitle',21),('AppHeading',14),('AppBold',10)]:
            self.fonts[name].configure(size=max(8, round(size*scale)))
        s = self.style
        s.configure('.', background=p['bg'], foreground=p['text'], font='AppFont', bordercolor=p['border'],
                    lightcolor=p['border'], darkcolor=p['border'], troughcolor=p['field'], selectbackground=p['selected'], selectforeground=p['text'])
        s.configure('TFrame', background=p['bg'])
        s.configure('Card.TFrame', background=p['panel'])
        s.configure('TLabel', background=p['bg'], foreground=p['text'], font='AppFont')
        s.configure('Muted.TLabel', foreground=p['muted'], font='AppSmall')
        s.configure('Title.TLabel', foreground=p['text'], font='AppTitle')
        s.configure('Heading.TLabel', foreground=p['text'], font='AppHeading')
        s.configure('Accent.TLabel', foreground=p['accent'], font='AppBold')
        s.configure('TLabelframe', background=p['bg'], bordercolor=p['border'], relief='solid', borderwidth=1)
        s.configure('TLabelframe.Label', background=p['bg'], foreground=p['accent'], font='AppBold')
        s.configure('TButton', background=p['field'], foreground=p['text'], borderwidth=1, padding=(10,7), focusthickness=2, focuscolor=p['accent'])
        s.map('TButton', background=[('disabled',p['panel']),('pressed',p['selected']),('active',p['selected'])], foreground=[('disabled',p['muted'])])
        s.configure('Primary.TButton', background=p['accent'], foreground=p['accent_fg'], font='AppBold')
        s.map('Primary.TButton', background=[('disabled',p['field']),('active',p['violet']),('pressed',p['violet'])], foreground=[('disabled',p['muted']),('!disabled',p['accent_fg'])])
        s.configure('Danger.TButton', foreground=p['danger'])
        for name in ('TEntry', 'TCombobox'):
            s.configure(name, fieldbackground=p['field'], background=p['field'], foreground=p['text'], insertcolor=p['text'], padding=6, arrowsize=14)
            s.map(name, fieldbackground=[('disabled',p['panel']),('readonly',p['field'])], foreground=[('disabled',p['muted']),('readonly',p['text'])], selectbackground=[('!disabled',p['selected'])], selectforeground=[('!disabled',p['text'])])
        for name in ('TCheckbutton','TRadiobutton'):
            s.configure(name, background=p['bg'], foreground=p['text'], indicatorbackground=p['field'], indicatorforeground=p['accent'], padding=5)
            s.map(name, background=[('active',p['panel'])], foreground=[('disabled',p['muted'])], indicatorbackground=[('selected',p['accent']),('disabled',p['panel'])])
        s.configure('TNotebook', background=p['bg'], borderwidth=0, tabmargins=(8,6,8,0))
        s.configure('TNotebook.Tab', background=p['panel'], foreground=p['muted'], padding=(18,10), font='AppBold')
        s.map('TNotebook.Tab', background=[('selected',p['selected'])], foreground=[('selected',p['accent']),('disabled',p['muted'])])
        s.configure('Treeview', background=p['panel'], fieldbackground=p['panel'], foreground=p['text'], borderwidth=0,
                    rowheight=round(self.fonts['AppFont'].metrics('linespace')*1.8))
        s.configure('Treeview.Heading', background=p['field'], foreground=p['accent'], font='AppBold', padding=8)
        s.map('Treeview', background=[('selected',p['selected'])], foreground=[('selected',p['text'])])
        s.configure('Horizontal.TProgressbar', background=p['accent'], troughcolor=p['field'], borderwidth=0, thickness=5)
        for name in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
            s.configure(name, background=p['border'], troughcolor=p['bg'], arrowcolor=p['muted'], borderwidth=0, arrowsize=14)
        self.root.configure(background=p['bg'])
        for pattern, value in {'*Text.background':p['panel'], '*Text.foreground':p['text'], '*Text.insertBackground':p['accent'],
                               '*Text.selectBackground':p['selected'], '*Text.selectForeground':p['text'], '*Text.font':'AppFont',
                               '*Listbox.background':p['panel'], '*Listbox.foreground':p['text'], '*Listbox.selectBackground':p['selected'],
                               '*Listbox.selectForeground':p['accent'], '*Listbox.font':'AppFont', '*Canvas.background':p['bg'],
                               '*TCombobox*Listbox.background':p['field'], '*TCombobox*Listbox.foreground':p['text'],
                               '*TCombobox*Listbox.font':'AppFont', '*TEntry.font':'AppFont', '*TCombobox.font':'AppFont'}.items():
            self.root.option_add(pattern, value)
        self.paint_widgets(self.root)
        self.icons = {}
        live = []
        for widget, name in self.icon_widgets:
            if widget.winfo_exists():
                widget.configure(image=self.icon(name), compound='left')
                live.append((widget, name))
        self.icon_widgets = live
        self.root.iconphoto(True, self.icon('logo', 40))
        self.root.event_generate('<<DesignChanged>>', when='tail')

    def paint_widgets(self, widget):
        p = self.colors
        if isinstance(widget, tk.Text):
            widget.configure(background=p['panel'], foreground=p['text'], insertbackground=p['accent'],
                             selectbackground=p['selected'], selectforeground=p['text'], font='AppFont',
                             relief='flat', borderwidth=0, padx=12, pady=10, highlightthickness=1, highlightbackground=p['border'], highlightcolor=p['accent'])
        elif isinstance(widget, tk.Listbox):
            widget.configure(background=p['panel'], foreground=p['text'], selectbackground=p['selected'], selectforeground=p['accent'],
                             font='AppFont', relief='flat', borderwidth=0, highlightthickness=0)
        elif isinstance(widget, tk.Canvas):
            widget.configure(background=p['bg'])
        for child in widget.winfo_children():
            self.paint_widgets(child)

    def icon(self, name, size=18):
        size = max(14, round(size*self.zoom/100 * self.root.winfo_fpixels('1i')/96))
        key = name, size
        if key in self.icons:
            return self.icons[key]
        img = tk.PhotoImage(master=self.root, width=size, height=size)
        primary = name.endswith('_primary')
        name = name.removesuffix('_primary')
        color = self.colors['accent_fg'] if primary else self.colors['accent']
        def rect(x1,y1,x2,y2,c=color):
            img.put(c, to=(round(x1*size/20),round(y1*size/20),max(round(x1*size/20)+1,round(x2*size/20)),max(round(y1*size/20)+1,round(y2*size/20))))
        if name in ('logo','flow'):
            for x,y in ((2,2),(11,2),(2,11),(11,11)):
                rect(x,y,x+6,y+6, self.colors['violet'] if x==y else color)
        elif name=='database':
            for y in (3,8,13):
                rect(3,y,17,y+3)
            rect(3,3,5,17);rect(15,3,17,17)
        elif name=='api':
            for x in (3,15):
                rect(x,4,x+2,16)
            rect(3,4,8,6);rect(3,14,8,16);rect(12,4,17,6);rect(12,14,17,16)
        elif name in ('next','back'):
            rect(3,9,16,11)
            for n in range(5):
                x=12+n if name=='next' else 6-n
                rect(x,5+n,x+2,7+n);rect(x,13-n,x+2,15-n)
        else:
            rect(4,3,16,5);rect(4,15,16,17);rect(3,4,5,16);rect(15,4,17,16)
            rect(8,8,12,12,self.colors['violet'])
        self.icons[key] = img
        return img

    def decorate(self, widget, name):
        if widget.cget('style') == 'Primary.TButton':
            name += '_primary'
        widget.configure(image=self.icon(name), compound='left')
        self.icon_widgets.append((widget, name))

    def persist(self):
        try:
            save_preferences(self.path, {'theme':self.theme,'zoom':self.zoom})
        except OSError:
            if hasattr(self, 'hint'):
                self.hint.configure(text='外观已应用，但本次未能保存')

    def set_zoom(self, value):
        if value in ZOOMS:
            self.zoom=value
            self.apply()
            if hasattr(self,'zoom_var'):
                self.zoom_var.set(f'{value}%')
            self.persist()
        return 'break'

    def change_zoom(self, delta):
        return self.set_zoom(ZOOMS[max(0,min(len(ZOOMS)-1,ZOOMS.index(self.zoom)+delta))])

    def toggle_theme(self):
        self.theme = next(p for p in PALETTES if p != self.theme)
        self.apply()
        self.theme_button.configure(text='浅色模式' if self.theme=='曜石深色' else '深色模式')
        self.persist()

    def maximize(self):
        if self.fullscreen:
            self.exit_fullscreen()
        try:
            self.root.state('normal' if self.root.state()=='zoomed' else 'zoomed')
        except tk.TclError:
            self.root.attributes('-zoomed', not self.root.attributes('-zoomed'))

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes('-fullscreen', self.fullscreen)
        return 'break'

    def exit_fullscreen(self, event=None):
        if self.fullscreen:
            self.fullscreen=False
            self.root.attributes('-fullscreen',False)
            return 'break'

    def header(self, parent):
        bar=ttk.Frame(parent,padding=(18,12))
        bar.pack(fill='x')
        brand=ttk.Label(bar,text='  VIDEO OPS',style='Heading.TLabel')
        self.decorate(brand,'logo')
        brand.pack(side='left')
        self.hint=ttk.Label(bar,text='视频运维工作台',style='Muted.TLabel')
        self.hint.pack(side='left',padx=14)
        self.theme_button=ttk.Button(bar,text='浅色模式' if self.theme=='曜石深色' else '深色模式',command=self.toggle_theme)
        self.theme_button.pack(side='right')
        ttk.Button(bar,text='最大化',command=self.maximize).pack(side='right',padx=6)
        self.zoom_var=tk.StringVar(value=f'{self.zoom}%')
        zoom=ttk.Combobox(bar,textvariable=self.zoom_var,values=[f'{n}%' for n in ZOOMS],state='readonly',width=5)
        zoom.pack(side='right',padx=6)
        zoom.bind('<<ComboboxSelected>>',lambda e:self.set_zoom(int(self.zoom_var.get()[:-1])))
        ttk.Label(bar,text='缩放',style='Muted.TLabel').pack(side='right')
        line=tk.Frame(parent,height=2,bg=self.colors['accent'])
        line.pack(fill='x')
        def fit(event):
            if event.width < 1050*self.zoom/100:
                self.hint.pack_forget()
            elif not self.hint.winfo_manager():
                self.hint.pack(side='left',padx=14,after=brand)
        bar.bind('<Configure>',fit)
        return bar


class ScrollFrame(ttk.Frame):
    """Scrollable fallback for advanced forms and modal dialogs at large zoom."""
    def __init__(self, parent, padding=12):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        y=ttk.Scrollbar(self,command=self.canvas.yview)
        y.grid(row=0,column=1,sticky='ns')
        x=ttk.Scrollbar(self,orient='horizontal',command=self.canvas.xview)
        x.grid(row=1,column=0,sticky='ew')
        self.canvas.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.content=ttk.Frame(self.canvas,padding=padding)
        self.window=self.canvas.create_window((0,0),window=self.content,anchor='nw')
        self.content.bind('<Configure>',self.layout)
        self.canvas.bind('<Configure>',self.layout)
        self.pending=None
        self.bind('<Destroy>',self.cleanup,add='+')

    def layout(self,event=None):
        if self.pending is None:
            self.pending=self.after_idle(self.reflow)

    def reflow(self):
        self.pending=None
        self.canvas.itemconfigure(self.window,width=max(self.canvas.winfo_width(),self.content.winfo_reqwidth()),
                                  height=max(self.canvas.winfo_height(),self.content.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def cleanup(self,event):
        if event.widget is self and self.pending is not None:
            self.after_cancel(self.pending)
            self.pending=None
