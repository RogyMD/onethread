"""Entry point for lease stand-ins and `ot-lease`.

  <shim> --shim NAME -- ARGS...   run NAME ARGS, with a lease if it is costly
  ot-lease CLASS -- CMD ARGS...   run CMD ARGS holding a CLASS lease
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from onethread import leases  # noqa: E402
from onethread.store import Store  # noqa: E402


def real_binary(name):
    shims = os.environ.get("ONETHREAD_SHIMS", "")
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and os.path.realpath(p) != os.path.realpath(shims or "/nonexistent"))
    return shutil.which(name, path=path)


def main(argv):
    if argv[:1] == ["--shim"]:
        cmd = [argv[1], *argv[3:]]
        cls_name = None
    elif len(argv) >= 3 and argv[1] == "--":
        cls_name, cmd = argv[0], argv[2:]
    else:
        print("usage: ot-lease CLASS -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    real = real_binary(cmd[0])
    if not real:
        print(f"ot-lease: command not found: {cmd[0]}", file=sys.stderr)
        return 127
    home = os.environ.get("ONETHREAD_HOME")
    if not home:                                    # not inside a worker
        return subprocess.call([real, *cmd[1:]])
    store = Store(leases.lease_db(home))
    classes = leases.load_classes(store)
    cls_name = cls_name or leases.classify(cmd, classes)
    if cls_name is None:
        store.close()
        return subprocess.call([real, *cmd[1:]])
    broker = leases.Broker(store, classes)
    say = lambda msg: print(f"[onethread] {msg}", file=sys.stderr, flush=True)  # noqa: E731
    lease_id = broker.acquire(cls_name, os.environ.get("ONETHREAD_TASK", "?"),
                              int(os.environ.get("ONETHREAD_RANK", "2")), " ".join(cmd),
                              os.getpid(), float(os.environ.get("ONETHREAD_LEASE_WAIT",
                                                                leases.DEFAULT_WAIT)),
                              on_wait=say)
    if lease_id is None:
        last = broker.store.q("SELECT id FROM leases WHERE pid=? ORDER BY id DESC LIMIT 1",
                              (os.getpid(),))
        say(broker.wait_message(last[0]["id"]) if last else "Not now.")
        say("Still busy: retry the same command later (exit 75). Do other work meanwhile if you can.")
        store.close()
        return leases.EXIT_RETRY
    try:
        return subprocess.call([real, *cmd[1:]])
    finally:
        broker.release(lease_id)
        store.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
