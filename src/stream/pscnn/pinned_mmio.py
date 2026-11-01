"""Map only the verified boot-loaded bridge; no overlay download or PYNQ import.

Caller must own the global device lock. Full artifact/HWH validation precedes
this constructor. Same-boot record is checked on BOTH sides of mapping. All
device access uses ordered C volatile reads/writes; never numpy bulk MMIO.
"""
import ctypes,mmap,os,platform
from .boot_state import load_boot_state


class PinnedMMIO:
    def __init__(self,artifact,library):
        if platform.machine()!='armv7l':raise RuntimeError('ARMv7 board required')
        expected={'phys_addr':0x40000000,'addr_range':8192,'ip_name':'cnn_axi_accelerator_0',
                  'bit_sha256':'dc960f1ecc56b6e84bf44f8e507227676c63dd4f36a555a88ab28b49b0568cc5',
                  'hwh_sha256':'2084914b551ddeca3ec7674b945cb8cf4fb855908e97336db3a30753e4335ee8'}
        if any(artifact.get(k)!=v for k,v in expected.items()):raise ValueError('Pinned MMIO artifact/range mismatch')
        state=load_boot_state(artifact)
        self.lib=ctypes.CDLL(str(library))
        self.lib.pscnn_read_words.argtypes=(ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32),ctypes.c_uint32)
        self.lib.pscnn_read_words.restype=ctypes.c_int
        self.lib.pscnn_write_control.argtypes=(ctypes.c_void_p,ctypes.c_uint32)
        self.lib.pscnn_write_control.restype=ctypes.c_int
        fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC)
        try:self.mapping=mmap.mmap(fd,8192,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE,offset=0x40000000)
        finally:os.close(fd)
        self.array=(ctypes.c_uint32*2048).from_buffer(self.mapping)
        self.base=ctypes.addressof(self.array)
        if load_boot_state(artifact)!=state:
            self.close();raise RuntimeError('Boot state changed while mapping')

    def read(self,offset):
        if type(offset) is not int or offset<0 or offset>0x60 or offset%4:raise ValueError('Invalid pinned register read')
        address=ctypes.c_uint32(offset);value=ctypes.c_uint32()
        if self.lib.pscnn_read_words(self.base,ctypes.byref(address),ctypes.byref(value),1):raise RuntimeError('Ordered register read failed')
        return value.value

    def write(self,offset,value):
        if offset!=8 or type(value) is not int or value not in (1,2):raise ValueError('Only bridge reset/start control writes permitted')
        if self.lib.pscnn_write_control(self.base,value):raise RuntimeError('Ordered control write failed')

    def close(self):
        self.array=None
        self.mapping.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
