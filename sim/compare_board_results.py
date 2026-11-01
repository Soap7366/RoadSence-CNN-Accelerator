"""Compare exact input hashes, INT32 outputs and decoded values across releases."""
import argparse
import getpass
import json
import sys
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--package',default=str(Path(__file__).resolve().parents[1]))
    args=parser.parse_args()
    root=Path(args.package).resolve()
    sys.path[:0]=[str(root/'src/desktop'),str(root/'src/stream')]
    import cv2
    from board_adapter import BoardAdapter
    from pscnn.runtime_config import load_config
    cfg=load_config()
    secret=getpass.getpass('Board password: ')
    video=root/'.runtime/compare_results.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),8,(320,240))
    assert writer.isOpened()
    for index in range(16):
        images=sorted((root/'data/images').glob('*.jpg'))
        image=cv2.imread(str(images[index%len(images)]))
        assert image is not None
        writer.write(cv2.resize(image,(320,240)))
    writer.release()
    results=[]
    for label,release in [('baseline',args.baseline),('current',cfg['remote_root'])]:
        chosen=dict(cfg,remote_root=release)
        adapter=BoardAdapter()
        try:
            with patch('pscnn.runtime_config.load_config',return_value=chosen):
                adapter.connect(cfg['host'],secret)
                adapter.process(str(video),4,'compare_'+label)
            complete=[value for kind,value in list(adapter.events.queue) if kind=='complete'][-1]
            rows=[]
            for sample in complete['samples']:
                event=sample['result']
                rows.append(dict(frame=sample['video_frame_id'],source_sha256=event['source_sha256'],
                                 input_sha256=event['input_sha256'],raw=event['raw'],decoded=event['decoded']))
            results.append(dict(release=release,samples=rows))
            print(label,'frames',[row['frame'] for row in rows],flush=True)
        finally:adapter.close()
    report=dict(status='PASS' if results[0]['samples']==results[1]['samples'] else 'FAIL',
                compared='exact original JPEG hash, INT8 input hash, raw INT32 outputs, all decoded values',
                results=results)
    output=root/'sim/results/board_result_equivalence.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(report['status'],output)
    return report['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
