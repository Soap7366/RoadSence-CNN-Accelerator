"""Isolate preparation from Tk; bound every stage and terminate its process tree."""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_task(command, credentials, events, cancelled, log_path, env=None,
             startup_timeout=45, idle_timeout=90):
    """Password travels over an anonymous stdin pipe, never argv or disk."""
    log_path=Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process=subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             errors='replace', env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    lines=queue.Queue()
    def read():
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    started=last_activity=time.monotonic()
    stage='启动准备任务'
    stage_started=started
    stage_limit=startup_timeout
    success=False
    eof=False
    secret=credentials.get('password', '')
    try:
        process.stdin.write(json.dumps(credentials, ensure_ascii=False)+'\n')
        process.stdin.flush()
        process.stdin.close()
        with log_path.open('w', encoding='utf-8') as log:
            while True:
                now=time.monotonic()
                if cancelled.is_set():
                    raise RuntimeError('准备已取消，相关连接已关闭。再次点击“开始准备”可重试。')
                if now-stage_started>stage_limit:
                    raise TimeoutError(f'“{stage}”超过 {stage_limit} 秒，任务已停止。请检查板卡与网络后重试。')
                if now-last_activity>idle_timeout:
                    raise TimeoutError(f'“{stage}”连续 {idle_timeout} 秒没有响应，任务已停止。请检查网线、板卡电源和地址。')
                try:
                    line=lines.get(timeout=.1)
                except queue.Empty:
                    if eof and process.poll() is not None:
                        break
                    continue
                if line is None:
                    eof=True
                    if process.poll() is not None: break
                    continue
                # Redaction precedes both persistent logging and UI delivery.
                last_activity=time.monotonic()
                try:
                    event=json.loads(line)
                except ValueError:
                    line=line.replace(secret, '***') if secret else line
                    log.write(line);log.flush()
                    events.put(('log', line)); continue
                if isinstance(event.get('text'),str) and secret:
                    event['text']=event['text'].replace(secret,'***')
                log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
                kind=event.get('kind')
                if kind=='stage':
                    stage=event['text'];stage_started=time.monotonic();stage_limit=event['timeout']
                    events.put(('stage', stage))
                    events.put(('log', '\n'+stage+'……\n'))
                elif kind=='success': success=True
                elif kind=='error': raise RuntimeError(event['text'])
                elif kind=='log': events.put(('log', event['text']))
            code=process.wait(timeout=5)
            if code or not success:
                raise RuntimeError(f'准备任务异常退出（{code}），详细日志：{log_path}')
    finally:
        stop_process(process)
        process.stdout.close()

