"""Real Tk acceptance driver; passwords arrive on stdin and never enter reports."""
import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',required=True)
    parser.add_argument('--mode',choices=['prepare','desktop'],required=True)
    args=parser.parse_args()
    root=Path(args.package).resolve()
    sys.path[:0]=[str(root),str(root/'build'),str(root/'src/desktop'),str(root/'src/stream')]
    password=getpass.getpass('Board password: ')
    result=dict(status='FAIL',mode=args.mode,package=str(root))
    started=time.monotonic()
    if args.mode=='prepare':
        import prepare
        assert prepare.ROOT==root
        app=prepare.Wizard()
        def done(title,text):
            result.update(status='PASS' if text.startswith('准备完成') else 'FAIL',message=text)
            app.after(300,app.destroy)
        app.host.set('192.168.2.99');app.secret.set(password)
        def timeout():
            result['message']='acceptance driver deadline'
            app.cancel();app.closing=True
        app.after(900000,timeout)
        app.after(500,app.begin)
        with patch.object(prepare.messagebox,'showinfo',done),patch.object(prepare.messagebox,'showerror',done):
            app.mainloop()
    else:
        import desktop
        import cv2
        assert Path(desktop.__file__).resolve().is_relative_to(root)
        video=root/'.runtime/acceptance.avi'
        writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),8,(320,240))
        assert writer.isOpened()
        for index in range(16):
            images=sorted((root/'data/images').glob('*.jpg'))
            image=cv2.imread(str(images[index%len(images)]))
            assert image is not None
            writer.write(cv2.resize(image,(320,240)))
        writer.release()
        app=desktop.Demo()
        app.host.set('192.168.2.99');app.password.set(password)
        phase=['connect']
        viewed=[]
        def fail(title,text):
            result['message']=text
            app.after(0,app.close)
        def poll():
            try:
                if time.monotonic()-started>180:raise TimeoutError('Desktop acceptance deadline')
                if phase[0]=='connect' and app.board.ready and not app.busy:
                    with patch.object(desktop.filedialog,'askopenfilename',return_value=str(video)):app.choose()
                    app.process_video();phase[0]='inference'
                elif phase[0]=='inference' and not app.busy:
                    frames=[row['video_frame_id'] for row in app.inference_samples]
                    assert frames==[0,4,8,12],frames
                    result['sampled_frames']=frames
                    app.enter();phase[0]='play';result['play_started']=time.monotonic()
                elif phase[0]=='play':
                    viewed.append(app.display_index)
                    if time.monotonic()-result['play_started']>4:
                        assert max(viewed)>0,viewed
                        result['displayed_frame_ids']=sorted(set(viewed))
                        result['status']='PASS'
                        result.pop('play_started',None)
                        app.close();return
                app.after(100,poll)
            except Exception as exc:
                result['message']=str(exc);app.close()
        with patch.object(desktop.messagebox,'showerror',fail):
            app.after(300,app.connect_board)
            app.after(400,poll)
            app.mainloop()
    result['elapsed_s']=round(time.monotonic()-started,2)
    output=root/'.runtime'/('acceptance_'+args.mode+'.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
