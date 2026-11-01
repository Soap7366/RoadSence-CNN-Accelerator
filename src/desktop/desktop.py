"""Native Windows demonstration; no browser or web server required."""
import os
import bisect
import queue
from board_adapter import BoardAdapter
import colorsys
import math
import time
import sys
import threading
import json
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw

BG, PANEL, TEXT, MUTED, ACCENT = '#09090c', '#15151c', '#f4eef1', '#a39ba7', '#FAD2D3'
BUTTON = '#FAD2D3'
BUTTON_TEXT = '#33232b'
SYSTEM_FONT = 'Segoe UI'


def enable_high_dpi():
    """Call before Tk creates any windows; avoid Windows bitmap enlargement."""
    if sys.platform == 'win32':
        import ctypes
        try:
            # System-aware is intentional: Tk 8.6 does not rescale this custom
            # layout automatically when moving between different-DPI monitors.
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


class TaskProgress(tk.Canvas):
    """Explicit geometry keeps partial progress visible with the dark theme."""
    def __init__(self, parent):
        super().__init__(parent, height=8, bg=BG, highlightthickness=0)
        self.value = 0
        self.animation = None
        self.fill = self.create_rectangle(0, 0, 0, 8, fill=ACCENT, outline='')
        self.bind('<Configure>', lambda event: self.paint())

    def configure(self, cnf=None, **kw):
        value = kw.pop('value', None)
        if value is not None:
            self.value = max(0, min(100, float(value)))
        if cnf or kw:
            super().configure(cnf, **kw)
        if hasattr(self, 'fill'):
            self.paint()

    def start(self, rate=.003):
        self.rate = rate
        self.stop()
        self.configure(value=0)
        self.animate()

    def animate(self):
        # Activity indication only; readiness, not elapsed time, completes it.
        self.configure(value=self.value + (94-self.value)*self.rate)
        self.animation = self.after(100, self.animate)

    def stop(self):
        if self.animation is not None:
            self.after_cancel(self.animation)
            self.animation = None

    def paint(self):
        self.coords(self.fill, 0, 0, self.winfo_width()*self.value/100, self.winfo_height())


class VideoWorker:
    """Single owner of the decoder. Latest request/result only, bounded memory."""
    def __init__(self, capture):
        self.capture = capture
        self.condition = threading.Condition()
        self.request = None
        self.result = None
        self.generation = 0
        self.stopped = False
        self.next_frame = 1
        self.last_frame = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, index, size, seek=False):
        with self.condition:
            if seek:
                self.generation += 1
                self.result = None
            self.request = (index, size, self.generation, seek)
            self.condition.notify()

    def take(self):
        with self.condition:
            result, self.result = self.result, None
            return result

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopped or self.request is not None)
                    if self.stopped:
                        break
                    index, size, generation, seek = self.request
                    self.request = None
                if seek or index < self.next_frame - 1:
                    self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    self.next_frame = index
                frame = self.last_frame
                while self.next_frame <= index:
                    ok, frame = self.capture.read()
                    self.next_frame += 1
                    if not ok:
                        break
                if frame is None:
                    continue
                self.last_frame = frame
                h, w = frame.shape[:2]
                ratio = min(size[0]/w, size[1]/h)
                resized = cv2.resize(frame, (max(1, round(w*ratio)), max(1, round(h*ratio))),
                                     interpolation=cv2.INTER_LINEAR)
                image = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
                with self.condition:
                    if generation == self.generation:
                        self.result = (generation, index, frame, image)
        finally:
            self.capture.release()

    def close(self):
        with self.condition:
            self.stopped = True
            self.condition.notify()
        self.thread.join(timeout=2)


