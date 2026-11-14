"""Exercise real profile mounts and reopening using only synthetic files."""
import json
from pathlib import Path
import tempfile
import unittest
from user_profiles import UNLOCK, open_profile
from port import launch_environment


class UserProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.content = self.source/'game/NPUB30154'
        self.content.mkdir(parents=True)
        self.exdata = self.source/'home/00000001/exdata'
        self.exdata.mkdir(parents=True)
        for name in (UNLOCK, UNLOCK+'.rifkey'):
            (self.exdata/name).write_bytes(b'synthetic entitlement')

    def profile(self, name, mode='purchased', resume=False):
        return open_profile(self.root/name, self.source, self.content, 'NPUB30154', mode, resume)

    def test_isolated_modes_and_resume_preserve_saves_and_originals(self):
        original = {p.name:p.read_bytes() for p in self.exdata.iterdir()}
        for name,mode in [('full','purchased'),('trial','missing-unlock')]:
            metadata,lock = self.profile(name,mode)
            profile = Path(metadata['path'])
            self.assertEqual((profile/'dev_hdd0/game/NPUB30154').resolve(),self.content)
            save=profile/'dev_hdd0/home/00000001/savedata/fixture'
            save.parent.mkdir(parents=True);save.write_bytes(b'private save')
            if mode=='purchased':
                local=profile/'dev_hdd0/home/00000001/exdata'/UNLOCK
                self.assertNotEqual(local.stat().st_ino,(self.exdata/UNLOCK).stat().st_ino)
                local.write_bytes(b'private mutation')
            else:self.assertEqual(list((profile/'dev_hdd0/home/00000001/exdata').iterdir()),[])
            lock.close()
            metadata,lock=self.profile(name,mode,True)
            self.assertEqual(metadata['operation'],'resume');self.assertEqual(save.read_bytes(),b'private save');lock.close()
            with self.assertRaises(FileExistsError):self.profile(name,mode)
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.exdata.iterdir()})

    def test_resume_rejects_mode_changes_and_concurrent_sessions(self):
        metadata,lock=self.profile('full')
        with self.assertRaises(BlockingIOError):self.profile('full',resume=True)
        lock.close()
        with self.assertRaises(ValueError):self.profile('full','missing-unlock',True)
        with self.assertRaises(ValueError):self.profile('absent',resume=True)

    def test_resume_rejects_external_save_and_entitlement_links(self):
        metadata,lock=self.profile('full');lock.close()
        private=Path(metadata['mounts']['PS3_HDD0_ROOT'])/'home/00000001'
        (private/'savedata').symlink_to(self.exdata,target_is_directory=True)
        with self.assertRaises(ValueError):self.profile('full',resume=True)
        (private/'savedata').unlink()
        target=private/'exdata'/UNLOCK;target.unlink();target.hardlink_to(self.exdata/UNLOCK)
        with self.assertRaises(ValueError):self.profile('full',resume=True)

    def test_missing_profile_rejects_added_unlock(self):
        metadata,lock=self.profile('trial','missing-unlock');lock.close()
        (Path(metadata['mounts']['PS3_HDD0_ROOT'])/'home/00000001/exdata'/UNLOCK).write_bytes(b'added')
        with self.assertRaises(ValueError):self.profile('trial','missing-unlock',True)

    def test_launch_uses_selected_mounts_before_creating_cache_directory(self):
        metadata,lock=self.profile('full')
        self.addCleanup(lock.close)
        sfo=self.content/'PARAM.SFO';sfo.write_bytes(b'synthetic metadata')
        original_cache=self.source/'dev_hdd1'
        config=dict(game=dict(title='Synthetic',title_id='NPUB30154',paramsfo=str(sfo)),
                    paths=dict(vfs_root=str(self.content),hdd0_root=str(self.source),
                               hdd1_root=str(original_cache),gamedata_root=str(self.source),flash_root=str(self.source/'flash')))
        env=launch_environment(config,str(sfo),mounts=metadata['mounts'])
        self.assertFalse(original_cache.exists())
        self.assertTrue(Path(env['PS3_HDD1_ROOT']).is_dir())
        self.assertEqual({k:env[k] for k in metadata['mounts']},metadata['mounts'])

    def test_resume_rejects_redirected_hdd_or_title_mount(self):
        metadata,lock=self.profile('full');lock.close()
        hdd=Path(metadata['mounts']['PS3_HDD0_ROOT'])
        title=hdd/'game/NPUB30154';title.unlink();title.symlink_to(self.exdata,target_is_directory=True)
        with self.assertRaises(ValueError):self.profile('full',resume=True)
        title.unlink();title.symlink_to(self.content,target_is_directory=True)
        original=hdd/'home';moved=hdd/'private-home';original.rename(moved)
        original.symlink_to(self.source/'home',target_is_directory=True)
        with self.assertRaises(ValueError):self.profile('full',resume=True)


if __name__=='__main__':unittest.main()
