"""Create the actual desktop from this package, validate construction and exit."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/desktop'))
import desktop
app=desktop.Demo()
app.withdraw()
app.update_idletasks()
app.update()
report=dict(status='PASS',actual_desktop_created=True,title=app.title(),
            configured_host=app.host.get(),scope='GUI construction only; no board connection')
app.close()
folder=ROOT/'sim/results';folder.mkdir(exist_ok=True)
(folder/'gui_smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