class RetainedCanvas(tk.Canvas):
    """Reuse items in stable draw order; coordinates stay in logical pixels."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = []
        self.cursor_index = 0
        self.item_states = {}

    def begin(self):
        self.cursor_index = 0

    def _create(self, kind, args, kw):
        scale = self.winfo_toplevel().dpi_scale
        coords = [v*scale for v in tk._flatten(args)]
        i = self.cursor_index
        self.cursor_index += 1
        if i >= len(self.items):
            item = super()._create(kind, coords, kw)
            self.items.append((kind, item))
        else:
            old_kind, item = self.items[i]
            if kind != old_kind:
                self.delete(item)
                item = super()._create(kind, coords, kw)
                self.items[i] = (kind, item)
            else:
                previous = self.item_states.get(item)
                if previous is None or previous[0] != coords:
                    self.coords(item, *coords)
                if previous is None or previous[1] != kw:
                    self.itemconfigure(item, **kw)
        self.item_states[item] = (coords, dict(kw))
        return item

    def end(self):
        for _, item in self.items[self.cursor_index:]:
            self.delete(item)
            self.item_states.pop(item, None)
        del self.items[self.cursor_index:]


class ProgressBar(tk.Canvas):
    """Thin two-tone seek track, driven by the existing position variable."""
    def __init__(self, parent, variable, command, scale):
        super().__init__(parent, bg=BG, height=24,
                         highlightthickness=0, cursor='hand2', takefocus=True)
        self.variable, self.command, self.scale = variable, command, scale
        self.maximum, self.disabled = 1, True
        self.track = self.create_line(0, 0, 0, 0, fill='#39313f', capstyle='round')
        self.elapsed = self.create_line(0, 0, 0, 0, fill='#ee9fac', capstyle='round')
        self.knob = self.create_oval(0, 0, 0, 0, fill=BUTTON, outline='')
        self.variable.trace_add('write', lambda *args: self.paint())
        self.bind('<Configure>', lambda event: self.paint())
        self.bind('<Button-1>', self.seek)
        self.bind('<B1-Motion>', self.seek)
        self.bind('<Left>', lambda event: self.nudge(-1))
        self.bind('<Right>', lambda event: self.nudge(1))

    def configure(self, cnf=None, **kw):
        if 'to' in kw:
            self.maximum = max(.001, float(kw.pop('to')))
        if 'state' in kw:
            self.disabled = kw.pop('state') == 'disabled'
        result = super().configure(cnf, **kw)
        self.paint()
        return result

    def paint(self):
        s = self.scale
        left, right, y = 6*s, max(6*s, self.winfo_width()-6*s), self.winfo_height()/2
        fraction = max(0, min(1, self.variable.get()/self.maximum))
        x, radius = left+(right-left)*fraction, 5*s
        self.coords(self.track, left, y, right, y)
        self.coords(self.elapsed, left, y, x, y)
        self.itemconfigure(self.track, width=3*s)
        self.itemconfigure(self.elapsed, width=3*s, state='normal' if fraction > 0 else 'hidden')
        self.coords(self.knob, x-radius, y-radius, x+radius, y+radius)
        self.itemconfigure(self.knob, fill='#514149' if self.disabled else BUTTON)

    def seek(self, event):
        if not self.disabled:
            self.focus_set()
            span = max(1, self.winfo_width()-12*self.scale)
            value = max(0, min(1, (event.x-6*self.scale)/span))*self.maximum
            self.variable.set(value)
            self.command(value)

    def nudge(self, delta):
        if not self.disabled:
            value = max(0, min(self.maximum, self.variable.get()+delta))
            self.variable.set(value)
            self.command(value)
        return 'break'


class RoundedButton(tk.Canvas):
    """Canvas button with rounded corners and keyboard activation."""
    def __init__(self, parent, text, command):
        self.command = command
        self.caption = text
        self.playback_icon = False
        self.button_state = 'normal'
        self.dpi_scale = parent.winfo_toplevel().dpi_scale
        super().__init__(parent, bg=parent['bg'], width=max(100, len(text)*15+30),
                         height=40, highlightthickness=0, takefocus=True, cursor='hand2')
        self.bind('<Configure>', lambda e: self.paint())
        self.bind('<Button-1>', self.activate)
        self.bind('<Return>', self.activate)
        self.bind('<Right>', lambda event: self.step_frame())
        self.bind('<space>', self.activate)
        self.bind('<FocusIn>', lambda e: self.paint())
        self.bind('<FocusOut>', lambda e: self.paint())

    def activate(self, event=None):
        if self.button_state != 'disabled':
            self.command()
        return 'break'

    def configure(self, cnf=None, **kw):
        if 'text' in kw:
            self.caption = kw.pop('text')
        if 'state' in kw:
            self.button_state = kw.pop('state')
        result = super().configure(cnf, **kw)
        self.paint()
        return result

    def paint(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2:
            w, h = int(self['width']), int(self['height'])
        r = 12 * self.dpi_scale
        color = '#514149' if self.button_state == 'disabled' else BUTTON
        border = '#fff0f1' if self.focus_get() == self else color
        if self.playback_icon:
            # Supersampling avoids jagged edges on the circular button.
            diameter = max(1, min(w, h))
            disk = Image.new('RGBA', (diameter*3, diameter*3), (0, 0, 0, 0))
            ImageDraw.Draw(disk).ellipse((2, 2, diameter*3-3, diameter*3-3),
                                         fill=color, outline=border, width=3)
            self.disk_photo = ImageTk.PhotoImage(disk.resize((diameter, diameter), Image.Resampling.LANCZOS))
            self.create_image(w/2, h/2, image=self.disk_photo)
        else:
            self.create_polygon(r, 1, w-r, 1, w-1, 1, w-1, r,
                                w-1, h-r, w-1, h-1, w-r, h-1,
                                r, h-1, 1, h-1, 1, h-r, 1, r, 1, 1,
                                smooth=True, splinesteps=24, fill=color, outline=border)
        ink = MUTED if self.button_state == 'disabled' else BUTTON_TEXT
        if self.playback_icon:
            # Geometry rather than font glyphs keeps both symbols crisp.
            unit = self.dpi_scale * .85
            cx, cy = w/2, h/2
            if self.caption == '暂停':
                for offset in (-6, 2):
                    self.create_rectangle(cx+offset*unit, cy-8*unit,
                                          cx+(offset+4)*unit, cy+8*unit,
                                          fill=ink, outline='')
            else:
                self.create_polygon(cx-5*unit, cy-9*unit, cx-5*unit, cy+9*unit,
                                    cx+9*unit, cy, fill=ink, outline='')
        else:
            self.create_text(w/2, h/2, text=self.caption, fill=ink,
                             font=(SYSTEM_FONT, 12))


def configure_tcl():
    # Use the active interpreter's Tcl/Tk installation or explicit environment.
    # A complete Python installation with Tk is required by the delivery.
    pass


class Demo(tk.Tk):
    def __init__(self):
        configure_tcl()
        enable_high_dpi()
        super().__init__()
        global SYSTEM_FONT
        SYSTEM_FONT = tkfont.nametofont('TkDefaultFont').actual('family')
        self.dpi_scale = self.winfo_fpixels('1i') / 96
        self.title('RoadSense')
        self.app_icon = tk.PhotoImage(file=str(Path(__file__).resolve().parents[2] / 'build/roadsense.png'))
        self.iconphoto(True, self.app_icon)
        if sys.platform == 'win32':
            self.iconbitmap(str(Path(__file__).resolve().parents[2] / 'build/roadsense.ico'))
        self.geometry('1280x850')
        self.minsize(960, 700)
        self.configure(bg=BG)
        self.option_add('*Font', (SYSTEM_FONT, 13))
        self.option_add('*TCombobox*Listbox.font', (SYSTEM_FONT, 13))
        self.board = BoardAdapter()
        self.busy = False
        self.inference_samples = []
        self.inference_frames = []
        self.video_path = None
        self.cap = None
        self.worker = None
        self.prepared = None
        self.video_key = None
        self.uploaded_key = None
        self.displayed_frames = 0
        self.last_request = None
        self.last_controls = 0
        self.cached_panel = None
        self.panel_key = None
        self.frame = None
        self.photo = None
        self.overlay_photo = None
        self.back_photo = None
        self.playing = False
        self.position = 0.0
        self.duration = 0.0
        self.fps = 30.0
        self.last = time.monotonic()
        self.demo_time = 0.0
        self.sample = None
        self.frame_data = {}
        self.session_id = uuid.uuid4().hex
        self.data_log = None
        self.display_index = 0
        self.pending_frame = None
        self.last_presented = 0
        self.pending_step = False
        self.playback_mode = tk.StringVar(value='流畅演示')
        self.mode = tk.StringVar(value='自动模拟')
        self.risk = tk.DoubleVar(value=0)
        self.angle = tk.DoubleVar(value=0)
        self.limit = tk.StringVar(value='90')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.scale_layout(self)
        self.set_initial_geometry()
        self.bind('<Escape>', lambda event: self.back())
        self.bind('<Right>', lambda event: self.step_frame())
        self.bind('<space>', lambda event: self.toggle() if self.dashboard.winfo_ismapped() else None)
        self.after(30, self.tick)

    def label(self, parent, text, size=12, color=TEXT, **kw):
        return tk.Label(parent, text=text, bg=parent['bg'], fg=color,
                        font=(SYSTEM_FONT, size, 'bold' if size >= 22 else 'normal'), **kw)

    def set_initial_geometry(self):
        """Fit the initial client area inside the desktop work area."""
        left, top = 0, 0
        right, bottom = self.winfo_screenwidth(), self.winfo_screenheight()
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
                left, top, right, bottom = area.left, area.top, area.right, area.bottom
        s = self.dpi_scale
        # Reserve space for the native title bar, borders and desktop margins.
        available_w = max(1, right-left-round(32*s))
        available_h = max(1, bottom-top-round(72*s))
        width = min(round(1280*s), available_w)
        height = min(round(740*s), available_h)
        self.minsize(min(round(960*s), width), min(round(600*s), height))
        x = left + (right-left-width)//2
        y = top + round(16*s)
        self.geometry(f'{width}x{height}+{x}+{y}')

    def scale_layout(self, parent):
        """Fonts use Tk points; scale only geometry specified in pixels."""
        s = self.dpi_scale
        for widget in parent.winfo_children():
            if isinstance(widget, (tk.Canvas, tk.Scale)):
                keys = ('width', 'height') if isinstance(widget, tk.Canvas) else ('length', 'width', 'sliderlength')
                for key in keys:
                    if key not in widget.keys():
                        continue
                    value = float(str(widget.cget(key)))
                    if value > 0:
                        widget.configure(**{key: round(value*s)})
            if isinstance(widget, tk.Frame):
                for key in ('padx', 'pady'):
                    widget.configure(**{key: round(float(str(widget.cget(key)))*s)})
            manager = widget.winfo_manager()
            if manager in ('pack', 'grid'):
                info = widget.pack_info() if manager == 'pack' else widget.grid_info()
                for key in ('padx', 'pady', 'ipadx', 'ipady'):
                    if key in info:
                        values = info[key] if isinstance(info[key], (tuple, list)) else self.tk.splitlist(str(info[key]))
                        scaled = tuple(round(float(v)*s) for v in values)
                        (widget.pack_configure if manager == 'pack' else widget.grid_configure)(**{key: scaled})
            self.scale_layout(widget)

    def button(self, parent, text, command):
        return RoundedButton(parent, text, command)

    def build(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=BG, background=PANEL, foreground=TEXT,
                        font=(SYSTEM_FONT, 13))
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)],
                  foreground=[('readonly', TEXT)], selectbackground=[('readonly', '#514149')],
                  selectforeground=[('readonly', TEXT)], arrowcolor=[('readonly', ACCENT)])
        self.option_add('*TCombobox*Listbox.background', PANEL)
        self.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.option_add('*TCombobox*Listbox.selectBackground', '#514149')
        self.option_add('*TCombobox*Listbox.selectForeground', TEXT)
        style.configure('Slim.Horizontal.TScale', background=BG,
                        troughcolor='#34303a', borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT, sliderrelief='flat',
                        sliderlength=round(10*self.dpi_scale),
                        sliderthickness=round(6*self.dpi_scale), gripcount=0)
        header = self.header = tk.Frame(self, bg=BG)
        header.pack(fill='x', padx=30, pady=20)
        self.label(header, 'RoadSense', 22).pack(side='left')
        self.connection_label = self.label(header, 'PYNQ-Z2 · 未连接', 10, ACCENT)
        # Connection status is displayed beside task progress.
        self.setup = tk.Frame(self, bg=BG)
        self.setup.pack(fill='both', expand=True, padx=50, pady=12)
        tagline = tk.Frame(header, bg=BG)
        tagline.pack(side='right')
        self.label(tagline, 'VISION INTO MOTION', 9, ACCENT).pack(anchor='e')
        self.label(tagline, '看见前方，预见风险。', 18).pack(anchor='e', pady=(3, 0))
        card = tk.Frame(self.setup, bg=PANEL, padx=22, pady=14)
        card.pack(fill='x', pady=8)
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill='x')
        self.label(row, '01 / 连接加速器', 12, ACCENT).pack(side='left', padx=(0, 18))
        from pscnn.runtime_config import load_config
        self.host = tk.StringVar(value=load_config()['host'])
        self.password = tk.StringVar()
        for title, variable, mask, width in [('板卡 IP', self.host, '', 16), ('密码', self.password, '*', 12)]:
            self.label(row, title, 10, MUTED).pack(side='left', padx=6)
            entry = tk.Entry(row, textvariable=variable, show=mask, width=width, bg=BG, fg=TEXT,
                             insertbackground=ACCENT, relief='flat')
            entry.pack(side='left', ipady=5)
        self.connect_button = self.button(row, '连接板子', self.connect_board)
        self.connect_button.pack(side='right')
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill='x', pady=(12, 0))
        self.label(row, '真实行驶录像', 12, ACCENT).pack(side='left')
        self.choose_button = self.button(row, '选择录像…', self.choose)
        self.choose_button.pack(side='right')
        self.choose_button.configure(state='disabled')
        self.file_label = self.label(card, '板卡初始化完成后，选择本地录像', 11, MUTED)
        self.file_label.pack(anchor='w', pady=6)
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill='x')
        self.process_button = self.button(row, '处理录像', self.process_video)
        self.process_button.pack(side='right')
        self.cancel_button = self.button(row, '取消任务', self.cancel_task)
        self.cancel_button.pack(side='right', padx=8)
        self.cancel_button.configure(state='disabled')
        self.process_button.configure(state='disabled')
        self.progress_text = self.label(card, '等待连接', 10, MUTED)
        self.progress_text.pack(anchor='w', pady=(8, 0))
        style.configure('Demo.Horizontal.TProgressbar', background=ACCENT, troughcolor=BG)
        self.progress = TaskProgress(card)
        self.progress.pack(fill='x', pady=5)
        self.start_button = self.button(self.setup, '开始播放展示 ↗', self.enter)
        self.start_button.pack(anchor='w', pady=8)
        self.start_button.configure(state='disabled')
        metrics = tk.Frame(self.setup, bg=PANEL, padx=22, pady=12)
        metrics.pack(fill='x', pady=8)
        self.metrics_open = False
        self.metrics_toggle = self.button(metrics, '性能详情  ▸', self.toggle_metrics)
        self.metrics_toggle.pack(anchor='w')
        self.metrics_view = tk.Frame(metrics, bg=PANEL)
        self.metrics_canvas = tk.Canvas(self.metrics_view, bg=PANEL, height=230, highlightthickness=0)
        style.configure('Demo.Vertical.TScrollbar', background='#514149', troughcolor=BG,
                        arrowcolor=ACCENT, bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        style.map('Demo.Vertical.TScrollbar', background=[('active', '#b58e98'), ('pressed', ACCENT)])
        metrics_scroll = ttk.Scrollbar(self.metrics_view, orient='vertical',
                                      style='Demo.Vertical.TScrollbar', command=self.metrics_canvas.yview)
        metrics_scroll.pack(side='right', fill='y')
        self.metrics_canvas.pack(side='left', fill='both', expand=True)
        self.metrics_canvas.configure(yscrollcommand=metrics_scroll.set)
        self.metrics_body = tk.Frame(self.metrics_canvas, bg=PANEL)
        metrics_window = self.metrics_canvas.create_window(0, 0, window=self.metrics_body, anchor='nw')
        self.metrics_body.bind('<Configure>', lambda event: self.metrics_canvas.configure(scrollregion=self.metrics_canvas.bbox('all')))
        self.metrics_canvas.bind('<Configure>', lambda event: self.metrics_canvas.itemconfigure(metrics_window, width=event.width))
        self.bind('<MouseWheel>', self.scroll_metrics, add='+')
        self.metric_values = {}
        self.metrics_body.columnconfigure(0, minsize=round(520*self.dpi_scale))
        self.metrics_body.columnconfigure(1, weight=1)
        for column, title in enumerate(['项目', '耗时 / 速率']):
            self.label(self.metrics_body, title, 11, ACCENT, anchor='w').grid(row=0, column=column, sticky='ew', padx=12, pady=7)
        pl_background = '#382832'
        pl_highlight = tk.Frame(self.metrics_body, bg=pl_background)
        pl_highlight.grid(row=4, column=0, columnspan=2, sticky='nsew', pady=3)
        pl_highlight.lower()
        tk.Frame(pl_highlight, bg=ACCENT, width=3).pack(side='left', fill='y')
        for number, (key, title) in enumerate([('initialization', '板端初始化'),
                ('transfer', '数据传输 / 每张'), ('preprocess', 'PS 预处理 / 每张'),
                ('pl', 'PL 计算耗时（含检测开销）')], 1):
            title_label = self.label(self.metrics_body, title, 11, MUTED)
            title_label.grid(row=number, column=0, sticky='w', padx=12, pady=5)
            value = self.label(self.metrics_body, '待测量', 12, ACCENT if key == 'pl' else TEXT)
            value.grid(row=number, column=1, sticky='w', padx=12, pady=5)
            if key == 'pl':
                title_label.configure(bg=pl_background, fg=ACCENT, font=(SYSTEM_FONT, 11, 'bold'))
                value.configure(bg=pl_background, font=(SYSTEM_FONT, 13, 'bold'))
                title_label.grid_configure(pady=12)
                value.grid_configure(pady=12)
            self.metric_values[key] = value
        self.metric_values['pl'].configure(text='待实测')
        self.label(self.metrics_body, '传输：PS接收JPEG正文的计时与有效速率，包含SSH缓冲影响。', 10, MUTED).grid(row=5, column=0, columnspan=2, sticky='w', pady=(6, 0))

        self.dashboard = tk.Frame(self, bg=BG)
        workspace = tk.Frame(self.dashboard, bg=BG)
        workspace.pack(fill='both', expand=True)
        self.canvas = RetainedCanvas(workspace, bg='#0d0d13', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.tag_bind('back', '<Button-1>', lambda event: self.back())
        self.canvas.tag_bind('back', '<Enter>', lambda event: self.canvas.configure(cursor='hand2'))
        self.canvas.tag_bind('back', '<Leave>', lambda event: self.canvas.configure(cursor=''))
        self.meter = RetainedCanvas(workspace, width=175, bg=PANEL, highlightthickness=0)
        self.meter.pack(side='right', fill='y')
        playback = tk.Frame(self.dashboard, bg=BG)
        playback.pack(fill='x', pady=10)
        self.play_button = self.button(playback, '播放 / 暂停', self.toggle)
        self.play_button.playback_icon = True
        self.play_button.configure(width=34, height=34)
        self.play_button.pack(side='left', padx=(12, 0), anchor='center')
        self.seek_position = tk.DoubleVar(value=0)
        self.seek = ProgressBar(playback, variable=self.seek_position,
                                command=self.seek_video, scale=self.dpi_scale)
        self.seek.pack(side='left', fill='x', expand=True, padx=(16, 20), anchor='center')
        self.clock_label = self.label(playback, '00:00 / 00:00', 10, MUTED)
        self.clock_label.pack(side='right', padx=(0, 18))
        self.play_button.configure(text='播放', state='disabled')
        self.seek.configure(state='disabled')
        controls = tk.Frame(self.setup, bg=PANEL, padx=18, pady=15)
        # Legacy simulation controls remain available to integration checks.
        self.label(controls, '演示驱动', 11, ACCENT).grid(row=0, column=0, padx=10)
        combo = ttk.Combobox(controls, textvariable=self.mode, values=['自动模拟', '手动调节', '外部数据（预留）'], state='readonly', width=18)
        combo.grid(row=1, column=0, padx=10)
        combo.bind('<<ComboboxSelected>>', self.change_mode)
        self.sliders = []
        for col, title, var, low, high, resolution in [(1, '风险值', self.risk, 0, 1, .01), (2, '转向角 / °', self.angle, -90, 90, .1)]:
            self.label(controls, title, 10, MUTED).grid(row=0, column=col)
            scale = tk.Scale(controls, variable=var, from_=low, to=high, resolution=resolution,
                             orient='horizontal', bg=PANEL, fg=TEXT, highlightthickness=0,
                             troughcolor=BG, activebackground=ACCENT, length=220)
            scale.grid(row=1, column=col, sticky='ew', padx=18)
            self.sliders.append(scale)
            controls.columnconfigure(col, weight=1)
        self.label(controls, '角度范围 / ±°', 10, MUTED).grid(row=0, column=3)
        ranges = ttk.Combobox(controls, textvariable=self.limit, values=['30', '45', '90'], state='readonly', width=6)
        ranges.grid(row=1, column=3, padx=10)
        ranges.bind('<<ComboboxSelected>>', self.change_range)
        mode_row = tk.Frame(self.setup, bg=PANEL)
        # Playback layout remains unchanged.
        self.label(mode_row, '播放策略', 11).pack(side='left', padx=12)
        ttk.Combobox(mode_row, textvariable=self.playback_mode, values=['流畅演示', '逐帧检查'],
                     state='readonly', width=14).pack(side='left')
        self.label(mode_row, '逐帧检查：暂停后按 → 查看下一帧', 10, MUTED).pack(side='left', padx=15)
        self.status = self.label(self.dashboard, '', 10, MUTED, anchor='w')
        self.change_mode()

    def scroll_metrics(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.metrics_view:
                self.metrics_canvas.yview_scroll(-int(event.delta/120), 'units')
                return 'break'
            widget = getattr(widget, 'master', None)

    def begin_stage(self, text, rate=.003):
        self.stage_after = None
        self.progress.start(rate)
        self.progress_text.configure(text=text)

    def finish_stage(self, next_text):
        self.progress.stop()
        self.progress.configure(value=100)
        self.progress_text.configure(text=next_text)
        if next_text:
            self.stage_after = self.after(250, lambda: self.begin_stage(next_text) if self.busy else None)

    def toggle_metrics(self):
        self.metrics_open = not self.metrics_open
        self.metrics_toggle.configure(text='性能详情  ▾' if self.metrics_open else '性能详情  ▸')
        if self.metrics_open:
            self.metrics_view.pack(fill='x', pady=(8, 0))
        else:
            self.metrics_view.pack_forget()

    def cancel_stage_timer(self):
        pending = getattr(self, 'stage_after', None)
        if pending is not None:
            self.after_cancel(pending)
            self.stage_after = None

    def cancel_task(self):
        self.cancel_stage_timer()
        self.progress.stop()
        self.board.close()
        self.board = BoardAdapter()
        self.busy = False
        self.connection_label.configure(text='PYNQ-Z2 · 已取消，请重新连接')
        self.progress_text.configure(text='任务已取消，已关闭连接')
        self.connect_button.configure(state='normal')
        self.choose_button.configure(state='disabled')
        self.process_button.configure(state='disabled')
        self.cancel_button.configure(state='disabled')

    def connect_board(self):
        if self.busy:
            return
        if not self.host.get().strip() or not self.password.get():
            messagebox.showinfo('连接板子', '请填写板卡 IP 和密码')
            return
        self.progress.stop()
        self.board.close()
        self.board = BoardAdapter()
        self.busy = True
        self.cancel_button.configure(state='normal')
        self.connect_button.configure(state='disabled')
        self.choose_button.configure(state='disabled')
        self.process_button.configure(state='disabled')
        self.connection_label.configure(text='PYNQ-Z2 · 正在连接')
        self.progress.start()
        self.progress_text.configure(text='正在连接并初始化板端…')
        secret = self.password.get()
        self.password.set('')
        self.board.launch(self.board.connect, self.host.get().strip(), secret)

    def process_video(self):
        if self.busy or not self.board.ready or not self.video_path:
            return
        stride = 4
        self.busy = True
        self.cancel_button.configure(state='normal')
        self.inference_samples = []
        self.inference_frames = []
        self.frame_data.clear()
        self.start_button.configure(state='disabled')
        self.choose_button.configure(state='disabled')
        self.process_button.configure(state='disabled')
        self.connect_button.configure(state='disabled')
        self.begin_stage('正在解码并统计采样帧…', rate=.0008)
        for key in ('transfer', 'preprocess', 'pl'):
            self.metric_values[key].configure(text='正在测量…')
        self.board.launch(self.board.process, self.video_path, stride, self.session_id)

    def poll_board(self):
        for _ in range(100):
            try:
                kind, value = self.board.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'connected':
                self.finish_stage('连接完成，正在初始化板端…')
            elif kind == 'status':
                self.progress_text.configure(text=value)
            elif kind == 'ready':
                self.cancel_stage_timer()
                self.progress.stop()
                self.progress.configure(value=100)
                self.busy = False
                self.cancel_button.configure(state='disabled')
                self.connection_label.configure(text='PYNQ-Z2 · 已就绪')
                self.progress_text.configure(text=f"连接就绪 · {value['connection_s']:.2f} s · 请选择录像")
                self.metric_values['initialization'].configure(text=f"{value['initialization_s']:.3f} s")
                self.choose_button.configure(state='normal')
                self.connect_button.configure(state='normal', text='重新连接')
                if self.video_path:
                    self.process_button.configure(state='normal')
            elif kind == 'scan_complete':
                self.progress.stop()
                self.progress.configure(value=100)
                self.progress_text.configure(text='扫描完成，开始逐图推理…')
            elif kind == 'scan':
                self.progress_text.configure(text=f'正在读取视频 · 已扫描 {value} 帧')
            elif kind == 'progress':
                done, total = value
                self.progress.stop()
                self.progress.configure(value=96*done/total)
                self.progress_text.configure(text=f'正在推理 · {done/total:.0%}')
            elif kind == 'complete':
                self.busy = False
                self.cancel_button.configure(state='disabled')
                if value['session_id'] != self.session_id:
                    continue
                self.inference_samples = value['samples']
                self.inference_frames = [r['video_frame_id'] for r in self.inference_samples]
                self.mode.set('外部数据（预留）')
                self.playback_mode.set('流畅演示')
                count = len(self.inference_samples)
                timings = [r['result'].get('timing', {}) for r in self.inference_samples]
                native_reports = [r['result'].get('native', {}) for r in self.inference_samples]
                measured = [r.get('start_wait_ns') for r in native_reports]
                if all(isinstance(t, (int, float)) and not isinstance(t, bool)
                       and math.isfinite(t) and t > 0 for t in measured):
                    self.metric_values['pl'].configure(text=f'{sum(measured)/count/1e6:.3f} ms / 张')
                else:
                    self.metric_values['pl'].configure(text='板端未返回有效实测计时')
                if all('body_read_ns' in t for t in timings):
                    receive_ns = sum(t['body_read_ns'] for t in timings)
                    size = sum(r['source_bytes'] for r in self.inference_samples)
                    speed = size/(receive_ns/1e9)/1e6 if receive_ns > 0 else 0
                    self.metric_values['transfer'].configure(text=f'{receive_ns/count/1e6:.2f} ms · {speed:.2f} MB/s')
                else:
                    self.metric_values['transfer'].configure(text='板端未返回计时')
                if all('preprocess_ns' in t for t in timings):
                    prep = sum(t['preprocess_ns'] for t in timings)/count/1e6
                    self.metric_values['preprocess'].configure(text=f'{prep:.2f} ms')
                else:
                    self.metric_values['preprocess'].configure(text='板端未返回计时')
                self.progress_text.configure(text=f"处理完成 · {count} 张采样 / {value['total']} 视频帧 · 总用时 {value['elapsed']:.1f} s")
                self.progress.configure(value=100)
                self.start_button.configure(state='normal')
                self.choose_button.configure(state='normal')
                self.process_button.configure(state='normal')
                self.connect_button.configure(state='normal')
            elif kind == 'error':
                self.cancel_stage_timer()
                self.progress.stop()
                self.busy = False
                self.cancel_button.configure(state='disabled')
                self.connection_label.configure(text='PYNQ-Z2 · 连接或处理失败')
                self.progress_text.configure(text='失败：' + value)
                self.connect_button.configure(state='normal', text='重新连接')
                messagebox.showerror('板端任务失败', value)

    def held_sample(self, index):
        position = bisect.bisect_right(self.inference_frames, index)-1
        return self.inference_samples[position] if position >= 0 else None

    def choose(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title='选择行驶录像', filetypes=[('录像文件', '*.mp4 *.avi *.mov *.mkv *.webm *.m4v'), ('所有文件', '*.*')])
        if not path:
            return
        capture = cv2.VideoCapture(path)
        ok, frame = capture.read()
        if not ok:
            capture.release()
            messagebox.showerror('无法打开视频', '无法解码此文件，请尝试 MP4（H.264）或其他视频。')
            return
        if self.worker:
            self.worker.close()
        self.video_path = path
        self.inference_samples = []
        self.inference_frames = []
        self.start_button.configure(state='disabled')
        self.process_button.configure(state='normal' if self.board.ready else 'disabled')
        self.progress.configure(value=0)
        self.progress_text.configure(text='录像已选择，点击处理录像')
        self.cap, self.frame = capture, frame
        fps = capture.get(cv2.CAP_PROP_FPS)
        self.fps = fps if math.isfinite(fps) and fps > 0 else 30
        self.duration = max(0, capture.get(cv2.CAP_PROP_FRAME_COUNT) / self.fps)
        if self.data_log:
            self.data_log.close()
            self.data_log = None
        self.session_id = uuid.uuid4().hex
        self.frame_data.clear()
        self.display_index = 0
        self.pending_frame = None
        self.worker = VideoWorker(capture)
        self.last_request = None
        self.prepared = None
        self.video_key = None
        self.uploaded_key = None
        self.position = 0
        self.playing = False
        self.seek.configure(to=max(self.duration, 1))
        self.seek.configure(state='normal')
        self.play_button.configure(state='normal', text='播放')
        self.file_label.configure(text=path)

    def enter(self):
        self.header.pack_forget()
        self.setup.pack_forget()
        self.dashboard.pack(fill='both', expand=True)
        self.playing = self.cap is not None
        self.last = time.monotonic()

    def back(self):
        self.playing = False
        self.dashboard.pack_forget()
        self.header.pack(fill='x', padx=round(30*self.dpi_scale), pady=round(20*self.dpi_scale))
        self.setup.pack(fill='both', expand=True, padx=round(50*self.dpi_scale), pady=round(30*self.dpi_scale))

    def toggle(self):
        if self.cap:
            if self.position >= self.duration:
                self.position = 0
                self.request_frame(seek=True)
            self.playing = not self.playing

    def seek_video(self, value):
        if self.worker:
            self.position = float(value)
            self.pending_frame = None
            self.request_frame(seek=True)

    def request_frame(self, seek=False):
        size = (max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        key = (min(max(0, int(self.duration*self.fps)-1), int(self.position*self.fps)), size)
        if self.worker and (seek or key != self.last_request):
            self.worker.submit(*key, seek=seek)
            self.last_request = key

    def change_mode(self, event=None):
        for slider in self.sliders:
            slider.configure(state='normal' if self.mode.get() == '手动调节' else 'disabled')
        self.sample = None

    def change_range(self, event=None):
        limit = float(self.limit.get())
        self.sliders[1].configure(from_=-limit, to=limit)
        self.angle.set(max(-limit, min(limit, self.angle.get())))

    def step_frame(self):
        if self.worker and self.playback_mode.get() == '逐帧检查' and not self.playing:
            self.pending_step = True
            self.position = min(max(0, self.duration-1/self.fps), (self.display_index+1)/self.fps)
            self.request_frame()

    def update_data(self, risk, steering_deg, frame_id=None, session_id=None):
        """Main-thread adapter: zero-based frame_id and current session_id required."""
        if session_id != self.session_id or isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
            return False
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (risk, steering_deg)):
            return False
        if not 0 <= risk <= 1 or abs(steering_deg) > float(self.limit.get()):
            return False
        record = dict(session_id=self.session_id, frame_id=frame_id, risk=risk,
                      steering_deg=steering_deg, received_at=time.time())
        if self.data_log is None:
            folder = Path(__file__).parent / 'records'
            folder.mkdir(exist_ok=True)
            self.data_log = (folder / (self.session_id+'.jsonl')).open('a', encoding='utf-8', buffering=1)
        self.data_log.write(json.dumps(record, ensure_ascii=False)+'\n')
        self.frame_data[frame_id] = (risk, steering_deg)
        return True

    @staticmethod
    def clock(seconds):
        return f'{int(seconds) // 60:02d}:{int(seconds) % 60:02d}'

    def tick(self):
        self.poll_board()
        now = time.monotonic()
        dt = min(now - self.last, .2)
        self.last = now
        if self.dashboard.winfo_ismapped():
            if self.cap and self.playing:
                if self.playback_mode.get() == '流畅演示':
                    self.position = min(self.duration, self.position + dt)
                elif self.pending_frame is None and now-self.last_presented >= 1/self.fps:
                    self.position = min(self.duration, (self.display_index+1)/self.fps) if self.video_key is not None else 0
                self.request_frame()
                if self.position >= self.duration:
                    self.playing = False
            elif not self.cap:
                self.demo_time += dt
            if self.worker:
                self.request_frame()
                result = self.worker.take()
                if result:
                    self.pending_frame = result
                if self.pending_frame:
                    generation, index, frame, prepared = self.pending_frame
                    matched = self.mode.get() != '外部数据（预留）' or index in self.frame_data or self.held_sample(index) is not None
                    if self.playback_mode.get() == '流畅演示' or matched:
                        self.frame, self.prepared = frame, prepared
                        self.display_index = index
                        self.video_key = (generation, index, prepared.size)
                        self.last_presented = now
                        self.pending_frame = None
                        self.pending_step = False
            if now-self.last_controls >= .1:
                self.seek_position.set(self.position)
                self.clock_label.configure(text=f'{self.clock(self.position)} / {self.clock(self.duration)}')
                caption = '暂停' if self.playing else '播放'
                if self.play_button.caption != caption:
                    self.play_button.configure(text=caption)
                self.last_controls = now
            limit = float(self.limit.get())
            valid = True
            if self.mode.get() == '自动模拟':
                t = self.display_index/self.fps if self.cap else self.demo_time
                risk = .05 + .9 * (math.sin(t * .43 - 1.3) + 1) / 2
                angle = math.sin(t * .31) * limit * .72
                status = '● 自动模拟 · 非板端推理'
            elif self.mode.get() == '手动调节':
                risk, angle = self.risk.get(), self.angle.get()
                status = '● 手动演示 · 非板端推理'
            elif self.held_sample(self.display_index) is not None:
                sample = self.held_sample(self.display_index)
                risk = sample['risk']
                angle = max(-limit, min(limit, sample['steering_deg']))
                status = f'● 板端推理 · 当前帧 {self.display_index} / 采样帧 {sample["video_frame_id"]}（从0计） · 每4帧检测1帧 · 标签角度显示'
            elif self.display_index in self.frame_data:
                risk, angle = self.frame_data[self.display_index]
                valid = abs(angle) <= limit
                status = f'● 外部数据 · 帧 {self.display_index}'
            else:
                risk, angle, valid = 0, 0, False
                status = '○ 当前帧等待数据 · 网口通信尚未实现'
            self.draw(risk, angle, limit, valid)
            self.status.configure(text=status + '     |     0° 朝前 · 左正 / 右负     |     视频静音播放')
        elapsed = time.monotonic()-now
        self.after(max(1, round(1000*(1/60-elapsed))), self.tick)

    def draw(self, risk, angle, limit, valid):
        c = self.canvas
        c.begin()
        s = self.dpi_scale
        w, h = c.winfo_width() / s, c.winfo_height() / s
        if self.frame is not None:
            if self.prepared is None:
                fh, fw = self.frame.shape[:2]
                ratio = min(w*s/fw, h*s/fh)
                resized = cv2.resize(self.frame, (max(1, round(fw*ratio)), max(1, round(fh*ratio))))
                self.prepared = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
                self.video_key = ('preview', self.prepared.size)
            if getattr(self, 'uploaded_key', None) != self.video_key:
                if self.photo is not None and (self.photo.width(), self.photo.height()) == self.prepared.size:
                    self.photo.paste(self.prepared)
                else:
                    self.photo = ImageTk.PhotoImage(self.prepared)
                self.uploaded_key = self.video_key
                self.displayed_frames += 1
            c.create_image(w / 2, h / 2, image=self.photo)
        else:
            for x in range(0, int(w), 60):
                c.create_line(w / 2, h * .4, x, h, fill='#29232e')
            for y in range(int(h * .55), int(h), 45):
                c.create_line(0, y, w, y, fill='#29232e')
            c.create_text(w / 2, h * .26, text='等待行驶画面', fill=TEXT, font=(SYSTEM_FONT, 22, 'bold'))
            c.create_text(w / 2, h * .35, text='仪表预览 · 返回后可选择录像', fill=MUTED, font=(SYSTEM_FONT, 13))
        radius = min(100, w * .20)
        cx, cy = radius + 40, h - 55
        # RGBA image keeps the video visible beneath the instrument panel.
        panel_w, panel_h = int(2 * radius + 50), int(radius + 88)
        aa = 3
        factor = s * aa
        panel_key = (panel_w, panel_h, s, round(angle*2)/2, limit, valid)
        if panel_key != self.panel_key:
            def box(values):
                return tuple(round(v*factor) for v in values)
            base_key = (panel_w, panel_h, s)
            if getattr(self, 'base_key', None) != base_key:
                panel = Image.new('RGBA', (round(panel_w*factor), round(panel_h*factor)), (0, 0, 0, 0))
                d = ImageDraw.Draw(panel)
                d.rounded_rectangle((0, 0, panel.width-1, panel.height-1), radius=round(12*factor),
                                    fill=(15, 12, 19, 180), outline=(150, 112, 134, 180), width=max(1, round(factor)))
                px, py = radius+25, radius+48
                d.arc(box((px-radius, py-radius, px+radius, py+radius)), 180, 360,
                      fill='#86707e', width=round(5*factor))
                for deg in [-90, -45, 0, 45, 90]:
                    rad = math.radians(deg)
                    d.line(box((px+radius*math.sin(rad), py-radius*math.cos(rad),
                                px+(radius-10)*math.sin(rad), py-(radius-10)*math.cos(rad))),
                           fill=MUTED, width=round(2*factor))
                self.panel_base = panel
                self.base_key = base_key
            panel = self.panel_base.copy()
            d = ImageDraw.Draw(panel)
            px, py = radius+25, radius+48
            if valid:
                rad = math.radians(angle / limit * 90)
                vx, vy = -math.sin(rad), -math.cos(rad)
                tx, ty = px+(radius-13)*vx, py+(radius-13)*vy
                d.line(box((px, py, tx-12*vx, ty-12*vy)), fill=ACCENT, width=round(5*factor))
                d.polygon([tuple(box(point)) for point in [(tx,ty), (tx-15*vx+6*vy,ty-15*vy-6*vx),
                                                          (tx-15*vx-6*vy,ty-15*vy+6*vx)]], fill=ACCENT)
                d.ellipse(box((px-5,py-5,px+5,py+5)), fill=ACCENT)
            small = cv2.resize(np.asarray(panel), (round(panel_w*s), round(panel_h*s)),
                               interpolation=cv2.INTER_AREA)
            self.overlay_photo = ImageTk.PhotoImage(Image.fromarray(small))
            self.panel_key = panel_key
        c.create_image(cx-radius-25, cy-radius-48, image=self.overlay_photo, anchor='nw')
        c.create_text(cx, cy - radius - 28, text='建议转向', fill=MUTED, font=(SYSTEM_FONT, 12))
        for x, y, text in [(cx-radius, cy+15, f'+{limit:g}°'), (cx, cy-radius-10, '0°'), (cx+radius, cy+15, f'−{limit:g}°')]:
            c.create_text(x, y, text=text, fill=MUTED, font=(SYSTEM_FONT, 9))
        c.create_text(cx, cy+24, text=f'{angle:+.1f}°' if valid else '无有效数据', fill=TEXT,
                      font=(SYSTEM_FONT, 16) if valid else (SYSTEM_FONT, 18, 'bold'))
        # Draw directly on the video canvas so rounded corners reveal the video,
        # instead of the opaque rectangular background of a child widget.
        if self.back_photo is None:
            factor = s * 3
            button = Image.new('RGBA', (round(92*factor), round(38*factor)), (0, 0, 0, 0))
            painter = ImageDraw.Draw(button)
            painter.rounded_rectangle((0, 0, button.width-1, button.height-1),
                                      radius=round(12*factor), fill=BUTTON)
            self.back_photo = ImageTk.PhotoImage(button.resize((round(92*s), round(38*s)), Image.Resampling.LANCZOS))
        c.create_image(14, 14, image=self.back_photo, anchor='nw', tags='back')
        c.create_text(60, 33, text='← 返回', fill=BUTTON_TEXT,
                      font=(SYSTEM_FONT, 12), tags='back')
        m = self.meter
        m.begin()
        mh = m.winfo_height() / s
        color = '#%02x%02x%02x' % tuple(int(v*255) for v in colorsys.hls_to_rgb(140*(1-risk)/360, .56, .72)) if valid else MUTED
        m.create_text(88, 30, text='COLLISION RISK', fill=MUTED, font=(SYSTEM_FONT, 10))
        m.create_text(88, 60, text='碰撞风险', fill=TEXT, font=(SYSTEM_FONT, 16, 'bold'))
        m.create_text(88, 108, text=f'{risk:.2f}' if valid else '—', fill=color, font=(SYSTEM_FONT, 36))
        level = ('低风险' if risk < .35 else '注意' if risk < .65 else '高风险' if risk < .85 else '极高风险') if valid else '无有效数据'
        m.create_text(88, 150, text=level, fill=color, font=(SYSTEM_FONT, 13))
        top, bottom = 185, max(220, mh-45)
        m.create_rectangle(85, top, 125, bottom, fill='#24212b', outline='#39313f')
        m.create_rectangle(86, bottom-risk*(bottom-top), 124, bottom, fill=color, outline='',
                           state='normal' if valid and risk > 0 else 'hidden')
        for fraction in [0, .25, .5, .75, 1]:
            m.create_text(65, bottom-fraction*(bottom-top), text=f'{fraction:g}', fill=MUTED, anchor='e', font=(SYSTEM_FONT, 9))

        c.end()
        m.end()

    def close(self):
        self.cancel_stage_timer()
        self.progress.stop()
        self.board.close()
        if self.worker:
            self.worker.close()
        if self.data_log:
            self.data_log.close()
        self.destroy()


if __name__ == '__main__':
    Demo().mainloop()
