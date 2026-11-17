"""Create or reopen isolated native save/license mounts without exposing keys."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from private_files import restrict_private


def is_link(path):
    """Include Windows junctions, which Python 3.11 does not call symlinks."""
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def content_link(path, target):
    if os.name != 'nt':
        path.symlink_to(target, target_is_directory=True)
        return
    # Junctions need no Developer Mode/admin privilege. Reject cmd metacharacters.
    if any(c in str(path)+str(target) for c in '&|<>^%"!\r\n'):
        raise ValueError('Unsupported shell metacharacter in content junction path')
    subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(path), str(target)],
                   check=True, capture_output=True)


def lock_profile(lock):
    if os.name == 'nt':
        import msvcrt
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write('0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError('Profile is already in use') from error
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

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
    if is_link(directory):
        raise ValueError('Private profile mount must not be a symlink')
    if directory.exists():
        for path in directory.rglob('*'):
            if is_link(path) or path.is_file() and path.stat().st_nlink != 1:
                raise ValueError('Private profile contains a linked file: '+str(path))


def open_profile(path, source_hdd, content, title_id, mode='purchased', resume=False):
    """Return sanitized mount metadata and an exclusive lock kept for the session."""
    path, source_hdd, content = map(Path, (path, source_hdd, content))
    if mode not in ('purchased', 'missing-unlock'):
        raise ValueError('Unsupported entitlement mode')
    if is_link(path) or any(is_link(p) for p in path.parents):
        raise ValueError('Profile path must not traverse a symlink')
    if path.resolve() == source_hdd.resolve() or path.resolve().is_relative_to(source_hdd.resolve()):
        raise ValueError('Profile must be separate from the original HDD')
    if resume:
        if not path.is_dir():
            raise ValueError('Resume requires an existing profile')
    else:
        path.mkdir(parents=True, exist_ok=False)
    lock_path = path / '.session.lock'
    if is_link(lock_path) or lock_path.exists() and lock_path.stat().st_nlink != 1:
        raise ValueError('Invalid profile lock')
    lock = lock_path.open('a')
    try:
        lock_profile(lock)
        manifest_path = path / 'profile.json'
        hdd = path / 'dev_hdd0'
        manifest = dict(version=1, title_id=title_id, entitlement=mode, content=str(content.resolve()))
        if resume:
            if is_link(manifest_path) or json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('Profile title/content/entitlement mismatch')
        else:
            (hdd / 'game').mkdir(parents=True)
            content_link(hdd / 'game' / title_id, content.resolve())
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
                        restrict_private(exdata / name)
            manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        for parent in (hdd, hdd/'game'):
            if is_link(parent):
                raise ValueError('HDD/game mount must not be a symlink')
        title = hdd / 'game' / title_id
        if not is_link(title) or title.resolve() != content.resolve():
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
