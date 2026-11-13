"""Linux CLI guardian: a killed Worker must not leave an unbounded MinerU process.

Executed by filename in a new session; deliberately has no application imports.
"""
import ctypes
import os
import signal
import subprocess
import sys


def main():
    expected_parent = int(sys.argv[1])

    def kill_group(*_):
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, kill_group)
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG is Linux-specific. Fail closed rather than run unguarded.
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot set MinerU parent-death signal')
    if os.getppid() != expected_parent:
        kill_group()
    child = subprocess.Popen(sys.argv[2:])
    return child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
