import unittest

from onethread import prompts, voice
from onethread.model import Task


def task(**kw):
    base = dict(id="T1", num=1, repo="app-a", title="Auth", branch="ot/T1-auth")
    base.update(kw)
    return Task(**base)


class PersonaTest(unittest.TestCase):
    CICERO = {"name": "Cicero", "style": "dry diplomat"}

    def test_plain_has_no_persona(self):
        for me in ("plain", "", None, " Plain "):
            self.assertEqual(prompts.orchestrator_persona(me, None, ""), "")
            self.assertEqual(prompts.task_persona(me, self.CICERO, "Sir"), "")

    def test_persona_keeps_guardrails(self):
        line = prompts.task_persona("Roman emperor", None, "Caesar")
        self.assertIn("Roman emperor", line)
        for rule in ("plain", "exact", "confirmation", "decides"):
            self.assertIn(rule, line)

    def test_real_person_marked_parody(self):
        self.assertIn("parody", prompts.orchestrator_persona("Donald Trump", None, ""))

    def test_work_prompt_includes_persona(self):
        repo = {"test_cmd": "make test"}
        plain = prompts.work_prompt(task(), repo)
        self.assertNotIn("You are Cicero", plain)
        line = prompts.task_persona("king", self.CICERO, "Your Majesty")
        named = prompts.work_prompt(task(), repo, persona=line)
        self.assertIn("You are Cicero (dry diplomat), one of king's people", named)
        self.assertIn("summary", named.split("You are Cicero")[1])


class PlainOffersTest(unittest.TestCase):
    def test_offer_keeps_facts(self):
        text = voice.offer("review_ready", task(), lead=True)
        for fact in ("Before we go on", "T1", "Auth", "app-a"):
            self.assertIn(fact, text)

    def test_offer_uses_cast_name(self):
        text = voice.offer("review_ready", task(cast_name="Cicero"))
        self.assertEqual(text, 'Cicero: "Auth" in app-a (T1) is ready for review. Review it now, or later?')
        for kind in voice.PLAIN:
            if kind != "lead":
                self.assertIn("Cicero: ", voice.offer(kind, task(cast_name="Cicero"), note="x"), kind)

    def test_dirty_worktree_offer(self):
        self.assertEqual(voice.offer("dirty_worktree", task()),
                         '"Auth" in app-a (T1)\'s worktree has uncommitted changes. '
                         "Delete it, or keep it?")

    def test_no_king_templates_left(self):
        self.assertFalse(hasattr(voice, "KING"))
        self.assertFalse(hasattr(prompts, "KING_PERSONA"))


if __name__ == "__main__":
    unittest.main()
