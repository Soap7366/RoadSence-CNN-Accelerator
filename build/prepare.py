"""Windows first-use wizard. Credentials exist only in this process's memory."""
import ipaddress
import json
import queue
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src/stream'), str(ROOT/'src/desktop')]


def board_address(value):
    address = ipaddress.IPv4Address(value.strip())
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.packed[-1] in (0, 255):
        raise ValueError('请输入板卡实际使用的局域网 IPv4 地址，例如 192.168.2.99')
    return str(address)


def reachable(host, port=22):
    try:
        with socket.create_connection((host, port), 5):
            return True
    except OSError:
        return False


def register_host(host, secret):
    """Authenticate before persisting a first-use key; never replace a pinned key."""
    import paramiko
    from pscnn.runtime_config import load_config
    from build.remote_control import fingerprint
    cfg = load_config()
    path = Path(cfg['known_hosts'])
    client = paramiko.SSHClient()
    if path.exists():
        client.load_host_keys(str(path))
    pending = []

    class FirstUse(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            # Any existing key for this host prevents a different key algorithm
            # from silently bypassing the recorded identity.
            if client.get_host_keys().lookup(hostname):
                raise RuntimeError('板卡 SSH 指纹发生变化，请核对板卡身份；不会自动覆盖原指纹。')
            pending.append((hostname, key))

    client.set_missing_host_key_policy(FirstUse())
    try:
        client.connect(host, port=cfg['port'], username=cfg['username'], password=secret,
                       look_for_keys=False, allow_agent=False, timeout=8, banner_timeout=10, auth_timeout=10)
        if pending:
            hostname, key = pending[0]
            keys = client.get_host_keys()
            keys.add(hostname, key.get_name(), key)
            # Save through a sibling temporary file; a failed write keeps old pins.
            temporary = path.with_suffix('.pending')
            try:
                keys.save(str(temporary))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            print('首次连接已登记 SSH 指纹：' + fingerprint(key), flush=True)
        else:
            print('SSH 身份校验和密码验证通过。', flush=True)
    finally:
        client.close()


def prepare_board(host, secret, stage=None):
    from pscnn.runtime_config import load_config, save_config
    from build import remote_control
    from board_adapter import BoardAdapter
    stage=stage or (lambda text,timeout:print(text+'……',flush=True))
    stage('检查本地交付包',30)
    from sim.verify_package import check
    integrity=check()
    if integrity['status']!='PASS':
        raise RuntimeError('本地交付包不完整：'+'; '.join(integrity['errors'][:5]))
    cfg = load_config()
    cfg.pop('known_hosts', None)
    cfg['host'] = host
    save_config(cfg)
    stage('验证板卡身份和密码',65)
    for attempt in range(2):
        try:
            register_host(host, secret)
            break
        except (TimeoutError,ConnectionError):
            if attempt:raise
            print('连接暂时超时，正在重试一次……',flush=True)
            time.sleep(1)
    existing = load_config()['remote_root']
    if '/releases/release_' in existing:
        try:
            stage('检查已有板端部署',60)
            remote_control.board_check(SimpleNamespace(boot=False), password=secret)
        except remote_control.RemoteCommandError as exc:
            print('已有部署检查失败：'+str(exc),flush=True)
            stage('部署板端程序并构建验证',600)
            remote_control.deploy(SimpleNamespace(overlay=None), password=secret)
    else:
        stage('部署板端程序并构建验证',600)
        remote_control.deploy(SimpleNamespace(overlay=None), password=secret)
    stage('加载板端镜像',120)
    remote_control.board_check(SimpleNamespace(boot=True), password=secret)
    stage('验证真实图像推理',120)
    adapter = BoardAdapter()
    try:
        adapter.connect(host, secret)
        if not adapter.ready:
            raise RuntimeError('板端推理服务未就绪')
        import cv2
        video = ROOT/'.runtime/prepare_check.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 1, (64, 64))
        if not writer.isOpened():
            raise RuntimeError('无法生成验证视频')
        try:
            image = cv2.imread(str(next((ROOT/'data').rglob('*.jpg'))))
            if image is None:
                raise RuntimeError('验证图片无法读取')
            writer.write(cv2.resize(image, (64, 64)))
        finally:
            writer.release()
        adapter.process(str(video), 4, 'prepare_check')
        results=[value for kind,value in list(adapter.events.queue) if kind=='complete']
        if not results or len(results[-1]['samples'])!=1:
            raise RuntimeError('真实单帧推理没有返回完整结果')
        print('真实图像推理通过。',flush=True)
    finally:
        adapter.close()


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        from pscnn.runtime_config import load_config
        self.title('RoadSense · 一键准备')
        self.app_icon = tk.PhotoImage(file=str(ROOT/'build/roadsense.png'))
        self.iconphoto(True, self.app_icon)
        if sys.platform == 'win32':
            self.iconbitmap(str(ROOT/'build/roadsense.ico'))
        self.geometry('800x640')
        self.events = queue.Queue()
        self.busy = False
        self.cancelled=threading.Event()
        self.stage_text='等待开始'
        self.started=0
        self.host = tk.StringVar(value=load_config()['host'])
        self.secret = tk.StringVar()
        ttk.Label(self, text='板卡通电并用网线连接电脑；首次准备请保持电脑联网。').pack(pady=12)
        row = ttk.Frame(self); row.pack(fill='x', padx=20)
        ttk.Label(row, text='板卡 IP').grid(row=0, column=0, padx=5, pady=5)
        ttk.Entry(row, textvariable=self.host, width=24).grid(row=0, column=1)
        ttk.Label(row, text='板卡密码').grid(row=1, column=0, padx=5, pady=5)
        ttk.Entry(row, textvariable=self.secret, show='*', width=24).grid(row=1, column=1)
        ttk.Label(self, text='首次连接自动登记指纹，请确认网线连接的是你的板卡。密码不保存。').pack(pady=8)
        self.button = ttk.Button(self, text='开始准备', command=self.begin); self.button.pack(pady=5)
        self.cancel_button=ttk.Button(self,text='取消准备',command=self.cancel,state='disabled');self.cancel_button.pack(pady=3)
        self.status=ttk.Label(self,text='等待开始');self.status.pack(pady=4)
        self.progress=ttk.Progressbar(self,mode='indeterminate');self.progress.pack(fill='x',padx=20)
        self.restore = ttk.Button(self, text='恢复上次修改前的网卡设置', command=self.restore_network); self.restore.pack(pady=3)
        self.output = tk.Text(self, wrap='word', height=19); self.output.pack(fill='both', expand=True, padx=16, pady=12)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            self.cancel()
            self.closing=True
        else: self.destroy()

    def cancel(self):
        self.cancelled.set()
        self.status.configure(text='正在停止准备任务并关闭连接……')
        if hasattr(self,'selection_ready'):self.selection_ready.set()

    def elevate(self, action, index, address='', host=''):
        # Only validated numeric values reach PowerShell. The password never does.
        script = str(ROOT/'build/network.ps1').replace("'", "''")
        command = ("$p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList "
                   "@('-NoProfile','-ExecutionPolicy','Bypass','-File','\"" + script + "\"','-Action','" + action +
                   "','-InterfaceIndex','" + str(int(index)) + "'")
        if address: command += ",'-Address','" + str(ipaddress.IPv4Address(address)) + "','-Board','" + board_address(host) + "'"
        command += '); exit $p.ExitCode'
        import base64
        error = ROOT/'.runtime/network_error.txt'
        error.unlink(missing_ok=True)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode()], creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            detail = error.read_text(encoding='utf-8-sig') if error.exists() else '管理员授权可能被取消。'
            raise RuntimeError('网卡配置未完成：'+detail)

    def restore_network(self):
        try:
            states = list((ROOT/'.runtime').glob('network_*.json'))
            if not states: raise RuntimeError('没有需要恢复的网卡设置记录。')
            for state in states:
                self.elevate('restore', int(state.stem.split('_')[-1]))
                state.unlink()
            messagebox.showinfo('已恢复', '已恢复准备程序修改前的网卡设置。')
        except Exception as exc: messagebox.showerror('恢复未完成', str(exc))

    def ensure_network(self, host):
        from pscnn.runtime_config import load_config
        if reachable(host, load_config()['port']): return
        raw = subprocess.check_output(['powershell.exe', '-NoProfile', '-File', str(ROOT/'build/network.ps1'), '-Action', 'list'], creationflags=subprocess.CREATE_NO_WINDOW)
        adapters = json.loads(raw.decode('utf-8-sig'))
        if not adapters: raise RuntimeError('未发现已连接的有线网卡，请检查网线和板卡电源。')
        network=ipaddress.ip_network(host+'/24',strict=False)
        if any(ipaddress.IPv4Address(address) in network for item in adapters for address in item['addresses']):
            # A busy SSH port must not trigger unnecessary network reconfiguration.
            self.events.put(('log','有线网卡已在板卡网段，保留现有配置并重试 SSH。\n'))
            return
        self.selection = None
        self.selection_ready = threading.Event()
        self.events.put(('network', (host, adapters)))
        self.selection_ready.wait()
        if self.cancelled.is_set():raise RuntimeError('准备已取消。')
        if self.selection is None: raise RuntimeError('已取消网卡配置。')
        prefix = host.rsplit('.', 1)[0]
        used = {a for item in adapters for a in item['addresses']}
        candidates = list(range(100, 251))
        random.SystemRandom().shuffle(candidates)
        address = next(prefix+'.'+str(n) for n in candidates if prefix+'.'+str(n) != host and prefix+'.'+str(n) not in used)
        print('配置所选有线网卡：' + address + ' / 255.255.255.0；Windows 将请求管理员授权。', flush=True)
        self.elevate('apply', self.selection, address, host)
        if not reachable(host, load_config()['port']):
            raise RuntimeError('网卡已配置，但板卡 SSH 仍不可达。请核对板卡地址、电源和网线；可点击恢复网卡设置。')

    def select_network(self, value):
        host, adapters = value
        dialog = tk.Toplevel(self); dialog.title('选择连接板卡的网卡'); dialog.transient(self); dialog.grab_set()
        ttk.Label(dialog, text='无法连接 '+host+'。请选择直连板卡的有线网卡。\n将添加同网段 /24 地址，需要管理员授权；不修改 Wi-Fi。').pack(padx=20, pady=15)
        selected = tk.IntVar(value=adapters[0]['index'])
        for adapter in adapters:
            ttk.Radiobutton(dialog, variable=selected, value=adapter['index'], text=adapter['name']+' · '+adapter['description']).pack(anchor='w', padx=20, pady=4)
        def finish(accept):
            self.selection = selected.get() if accept else None
            dialog.destroy(); self.selection_ready.set()
        ttk.Button(dialog, text='配置并继续', command=lambda: finish(True)).pack(pady=10)
        dialog.protocol('WM_DELETE_WINDOW', lambda: finish(False))

    def begin(self):
        try:
            host = board_address(self.host.get())
            secret = self.secret.get()
            if not secret or '\n' in secret or '\r' in secret: raise ValueError('请输入有效的板卡密码。')
        except ValueError as exc:
            messagebox.showerror('输入有误', str(exc)); return
        self.secret.set('')
        self.output.delete('1.0','end')
        self.cancelled.clear()
        self.busy = True
        self.started=time.monotonic()
        self.stage_text='检查网络'
        self.cancel_button.configure(state='normal')
        self.progress.start(12)
        self.button.configure(state='disabled'); self.restore.configure(state='disabled')
        def worker():
            try:
                self.ensure_network(host)
                from task_runner import run_task
                import manage
                run_task([sys.executable,'-u','-B',str(ROOT/'build/prepare_worker.py')],
                         dict(host=host,password=secret),self.events,self.cancelled,
                         ROOT/'.runtime/prepare.log',env=manage.clean_env())
                self.events.put(('success', '准备完成，可以双击 RoadSense.exe 使用。'))
            except Exception as exc:
                # Do not print credentials even if a third-party exception includes them.
                self.events.put(('error', str(exc).replace(secret, '***')))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        for _ in range(200):
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind == 'network': self.select_network(value)
            elif kind == 'stage': self.stage_text=value
            elif kind == 'log': self.output.insert('end', value); self.output.see('end')
            else:
                self.busy = False
                self.cancel_button.configure(state='disabled')
                self.progress.stop()
                self.status.configure(text='准备完成' if kind=='success' else '准备已停止')
                self.button.configure(state='normal'); self.restore.configure(state='normal')
                self.output.insert('end', '\n'+value+'\n'); self.output.see('end')
                if getattr(self,'closing',False):self.destroy();return
                (messagebox.showinfo if kind == 'success' else messagebox.showerror)('一键准备', value)
        if self.busy and not self.cancelled.is_set():
            self.status.configure(text=f'{self.stage_text} · 已用时 {int(time.monotonic()-self.started)} 秒')
        self.after(100, self.poll)


if __name__ == '__main__':
    from process_lock import SingleInstance
    instance=SingleInstance(ROOT/'.runtime/prepare.lock')
    try:
        if instance.acquire():Wizard().mainloop()
        else:
            notice=tk.Tk();notice.withdraw()
            messagebox.showinfo('一键准备已打开','此文件夹的准备程序已经在运行，请使用已打开的窗口。')
            notice.destroy()
    finally:instance.close()
