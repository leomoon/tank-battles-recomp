import os,stat,tempfile,unittest
from pathlib import Path
from diagnostic_logs import open_log,normalize_log_permissions

class DiagnosticLogTests(unittest.TestCase):
 def test_restrictive_umask_still_creates_readable_log(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'startup.log';previous=os.umask(0o777)
   try:
    with open_log(path) as stream:stream.write(b'fixture')
   finally:os.umask(previous)
   self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o644)
 def test_repairs_vendor_logs_without_touching_keys_or_links(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);log=root/'RPCS3.log.gz';log.write_bytes(b'synthetic');log.chmod(0)
   key=root/'private.rifkey';key.write_bytes(b'synthetic');key.chmod(0o600)
   (root/'linked.log').symlink_to(key)
   self.assertEqual(normalize_log_permissions(root),[str(log)])
   self.assertEqual(stat.S_IMODE(log.stat().st_mode),0o644)
   self.assertEqual(stat.S_IMODE(key.stat().st_mode),0o600)
   self.assertEqual(normalize_log_permissions(root),[])
if __name__=='__main__':unittest.main()
