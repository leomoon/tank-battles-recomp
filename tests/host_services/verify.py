"""Check actual process exit semantics of the portable strict HLE boundary."""
import os
import subprocess
import sys
from pathlib import Path
import tempfile
binary=Path(sys.argv[1])
for probe,label in [('missing','hle-missing'),('fallback','spu-interpreter')]:
    result=subprocess.run([str(binary),probe],env=dict(os.environ,TB_EXECUTION_MODE='strict'),capture_output=True,text=True,timeout=10)
    assert result.returncode==86,(result.returncode,result.stderr)
    assert 'STRICT STOP: '+label in result.stderr,result.stderr
    assert label+'=0x00000001' in result.stderr,result.stderr
for kind,label in [(1,'ppu-miss'),(4,'spu-untranslated'),(7,'ppu-recovery')]:
    result=subprocess.run([str(binary),'event',str(kind)],env=dict(os.environ,TB_EXECUTION_MODE='strict'),capture_output=True,text=True,timeout=10)
    assert result.returncode==86 and 'STRICT STOP: '+label in result.stderr,result.stderr
result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
assert result.returncode==0,result.stderr
assert 'host fixture snapshot' in result.stderr and 'hle_nid=' in result.stderr,result.stderr
with tempfile.TemporaryDirectory() as directory:
    trigger=Path(directory)/'snapshot'
    result=subprocess.run([str(binary)],env=dict(os.environ,TB_DIAGNOSTIC_TRIGGER=str(trigger)),capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and not trigger.exists(),result.stderr
    assert 'reason=snapshot requested' in result.stderr,result.stderr
    inventory=Path(directory)/'inventory.tsv'
    result=subprocess.run([str(binary)],env=dict(os.environ,TB_HLE_INVENTORY=str(inventory)),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    for kind in ('generic','context','fast','override'):
        assert kind+'\t' in inventory.read_text(),inventory.read_text()
if os.name=='nt':
    result=subprocess.run([str(binary),'crash'],capture_output=True,text=True,timeout=10)
    assert result.returncode not in (0,3),result.stderr
    assert 'fatal Windows exception' in result.stderr,result.stderr
print('PASS: strict missing import/fallback and portable HLE snapshots')
