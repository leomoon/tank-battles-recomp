"""Create or reopen isolated native save/license mounts without exposing keys."""
import fcntl
import json
from pathlib import Path
import shutil

UNLOCK = 'UP2006-NPUB30154_00-TANKBATTLES00KEY.edat'


def entitlement_files(directory):
    unlock = Path(directory) / UNLOCK
    if unlock.is_file() and not unlock.is_symlink():
        with unlock.open('rb') as stream:
            header = stream.read(12)
        if header[:4] == b'NPD\0' and header[8:12] == b'\0\0\0\3':
            return (UNLOCK,)  # Runtime still authenticates the complete EDAT.
    return (UNLOCK, UNLOCK+'.rifkey')


def validate_private_tree(directory):
    """Save/license writes must never escape through links or shared inodes."""
    if directory.is_symlink():
        raise ValueError('Private profile mount must not be a symlink')
    if directory.exists():
        for path in directory.rglob('*'):
            if path.is_symlink() or path.is_file() and path.stat().st_nlink != 1:
                raise ValueError('Private profile contains a linked file: '+str(path))


def open_profile(path, source_hdd, content, title_id, mode='purchased', resume=False):
    """Return sanitized mount metadata and an exclusive lock kept for the session."""
    path, source_hdd, content = map(Path, (path, source_hdd, content))
    if mode not in ('purchased', 'missing-unlock'):
        raise ValueError('Unsupported entitlement mode')
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('Profile path must not traverse a symlink')
    if path.resolve() == source_hdd.resolve() or path.resolve().is_relative_to(source_hdd.resolve()):
        raise ValueError('Profile must be separate from the original HDD')
    if resume:
        if not path.is_dir():
            raise ValueError('Resume requires an existing profile')
    else:
        path.mkdir(parents=True, exist_ok=False)
    lock_path = path / '.session.lock'
    if lock_path.is_symlink() or lock_path.exists() and lock_path.stat().st_nlink != 1:
        raise ValueError('Invalid profile lock')
    lock = lock_path.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_path = path / 'profile.json'
        hdd = path / 'dev_hdd0'
        manifest = dict(version=1, title_id=title_id, entitlement=mode, content=str(content.resolve()))
        if resume:
            if manifest_path.is_symlink() or json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('Profile title/content/entitlement mismatch')
        else:
            (hdd / 'game').mkdir(parents=True)
            (hdd / 'game' / title_id).symlink_to(content.resolve(), target_is_directory=True)
            exdata = hdd / 'home/00000001/exdata'
            exdata.mkdir(parents=True)
            if mode == 'purchased':
                source_exdata = source_hdd / 'home/00000001/exdata'
                for name in entitlement_files(source_exdata):
                    source = source_hdd / 'home/00000001/exdata' / name
                    if source.is_symlink() or not source.is_file():
                        raise ValueError('Purchased profile requires regular local unlock and RIF sidecar files')
                    shutil.copyfile(source, exdata / name)
                    if name.endswith('.rifkey'):
                        (exdata / name).chmod(0o600)
            manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        for parent in (hdd, hdd/'game'):
            if parent.is_symlink():
                raise ValueError('HDD/game mount must not be a symlink')
        title = hdd / 'game' / title_id
        if not title.is_symlink() or title.resolve() != content.resolve():
            raise ValueError('Unexpected title content mount')
        for private in (hdd/'home', path/'dev_hdd1'):
            validate_private_tree(private)
        exdata = hdd / 'home/00000001/exdata'
        present = [(exdata/name).is_file() for name in (UNLOCK, UNLOCK+'.rifkey')]
        if mode == 'missing-unlock' and any(present) or mode == 'purchased' and not all((exdata/name).is_file() for name in entitlement_files(exdata)):
            raise ValueError('Profile entitlement files disagree with selected mode')
        mounts = dict(PS3_VFS_ROOT=str(content.resolve()), PS3_GAMEDATA_ROOT=str(path.resolve()),
                      PS3_HDD0_ROOT=str(hdd.resolve()), PS3_HDD1_ROOT=str((path/'dev_hdd1').resolve()))
        return dict(**manifest, path=str(path.resolve()), operation='resume' if resume else 'create', mounts=mounts,
                    shared_content=True, private_saves=True, private_entitlement=True), lock
    except BaseException:
        lock.close()
        raise
