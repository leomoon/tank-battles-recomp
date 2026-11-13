#!/usr/bin/env python3
"""Check packaged-read correlation using original synthetic ZIP contents."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from resource_reports import inspect_resources


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tank-resource-report-')
        self.root=Path(self.temp.name);self.run=self.root/'run';self.run.mkdir()
        self.title=self.root/'title';usr=self.title/'USRDIR';usr.mkdir(parents=True)
        self.archive=usr/'assets_images.edat.dec'
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('data/synthetic/image.dds',b'Original synthetic texture fixture.'*30)
            z.writestr('data/synthetic/other.bin',b'Original second fixture.'*10)
        (self.run/'session.json').write_text(json.dumps(dict(content_root=str(self.title),strict_passed=True)))
        (self.run/'startup.log').write_text('[sys_fs] open FAILED: /fixture/USRDIR/data/missing/image.dds (errno=2 missing)\n')
        self.directory=self.run/'resources';self.directory.mkdir()
        (self.directory/'media-imports.json').write_text('[]')
        self.payload=self.archive.read_bytes()
        self.write_trace()

    def tearDown(self):self.temp.cleanup()

    def write_trace(self,mutate=False,limit=False):
        cut=len(self.payload)//2
        rows=[dict(archive=self.archive.name,offset=0,bytes=cut,capture_offset=0),
              dict(archive=self.archive.name,offset=cut,bytes=len(self.payload)-cut,capture_offset=cut),dict(media_nid=123)]
        if limit:rows.append(dict(trace_limit=True))
        (self.directory/'reads.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
        data=bytearray(self.payload)
        if mutate:data[0]^=1
        (self.directory/'reads.bin').write_bytes(data)

    def inspect(self):
        with contextlib.redirect_stdout(io.StringIO()):return inspect_resources(self.run)

    def test_split_reads_and_zip_payloads_correlate(self):
        r=self.inspect();self.assertEqual(r['reads_verified'],2);self.assertEqual(len(r['payloads']),2)
        self.assertEqual(r['media_calls'],{'0000007b':1})
        failure=r['loose_failures'][0]
        self.assertFalse(failure['exact_casefold'])
        self.assertEqual(failure['candidate_payloads_read'][0]['path'],'data/synthetic/image.dds')

    def test_failed_session_is_diagnostic_only(self):
        (self.run/'session.json').write_text(json.dumps(dict(content_root=str(self.title),strict_passed=False,session=dict(launcher_exit_status=86))))
        with self.assertRaises(ValueError):self.inspect()
        with contextlib.redirect_stdout(io.StringIO()):r=inspect_resources(self.run,allow_failed=True)
        self.assertFalse(r['accepted_for_verification'])

    def test_changed_native_read_is_rejected(self):
        self.write_trace(mutate=True)
        with self.assertRaises(ValueError):self.inspect()

    def test_trace_limit_cannot_claim_complete_coverage(self):
        self.write_trace(limit=True)
        with self.assertRaises(ValueError):self.inspect()


if __name__=='__main__':unittest.main()
