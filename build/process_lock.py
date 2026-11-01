"""OS-owned file lock, released even when a preparation process is killed."""
from pathlib import Path
import os


class SingleInstance:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.file=path.open('a+b')
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        self.locked=False
    def acquire(self):
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:return False
        self.locked=True
        return True
    def close(self):
        if not self.file.closed:self.file.close()
