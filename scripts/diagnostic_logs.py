"""Keep retained diagnostic logs readable without touching game/license data."""
import argparse
import os
from pathlib import Path
import stat


def normalize_log_file(path):
    path=Path(path)
    try:info=path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(info.st_mode):return False
    if stat.S_IMODE(info.st_mode)==0o644:return False
    path.chmod(0o644)
    return True


def normalize_log_permissions(directory):
    changed=[]
    for path in Path(directory).rglob('*'):
        if not (path.name.endswith(('.log','.log.gz')) or path.name.startswith('RPCS3.log')):
            continue
        if normalize_log_file(path):changed.append(str(path))
    return changed


def open_log(path, mode='wb'):
    stream=Path(path).open(mode)
    os.fchmod(stream.fileno(),0o644)
    return stream


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    changed=normalize_log_permissions(args.directory)
    print(f'Restored readable log permissions: {len(changed)} file(s)')
