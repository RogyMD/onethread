"""Shared test helpers: temporary homes and throwaway git repositories."""
import os
import subprocess
import tempfile
import unittest

from onethread.store import Store


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(parent, name):
    """Create a git repo with one commit on branch main."""
    path = os.path.join(parent, name)
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write(f"# {name}\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return path


class TempHomeCase(unittest.TestCase):
    """Each test gets its own ONETHREAD home and a fresh Store."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = os.path.realpath(self._tmp.name)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.store = Store(os.path.join(self.home, "state.db"))
        self.store.set_meta("notify", "0")      # never post a real banner from a test

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = Store(os.path.join(self.home, "state.db"))
        return self.store
