"""Validate profile-selected fields in JSONL; never certify hardware execution."""
import argparse
import json
import math
from pathlib import Path


def field(row, path):
    for part in path.split('.'):
        row = row[part]
    return row


def validate(rows, profile):
    errors = []
    previous = None
    if not rows:
        return ['empty records']
    for index, row in enumerate(rows):
        try:
            for path, expected in profile['equals'].items():
                actual = field(row, path)
                if type(actual) is not type(expected) or actual != expected:
                    raise ValueError('unexpected ' + path)
            for path in profile['finite']:
                value = field(row, path)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('not finite: ' + path)
            for path, bounds in profile['ranges'].items():
                if not bounds[0] <= field(row, path) <= bounds[1]:
                    raise ValueError('out of range: ' + path)
            for a, b in profile['matching_fields']:
                if field(row, a) != field(row, b):
                    raise ValueError('mismatch: ' + a + '/' + b)
            for path in profile['sha256']:
                value = field(row, path)
                if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError('invalid SHA256: ' + path)
            frame = field(row, profile['frame'])
            if type(frame) is not int or frame != index:
                raise ValueError('frame must start at zero and be continuous')
            seq = field(row, profile['sequence'])
            if type(seq) is not int or not 0 <= seq < 2**profile['sequence_bits']:
                raise ValueError('invalid sequence')
            if previous is not None and seq != (previous + 1) % 2**profile['sequence_bits']:
                raise ValueError('sequence gap')
            for path in profile['constant']:
                if field(row, path) != field(rows[0], path):
                    raise ValueError('mixed session/run: ' + path)
            previous = seq
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            errors.append('row %d: %s' % (index + 1, exc))
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--expected-count', type=int, required=True,
                        help='Expected sampled frame count; obtain independently from the source')
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in Path(args.records).read_text(encoding='utf-8').splitlines() if line.strip()]
        errors = validate(rows, json.loads(Path(args.profile).read_text(encoding='utf-8')))
        if args.expected_count < 1 or len(rows) != args.expected_count:
            errors.append('expected count mismatch')
    except (OSError, ValueError) as exc:
        rows, errors = [], [str(exc)]
    print(json.dumps(dict(status='FAIL' if errors else 'PASS', rows=len(rows), errors=errors,
                         scope='record consistency only; run_complete requires separate evidence'), indent=2))
    raise SystemExit(bool(errors))
