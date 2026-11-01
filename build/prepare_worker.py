"""Private process entry for the wizard; no credentials in arguments or logs."""
import contextlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build'))


def main():
    wire=sys.stdout
    credentials=json.loads(sys.stdin.readline())
    secret=credentials['password']
    def emit(kind, **values):
        values={k:(v.replace(secret,'***') if isinstance(v,str) else v) for k,v in values.items()}
        line=json.dumps(dict(kind=kind, **values), ensure_ascii=False)
        wire.write(line+'\n');wire.flush()
    class Output:
        def write(self,text):
            if text: emit('log',text=text)
        def flush(self): pass
    try:
        with contextlib.redirect_stdout(Output()),contextlib.redirect_stderr(Output()):
            import prepare
            prepare.prepare_board(credentials['host'], secret,
                                  stage=lambda text,timeout:emit('stage',text=text,timeout=timeout))
        emit('success')
        return 0
    except Exception as exc:
        emit('error',text=f'{type(exc).__name__}: {exc}')
        return 1


if __name__=='__main__':raise SystemExit(main())
