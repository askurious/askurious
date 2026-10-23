"""Hold the volume's exclusive storage gate until the controlling backup script releases stdin."""
import argparse
import fcntl
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('root')
parser.add_argument('--timeout', type=float, default=120)
parser.add_argument('--release-file')
parser.add_argument('--max-hold', type=float, default=90)
args = parser.parse_args()
with (Path(args.root) / '.storage-backup.lock').open('a+b') as handle:
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                sys.exit('Storage is busy: no consistent backup window acquired.')
            time.sleep(0.1)
    print('READY', flush=True)
    if args.release_file:
        deadline = time.monotonic() + args.max_hold
        while not Path(args.release_file).exists():
            if time.monotonic() >= deadline:
                sys.exit('Backup window exceeded its time limit; no complete backup may be acknowledged.')
            time.sleep(0.1)
    else:
        sys.stdin.readline()
