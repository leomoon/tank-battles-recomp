"""The control-file trigger must emit the same parsable timeline on both OSes."""
from pathlib import Path
import subprocess
import sys
import tempfile
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'scripts'))
from frame_budget import load
with tempfile.TemporaryDirectory() as directory:
    output=Path(directory)/'frame.trace';trigger=Path(directory)/'arm'
    subprocess.run([sys.argv[1],str(output),str(trigger)],check=True,timeout=10)
    assert not trigger.exists(),'Trigger was not consumed'
    assert output.read_text().startswith('# ps3_ftrace start_us=')
    header,names,spans=load(output)
    assert len(spans)>20,'No useful spans loaded'
    print('PASS: control-file arming and original timeline format')
