import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skill/pynq-accelerator-validation'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / ('scripts/' + name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SkillTests(unittest.TestCase):
    def test_byte_equality_and_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a', Path(directory)/'b'
            a.write_bytes(b'abc'); b.write_bytes(b'abc')
            compare = module('compare_bytes').compare
            self.assertEqual(compare(a,b)['status'], 'PASS')
            b.write_bytes(b'abd')
            self.assertEqual(compare(a,b)['first_difference'], 2)
            b.write_bytes(b'ab')
            self.assertEqual(compare(a,b)['first_difference'], 2)

    def test_record_contract_accepts_wrap_and_rejects_corruption(self):
        profile = json.loads((SKILL/'assets/cnn_profile.json').read_text())
        rows = []
        for i in range(2):
            rows.append(dict(session_id='test', run_id=0, protocol_frame_id=i, risk=.5, steering_deg=0.,
                result=dict(event='image_result',run_id=0,frame_id=i,source_sha256='a'*64,input_sha256='b'*64,
                    native=dict(readback_verified=True,count_checks=1024,bytes_read=4096,bytes_written=4096,result_seq=(2**32-1+i)%2**32),
                    decoded=dict(risk_score=.5,steering_value=0.,steering_units='rad_label_space'))))
        validate = module('check_records').validate
        self.assertEqual(validate(rows,profile), [])
        mutations = [lambda r:r[1].update(protocol_frame_id=4),
                     lambda r:r[1].update(session_id='other'),
                     lambda r:r[0]['result']['native'].update(readback_verified=False),
                     lambda r:r[0]['result']['decoded'].update(risk_score=float('nan')),
                     lambda r:r[0]['result'].update(input_sha256='invalid'),
                     lambda r:r[1]['result']['native'].update(result_seq=5)]
        for mutate in mutations:
            damaged=copy.deepcopy(rows);mutate(damaged)
            self.assertTrue(validate(damaged,profile))
        self.assertTrue(validate([],profile))


if __name__ == '__main__':
    unittest.main()
