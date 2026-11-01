"""Run on the board after building libpscnn_frame_mock.so; no PL access."""
import ctypes
import platform
from pathlib import Path
import unittest
import numpy as np
from pscnn.native_session import NativeSession
from pscnn.model import ModelSpec
from pscnn.control import ControlError,ControlBusyError,ControlTimeout
ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.machine()=='armv7l' and (ROOT/'native/libpscnn_frame_mock.so').exists(),'Requires ARM board native software mock')
class NativeFaultTests(unittest.TestCase):
    def setUp(self):
        self.lib=ctypes.CDLL(str(ROOT/'native/libpscnn_frame_mock.so'))
        self.lib.pscnn_mock_new.restype=ctypes.c_void_p
        self.lib.pscnn_mock_free.argtypes=(ctypes.c_void_p,)
        self.lib.pscnn_mock_fault.argtypes=(ctypes.c_void_p,ctypes.c_uint32)
        self.lib.pscnn_mock_counts.argtypes=(ctypes.c_void_p,ctypes.c_uint32);self.lib.pscnn_mock_counts.restype=ctypes.c_uint32
        self.lib.pscnn_mock_wrap.argtypes=(ctypes.c_void_p,)
        self.base=self.lib.pscnn_mock_new()
        class MMIO:pass
        self.mmio=MMIO();self.mmio.array=np.ctypeslib.as_array((ctypes.c_uint32*2048).from_address(self.base))
        self.s=NativeSession(self.mmio,ModelSpec.load(),ROOT/'native/libpscnn_frame_mock.so',ack_words=1)

    def tearDown(self):self.lib.pscnn_mock_free(self.base)

    def fault(self,n):self.lib.pscnn_mock_fault(self.base,n)
    def counts(self):return tuple(self.lib.pscnn_mock_counts(self.base,n) for n in (0,1))

    def test_continuous_full_reads_identity_each_frame_and_signed_results(self):
        for i in range(5):
            raw=self.s.infer(bytes(range(128))*32)
            self.assertEqual(raw,{'collision_int32':-(1<<31),'steering_int32':(1<<31)-1})
            r=self.s.last_run
            self.assertTrue(r['readback_verified']);self.assertEqual(r['bytes_read'],4096);self.assertEqual(r['bytes_written'],4096)
            self.assertGreater(r['read_calls'],2048);self.assertEqual(r['write_calls'],1026)
            self.assertEqual(r['result_seq'],i+1)
        self.fault(2)
        with self.assertRaises(ControlError):self.s.infer(bytes(4096))
        self.assertEqual(self.counts(),(5,5))

    def test_bad_identity_never_resets_or_starts(self):
        for fault in (1,2,3):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(4096))
            self.assertEqual(self.counts(),(0,0))

    def test_unrelated_busy_never_resets(self):
        self.fault(4)
        with self.assertRaises(ControlBusyError):self.s.infer(bytes(4096))
        self.assertEqual(self.counts(),(0,0))

    def test_reserved_and_inconsistent_initial_status_rejected(self):
        for fault in (16,17,18,19,26):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(4096))
            self.assertEqual(self.counts(),(0,0))

    def test_count_and_complete_readback_corruptions_never_start(self):
        for fault in (5,6,7,8,9,10,20,23):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(range(128))*32,timeout_s=.02)
            self.assertEqual(self.counts()[1],0)
            self.fault(0);self.s.recover()

    def test_wrong_start_sequence_and_consumption_rejected(self):
        for fault in (11,12):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(4096))
            self.fault(0);self.s.recover()

    def test_wrong_result_pair_sequence_and_lost_valid_rejected(self):
        for fault in (13,14,15):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(4096))
            self.fault(0);self.s.recover()

    def test_compute_timeout_recovers_owned_frame(self):
        self.fault(21)
        with self.assertRaises(ControlTimeout):self.s.infer(bytes(4096),timeout_s=.01)
        self.assertTrue(self.s.last_run['recovery_succeeded']);self.assertFalse(self.s.needs_recovery)

    def test_failed_recovery_poison_blocks_then_explicit_recovery(self):
        self.fault(22)
        with self.assertRaises(ControlTimeout):self.s.infer(bytes(4096),timeout_s=.002)
        self.assertTrue(self.s.needs_recovery)
        counts=self.counts()
        with self.assertRaises(ControlError):self.s.infer(bytes(4096))
        self.assertEqual(self.counts(),counts)
        self.fault(0);self.s.recover();self.s.infer(bytes(4096))

    def test_status_transition_retried_and_counter_wrap(self):
        self.fault(24);self.lib.pscnn_mock_wrap(self.base)
        self.s.infer(bytes(4096));self.assertEqual(self.s.last_run['result_seq'],0)

    def test_argument_hash_range_and_readback_policy(self):
        for data,kwargs in ((b'x',{}),(bytes(4096),{'expected_sha256':'0'*64}),(bytes(4096),{'verify_readback':False}),(b'\xff'*4096,{})):
            with self.assertRaises((ValueError,ControlError)):self.s.infer(data,**kwargs)
        self.assertEqual(self.counts(),(0,0))

    def test_concurrent_caller_is_rejected(self):
        self.s._lock.acquire()
        try:
            with self.assertRaises(ControlBusyError):self.s.infer(bytes(4096))
        finally:self.s._lock.release()

    def test_frame_ack_retains_complete_reads_and_error_checks(self):
        self.s.ack_words=1024
        self.s.infer(bytes(4096))
        self.assertEqual(self.s.last_run['count_checks'],1)
        self.assertEqual(self.s.last_run['bytes_read'],4096)
        for fault in (7,8,9,10,11,12,13,14,15,20,23):
            self.fault(fault)
            with self.assertRaises(ControlError):self.s.infer(bytes(4096))
            self.fault(0);self.s.recover()

    def test_all_acknowledgment_strides_and_last_pixel_range(self):
        for ack in (1,16,64,1024):
            self.s.ack_words=ack;self.s.infer(bytes(4096))
            self.assertEqual(self.s.last_run['count_checks'],1024//ack)
        for index in (0,2048,4095):
            payload=bytearray(4096);payload[index]=128;counts=self.counts()
            with self.assertRaises(ControlError):self.s.infer(payload)
            self.assertEqual(self.counts(),counts)
