"""Process liveness that survives pid reuse: a pid only counts as the same
process when its start time matches what we recorded."""
import os
import subprocess


def _ps(field, pid):
    return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()


def process_start(pid):
    return _ps("lstart", pid)


def alive(raw):
    """True when a "pid:start" record names a live process."""
    if not raw:
        return False
    pid, start = raw.split(":", 1)
    return pid_alive(int(pid), start)


def pid_alive(pid, started):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    stat = _ps("stat", pid)
    return bool(stat) and not stat.startswith("Z") and process_start(pid) == started
