import ctypes
import io
import platform
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from pscnn.fast_preprocess import FastPreprocessor,build_table
from pscnn.model import ModelSpec
from pscnn.preprocess import quantize_input
from pscnn.image_stream import prepare_bytes
ROOT=Path(__file__).resolve().parents[1]

class TableTests(unittest.TestCase):
    def test_all_256_levels_are_reference_exact(self):
        expected=quantize_input(np.arange(256,dtype=np.uint8).astype(np.float32)/255.0,1/127).tobytes()
        self.assertEqual(build_table(1/127),expected)

@unittest.skipUnless(platform.machine()=='armv7l' and (ROOT/'native/libpscnn_preprocess.so').exists(),'ARM preprocessing required')
class NativePreTests(unittest.TestCase):
    def setUp(self):self.pre=FastPreprocessor(ModelSpec.load(),ROOT/'native/libpscnn_preprocess.so')
    def tearDown(self):self.pre.close()
    def test_varied_sizes_modes_and_progressive(self):
        rng=np.random.RandomState(31)
        for width,height in [(960,720),(319,241),(113,79),(64,64),(31,17),(65,67)]:
            for mode in ['RGB','L']:
                for progressive in [False,True]:
                    shape=(height,width,3) if mode=='RGB' else (height,width)
                    im=Image.fromarray(rng.randint(0,256,shape).astype(np.uint8))
                    data=io.BytesIO();im.save(data,format='JPEG',progressive=progressive)
                    encoded=data.getvalue()
                    self.assertEqual(self.pre.prepare(encoded)[0],prepare_bytes(encoded,ModelSpec.load())[0],(width,height,mode,progressive))
    def test_truncated_and_invalid_jpeg_rejected(self):
        b=io.BytesIO();Image.new('RGB',(960,720),(39,159,233)).save(b,format='JPEG')
        for data in [b'no JPEG',b.getvalue()[:-30]]:
            with self.assertRaises((ValueError,OSError)):self.pre.prepare(data)
    def test_cmyk_uses_exact_fallback(self):
        b=io.BytesIO();Image.new('CMYK',(113,79),(31,63,95,127)).save(b,format='JPEG')
        self.assertEqual(self.pre.prepare(b.getvalue())[0],prepare_bytes(b.getvalue(),ModelSpec.load())[0])
    def test_two_workers_share_immutable_coefficients_safely(self):
        from concurrent.futures import ThreadPoolExecutor
        samples=[]
        for i in range(8):
            b=io.BytesIO();Image.new('RGB',(319,241),(i*31,255-i*19,i*17)).save(b,format='JPEG')
            data=b.getvalue();samples.append((data,prepare_bytes(data,ModelSpec.load())[0]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual=list(pool.map(lambda item:self.pre.prepare(item[0])[0],samples))
        self.assertEqual(actual,[item[1] for item in samples])
