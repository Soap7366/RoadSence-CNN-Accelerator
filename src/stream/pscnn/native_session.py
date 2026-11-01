"""Single ordered native frame call, full checks; Python baseline stays intact."""
import ctypes,hashlib,math,threading,time,weakref
from .errors import ControlError,ControlTimeout,ControlBusyError
from .overlay_artifact import MODEL_ID
from .postprocess import register_word_to_int32


class FrameReport(ctypes.Structure):
    _fields_=[(k,ctypes.c_int32) for k in ('code','phase','recovery_code')]+[(k,ctypes.c_uint32) for k in
        ('bytes_written','bytes_read','result_seq','collision','steering','reset_requests','start_requests','read_calls','write_calls','poll_count','count_checks')]+[(k,ctypes.c_uint64) for k in
        ('identity_ns','reset_ns','write_ns','readback_ns','start_wait_ns','output_ns','total_ns')]


class NativeSession:
    def __init__(self,mmio,model,library,poll_us=0,ack_words=1024):
        if model.model_id!=MODEL_ID or model.quant_sha256!='346bafa31f56bd5a28bff0d8ee7d8cd850e5e2da7f56e536037e46d033dc4225':
            raise ValueError('Pinned native model required')
        if type(poll_us) is not int or not 0<=poll_us<=1000:raise ValueError('Invalid native polling interval')
        if type(ack_words) is not int or ack_words not in (1,16,64,1024):raise ValueError('Invalid write acknowledgment stride')
        self.mmio=mmio;self.poll_us=poll_us;self.ack_words=ack_words;self._lock=threading.Lock();self.needs_recovery=False;self.last_run={}
        self.lib=ctypes.CDLL(str(library))
        self.lib.pscnn_frame_report_size.restype=ctypes.c_uint32
        if self.lib.pscnn_frame_report_size()!=ctypes.sizeof(FrameReport):raise RuntimeError('Native report ABI size mismatch')
        self.lib.pscnn_frame_new.argtypes=(ctypes.c_void_p,);self.lib.pscnn_frame_new.restype=ctypes.c_void_p
        self.lib.pscnn_frame_free.argtypes=(ctypes.c_void_p,);self.lib.pscnn_frame_free.restype=None
        self.lib.pscnn_frame_infer.argtypes=(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint64,ctypes.c_uint32,ctypes.c_uint32,ctypes.POINTER(FrameReport))
        self.lib.pscnn_frame_infer.restype=ctypes.c_int
        self.lib.pscnn_frame_recover.argtypes=(ctypes.c_void_p,ctypes.c_uint64,ctypes.POINTER(FrameReport));self.lib.pscnn_frame_recover.restype=ctypes.c_int
        self.context=self.lib.pscnn_frame_new(mmio.base if hasattr(mmio,'base') else mmio.array.ctypes.data)
        if not self.context:raise RuntimeError('Native frame context allocation failed')
        self._finalizer=weakref.finalize(self,self.lib.pscnn_frame_free,self.context)

    @staticmethod
    def _timeout(value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .000001<=value<=60:
            raise ValueError('Timeout must be 1 microsecond to 60 seconds')
        return int(value*1e9)

    def infer(self,payload,timeout_s=1.0,verify_readback=True,expected_sha256=None):
        started=time.perf_counter()
        input_started_ns=time.perf_counter_ns()
        if not self._lock.acquire(blocking=False):raise ControlBusyError('Native session already active')
        try:
            if self.needs_recovery:raise ControlError('Native session requires explicit recovery')
            ns=self._timeout(timeout_s)
            if verify_readback is not True:raise ValueError('Native session requires full input readback')
            if not isinstance(payload,(bytes,bytearray,memoryview)):raise ValueError('Expected bytes-like frame')
            payload=bytes(payload)
            if len(payload)!=4096:raise ValueError('Expected 4096 bytes')
            digest=hashlib.sha256(payload).hexdigest()
            if expected_sha256 is not None and digest!=expected_sha256:raise ValueError('Input hash mismatch')
            input_finished_ns=time.perf_counter_ns()
            source=ctypes.c_char_p(payload);report=FrameReport()
            call_started_ns=time.perf_counter_ns()
            code=self.lib.pscnn_frame_infer(self.context,source,4096,ns,self.poll_us,self.ack_words,ctypes.byref(report))
            call_finished_ns=time.perf_counter_ns()
            self.last_run={k:getattr(report,k) for k,_ in FrameReport._fields_}
            self.last_run.update(input_int8_sha256=digest,readback_verified=(code==0 and report.bytes_read==4096),
                                 native_core_s=report.total_ns/1e9,elapsed_s=time.perf_counter()-started,start_to_completed_s=report.start_wait_ns/1e9,
                                 recovery_succeeded=(report.recovery_code==0),
                                 phase_timings_s={k[:-3]+'_s':getattr(report,k)/1e9 for k in
                                                  ('identity_ns','reset_ns','write_ns','readback_ns','start_wait_ns','output_ns')})
            self.needs_recovery=report.recovery_code!=0
            if code:
                message='Native frame rejected code={} phase={} recovery={}'.format(code,report.phase,report.recovery_code)
                if code==-10:raise ControlTimeout(message)
                if code==-3:raise ControlBusyError(message)
                raise ControlError(message)
            unpack_finished_ns=time.perf_counter_ns()
            result={'collision_int32':register_word_to_int32(report.collision),'steering_int32':register_word_to_int32(report.steering)}
            decoded_ns=time.perf_counter_ns()
            self.last_run.update(python_input_check_hash_ns=input_finished_ns-input_started_ns,
                                 python_ctypes_setup_ns=call_started_ns-input_finished_ns,
                                 ctypes_call_ns=call_finished_ns-call_started_ns,
                                 python_report_unpack_ns=unpack_finished_ns-call_finished_ns,
                                 python_result_decode_ns=decoded_ns-unpack_finished_ns)
            self.last_run['elapsed_s']=time.perf_counter()-started
            return result
        finally:self._lock.release()

    def recover(self,timeout_s=.25):
        if not self._lock.acquire(blocking=False):raise ControlBusyError('Native session active')
        try:
            report=FrameReport();code=self.lib.pscnn_frame_recover(self.context,self._timeout(timeout_s),ctypes.byref(report))
            self.needs_recovery=code!=0
            if code:raise ControlError('Native recovery rejected '+str(code))
        finally:self._lock.release()
