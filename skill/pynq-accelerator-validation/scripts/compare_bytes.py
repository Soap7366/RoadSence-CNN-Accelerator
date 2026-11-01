"""Compare exact input bytes using only the Python standard library."""
import argparse
import hashlib
import json
from pathlib import Path


def compare(reference, candidate):
    a, b = Path(reference).read_bytes(), Path(candidate).read_bytes()
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    if first is None and len(a) != len(b):
        first = min(len(a), len(b))
    return dict(status='PASS' if a == b else 'FAIL', reference_bytes=len(a),
                candidate_bytes=len(b), first_difference=first,
                reference_sha256=hashlib.sha256(a).hexdigest(),
                candidate_sha256=hashlib.sha256(b).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference')
    parser.add_argument('candidate')
    args = parser.parse_args()
    result = compare(args.reference, args.candidate)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
