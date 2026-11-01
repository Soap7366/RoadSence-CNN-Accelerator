"""PS JPEG preprocessing preserving original grayscale, two-pass resize and INT8."""
import ctypes
import io
import math
import struct
import threading
import time
from PIL import Image, JpegImagePlugin


class PreReport(ctypes.Structure):
    _fields_=[(k,ctypes.c_uint64) for k in ('header_ns','start_ns','decode_ns','gray_ns','horizontal_ns',
        'finish_ns','vertical_ns','lut_ns','total_ns')]+[(k,ctypes.c_uint32) for k in ('width','height','scanline_calls')]


def f32(v):
    return struct.unpack('f',struct.pack('f',v))[0]


def build_table(scale):
    if scale!=1.0/127:
        raise ValueError('Pinned input scale required')
    # Same float32 normalization/division followed by float64 half-away rounding.
    return bytes(min(127,max(0,math.floor(f32(f32(v/255.0)/f32(scale))+.5))) for v in range(256))


class FastPreprocessor:
    def __init__(self,model,library):
        self.model=model
        mark=time.perf_counter_ns()
        self.table=build_table(model.input_scale)
        self.table_build_ns=time.perf_counter_ns()-mark
        self.lib=ctypes.CDLL(str(library))
        self.lib.pscnn_resize_new.argtypes=(ctypes.c_int,ctypes.c_int)
        self.lib.pscnn_resize_new.restype=ctypes.c_void_p
        self.lib.pscnn_resize_free.argtypes=(ctypes.c_void_p,)
        self.lib.pscnn_jpeg_resize.argtypes=(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,
            ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(PreReport))
        self.lib.pscnn_jpeg_resize.restype=ctypes.c_int
        self.contexts={};self.lock=threading.Lock()

    def close(self):
        for ctx in self.contexts.values():self.lib.pscnn_resize_free(ctx)
        self.contexts.clear()

    def prepare(self,data):
        begin=time.perf_counter_ns();timing={}
        with Image.open(io.BytesIO(data),formats=['JPEG']) as image:
            width,height=image.size;mode=image.mode
        timing['image_header_ns']=time.perf_counter_ns()-begin
        if not 1<=width<=4096 or not 1<=height<=4096 or width*height>16000000:
            raise ValueError('Image dimensions exceed bounded preprocessing limits')
        if mode not in ('RGB','L'):
            # Non-RGB JPEG modes keep the reference path explicitly.
            from .image_stream import prepare_bytes
            payload,legacy=prepare_bytes(data,self.model)
            legacy['fallback']=True
            return payload,legacy
        mark=time.perf_counter_ns()
        with self.lock:
            ctx=self.contexts.get((width,height))
            if ctx is None:
                if len(self.contexts)>=32:
                    raise ValueError('Too many distinct image dimensions in one session')
                ctx=self.lib.pscnn_resize_new(width,height)
                if not ctx:raise ValueError('Resize context allocation failed')
                self.contexts[width,height]=ctx
        timing['resize_context_ns']=time.perf_counter_ns()-mark
        mark=time.perf_counter_ns()
        out=ctypes.create_string_buffer(4096);report=PreReport()
        source=ctypes.c_char_p(data);table=ctypes.c_char_p(self.table)
        timing['preprocess_ctypes_setup_ns']=time.perf_counter_ns()-mark
        mark=time.perf_counter_ns()
        code=self.lib.pscnn_jpeg_resize(ctx,source,len(data),table,out,ctypes.byref(report))
        timing['preprocess_ctypes_call_ns']=time.perf_counter_ns()-mark
        if code:raise ValueError('Native JPEG preprocessing failed: '+str(code))
        timing.update({'pre_'+k:getattr(report,k) for k,_ in PreReport._fields_})
        mark=time.perf_counter_ns();payload=out.raw
        timing['input_serialize_ns']=time.perf_counter_ns()-mark
        timing['preprocess_ns']=time.perf_counter_ns()-begin
        return payload,timing
