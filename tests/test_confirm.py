import subprocess
import unittest

from onethread import confirm


class ConfirmTest(unittest.TestCase):
    def test_tty_uses_terminal_answer(self):
        asked = []
        c = confirm.make_confirm(isatty=lambda: True,
                                 ask=lambda q: asked.append(q) or "y",
                                 dialog=lambda q: self.fail("no dialog on a terminal"))
        self.assertTrue(c("Cancel T1?"))
        self.assertIn("Cancel T1?", asked[0])
        self.assertFalse(confirm.make_confirm(isatty=lambda: True, ask=lambda q: "")("x?"))

    def test_no_tty_uses_dialog(self):
        shown = []
        c = confirm.make_confirm(isatty=lambda: False, ask=lambda q: self.fail("no input"),
                                 dialog=lambda q: shown.append(q) or True)
        self.assertTrue(c("Merge T1?"))
        self.assertEqual(shown, ["Merge T1?"])

    def test_dialog_yes_only_on_yes_button(self):
        def run(argv, **kw):
            self.assertEqual(argv[0], "osascript")
            self.assertTrue(any('default button "No"' in a for a in argv))
            self.assertEqual(argv[-1], 'Cancel "T1"?')     # passed as data, not code
            return subprocess.CompletedProcess(argv, 0, stdout="button returned:Yes\n")
        self.assertTrue(confirm._dialog('Cancel "T1"?', run=run))
        no = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="button returned:No\n")  # noqa: E731
        self.assertFalse(confirm._dialog("Cancel T1?", run=no))

    def test_dialog_error_means_no(self):
        def missing(argv, **kw):
            raise FileNotFoundError("osascript")
        self.assertFalse(confirm._dialog("Cancel T1?", run=missing))
        failed = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="")  # noqa: E731
        self.assertFalse(confirm._dialog("Cancel T1?", run=failed))
