"""Read-only process trace to diagnose preparation stalls on the real board."""
import getpass
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src/stream')]
from tools.board_connection import connect


def main():
    client,_,_=connect(password=getpass.getpass('Board password: '))
    output=ROOT/'.runtime/board_live_trace.jsonl'
    try:
        with output.open('w',encoding='utf-8') as log:
            for index in range(60):
                stdin,stdout,stderr=client.exec_command('ps -eo pid,ppid,user,etime,pcpu,stat,wchan:20,args',timeout=8)
                stdin.channel.shutdown_write()
                text=stdout.read().decode(errors='replace')
                code=stdout.channel.recv_exit_status()
                selected=[line for line in text.splitlines() if any(word in line for word in ('python','gcc','cc1','sshd','native/build','boot_pl','ps -eo'))]
                log.write(json.dumps(dict(time=time.time(),code=code,processes=selected))+'\n');log.flush()
                print('snapshot',index,flush=True)
                time.sleep(3)
    finally:client.close()


if __name__=='__main__':main()
