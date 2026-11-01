import os
import stat
import subprocess
import sys
import unittest

from onethread import leases
from onethread.store import Store
from tests.helpers import TempHomeCase, make_repo


def write_exe(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class ClassifyTest(unittest.TestCase):
    def test_costly_commands_match_their_class(self):
        c = leases.DEFAULT_CLASSES
        self.assertEqual(leases.classify(["xcodebuild", "test"], c), "xcode")
        self.assertEqual(leases.classify(["xcrun", "simctl", "boot", "X"], c), "xcode")
        self.assertEqual(leases.classify(["swift", "build"], c), "build")
        self.assertEqual(leases.classify(["npm", "run", "build"], c), "build")

    def test_cheap_commands_match_nothing(self):
        c = leases.DEFAULT_CLASSES
        for argv in (["xcrun", "--find", "clang"], ["swift", "--version"],
                     ["git", "status"], ["npm", "install"]):
            self.assertIsNone(leases.classify(argv, c), argv)

    def test_shim_names_are_bare_command_names(self):
        names = leases.shim_names(leases.DEFAULT_CLASSES)
        self.assertIn("xcodebuild", names)
        self.assertIn("xcrun", names)
        self.assertNotIn("./gradlew", names)


class BrokerTest(TempHomeCase):
    def setUp(self):
        super().setUp()
        self.procs = []
        self.broker = leases.Broker(self.store)

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        super().tearDown()

    def live_pid(self):
        p = subprocess.Popen(["sleep", "30"])
        self.procs.append(p)
        return p.pid

    def test_limit_one_makes_second_wait_until_release(self):
        first = self.broker.request("xcode", "T1", self.live_pid(), "xcodebuild", 2)
        self.assertTrue(self.broker.try_grant(first))
        second = self.broker.request("xcode", "T2", self.live_pid(), "xcodebuild", 2)
        self.assertFalse(self.broker.try_grant(second))
        self.assertIn("T1", self.broker.wait_message(second))
        self.assertEqual(self.broker.busy_classes(), {"xcode"})
        self.broker.release(first)
        self.assertTrue(self.broker.try_grant(second))

    def test_waiting_requests_are_served_urgent_first(self):
        holder = self.broker.request("xcode", "T1", self.live_pid(), "x", 2)
        self.broker.try_grant(holder)
        low = self.broker.request("xcode", "T2", self.live_pid(), "x", 3)
        urgent = self.broker.request("xcode", "T3", self.live_pid(), "x", 0)
        self.broker.release(holder)
        self.assertFalse(self.broker.try_grant(low))
        self.assertTrue(self.broker.try_grant(urgent))

    def test_dead_holder_lease_is_reclaimed(self):
        p = subprocess.Popen(["sleep", "30"])
        dead = self.broker.request("xcode", "T1", p.pid, "x", 2)
        self.broker.try_grant(dead)
        p.kill()
        p.wait()
        nxt = self.broker.request("xcode", "T2", self.live_pid(), "x", 2)
        self.assertTrue(self.broker.try_grant(nxt))

    def test_long_held_lease_becomes_attention_item(self):
        self.store.add_repo(make_repo(self.tmp, "app-a"))
        self.store.add_task("app-a", "Build")
        lid = self.broker.request("xcode", "T1", self.live_pid(), "x", 2)
        self.broker.try_grant(lid)
        leases.check_overdue(self.store, self.store, max_seconds=-1)
        items = self.store.open_attention()
        self.assertEqual([(i["task"], i["kind"], i["note"]) for i in items],
                         [("T1", "lease_overdue", "xcode")])


class ShimTest(TempHomeCase):
    """Runs the real stand-in scripts against fake 'real' binaries."""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        write_exe(os.path.join(self.bin, "xcodebuild"), 'echo "real xcodebuild $*"; exit 7')
        write_exe(os.path.join(self.bin, "xcrun"), 'echo "real xcrun $*"; exit 3')
        self.shims = leases.install_shims(self.home, leases.DEFAULT_CLASSES)
        self.env = dict(os.environ, **leases.worker_env(self.home, "T1", 2,
                                                        self.shims, wait=0.5))
        self.env["PATH"] = f"{self.shims}:{self.bin}:/usr/bin:/bin"
        self.procs = []
        self.lstore = Store(leases.lease_db(self.home))

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        self.lstore.close()
        super().tearDown()

    def run_shim(self, *argv):
        return subprocess.run(list(argv), env=self.env, capture_output=True,
                              text=True, timeout=20)

    def test_install_removes_anything_planted_in_the_shims_folder(self):
        planted = os.path.join(self.shims, "git")
        write_exe(planted, "echo planted")
        with open(os.path.join(self.shims, "ot-lease"), "w") as f:
            f.write("#!/bin/sh\necho tampered\n")
        leases.install_shims(self.home, leases.DEFAULT_CLASSES)
        self.assertFalse(os.path.exists(planted))
        with open(os.path.join(self.shims, "ot-lease")) as f:
            self.assertIn("lease_cli.py", f.read())

    def test_stand_in_runs_real_binary_with_same_args_and_exit_code(self):
        res = self.run_shim("xcodebuild", "-scheme", "App", "test")
        self.assertEqual(res.returncode, 7)
        self.assertIn("real xcodebuild -scheme App test", res.stdout)
        held = self.lstore.q("SELECT * FROM leases WHERE state='held'")
        self.assertEqual(held, [])
        self.assertEqual(len(self.lstore.q("SELECT * FROM leases")), 1)

    def test_unmatched_command_runs_without_asking(self):
        res = self.run_shim("xcrun", "--find", "clang")
        self.assertEqual(res.returncode, 3)
        self.assertIn("real xcrun --find clang", res.stdout)
        self.assertEqual(self.lstore.q("SELECT * FROM leases"), [])

    def test_waiting_too_long_exits_75_with_retry_message(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.procs.append(sleeper)
        broker = leases.Broker(self.lstore)
        broker.try_grant(broker.request("xcode", "T9", sleeper.pid, "xcodebuild", 0))
        res = self.run_shim("xcodebuild", "build")
        self.assertEqual(res.returncode, 75)
        self.assertIn("Not now", res.stderr)
        self.assertIn("T9", res.stderr)
        self.assertIn("retry the same command later", res.stderr)
        self.assertNotIn("real xcodebuild", res.stdout)

    def test_ot_lease_marks_any_command_heavy(self):
        res = self.run_shim("ot-lease", "heavy", "--", "sh", "-c", "echo hi; exit 4")
        self.assertEqual(res.returncode, 4)
        self.assertIn("hi", res.stdout)
        rows = self.lstore.q("SELECT cls, state FROM leases")
        self.assertEqual([(r["cls"], r["state"]) for r in rows], [("heavy", "released")])

    def test_worker_env_puts_shims_first_on_path(self):
        env = leases.worker_env(self.home, "T1", 2, self.shims)
        self.assertTrue(env["PATH"].startswith(self.shims + os.pathsep))
        self.assertEqual(env["ONETHREAD_TASK"], "T1")


if __name__ == "__main__":
    unittest.main()
