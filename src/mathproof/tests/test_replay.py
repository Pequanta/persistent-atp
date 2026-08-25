"""Independent replay: kernel verdicts over recorded scripts (6.11)."""

import unittest

from mathproof.maths_ai_atp import MathsAIFormalATP
from mathproof.replay import lean_replay_fn
from mathproof.tests.test_maths_ai_adapter import REQUEST

ENV = REQUEST["environment_hash"]


def certificate(**overrides):
    cert = {
        "artifact_hash": "sha256:" + "18" * 32,
        "status": "candidate",
        "producer_run_id": "p1/fr-1",
        "environment_hash": ENV,
        "goal_text": REQUEST["goal_text"],
        "tactics": [{"label": "exact", "arguments": ["Or.comm"]}],
    }
    cert.update(overrides)
    return cert


class FakeDriver:
    """Records the script; replays whatever script it is handed."""

    def __init__(self, remaining_after=0, fail_on=None):
        self.remaining_after = remaining_after
        self.fail_on = fail_on
        self.started = None
        self.scripts = []

    def start(self, goal_text):
        self.started = goal_text
        return ("state", 0)

    def run(self, state, tactic_text):
        if self.fail_on is not None and len(self.scripts) == self.fail_on:
            raise RuntimeError("tactic failed")
        self.scripts.append(tactic_text)
        return ("state", len(self.scripts))

    def goals_remaining(self, state):
        return self.remaining_after


def replay_fn(**driver_kwargs):
    return lean_replay_fn(lambda: FakeDriver(**driver_kwargs))


class TestLeanReplay(unittest.TestCase):
    def test_clean_script_that_closes_all_goals_verifies(self):
        driver = FakeDriver()
        verdict = lean_replay_fn(lambda: driver)(certificate(), ENV)
        self.assertEqual(
            verdict,
            {"status": "verified", "environment_hash": ENV, "sorry_detected": False},
        )
        self.assertEqual(driver.started, REQUEST["goal_text"])
        self.assertEqual(driver.scripts, ["exact Or.comm"])

    def test_environment_drift_is_rejected_before_any_lean_run(self):
        driver = FakeDriver()
        verdict = lean_replay_fn(lambda: driver)(
            certificate(environment_hash="sha256:" + "ff" * 32), ENV
        )
        self.assertEqual(verdict["status"], "rejected")
        self.assertTrue(verdict["rejection_reason"].startswith("environment-drift"))
        self.assertIsNone(driver.started)

    def test_certificate_without_recorded_script_is_rejected(self):
        bare = {
            k: v for k, v in certificate().items() if k not in ("goal_text", "tactics")
        }
        verdict = replay_fn()(bare, ENV)
        self.assertEqual(verdict["rejection_reason"], "certificate-payload-unavailable")

    def test_sorry_in_the_script_is_detected_without_running_lean(self):
        driver = FakeDriver()
        cert = certificate(tactics=[{"label": "sorry"}])
        verdict = lean_replay_fn(lambda: driver)(cert, ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertTrue(verdict["sorry_detected"])
        self.assertEqual(verdict["rejection_reason"], "sorry-detected-in-script")
        self.assertIsNone(driver.started)

    def test_admit_is_also_a_sorry(self):
        cert = certificate(tactics=[{"label": "exact", "arguments": ["admit"]}])
        verdict = replay_fn()(cert, ENV)
        self.assertTrue(verdict["sorry_detected"])

    def test_kernel_error_mid_script_names_the_step(self):
        cert = certificate(
            tactics=[{"label": "intro"}, {"label": "exact", "arguments": ["Or.comm"]}]
        )
        verdict = replay_fn(fail_on=1)(cert, ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertEqual(verdict["rejection_reason"], "replay-crash:RuntimeError")

    def test_goals_left_open_do_not_verify(self):
        verdict = replay_fn(remaining_after=2)(certificate(), ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertEqual(verdict["rejection_reason"], "goals-remaining")


class TestAxiomPolicy(unittest.TestCase):
    def test_native_decide_is_refused_before_any_lean_run(self):
        driver = FakeDriver()
        cert = certificate(tactics=[{"label": "native_decide"}])
        verdict = lean_replay_fn(lambda: driver)(cert, ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertEqual(verdict["rejection_reason"], "axiom-policy:native_decide")
        self.assertIsNone(driver.started)

    def test_user_declared_axiom_outside_the_standard_closure_is_refused(self):
        cert = certificate(
            lean_source="axiom myFake : False\ntheorem boom : False := myFake"
        )
        verdict = replay_fn()(cert, ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertEqual(verdict["rejection_reason"], "axiom-policy:user-axiom:myFake")

    def test_standard_closure_axioms_are_allowed_by_default(self):
        driver = FakeDriver()
        cert = certificate(lean_source="theorem c : p ∨ ¬p := Classical.em p")
        verdict = lean_replay_fn(lambda: driver)(cert, ENV)
        self.assertEqual(verdict["status"], "verified")

    def test_stricter_profile_can_deny_the_standard_closure_too(self):
        cert = certificate(
            lean_source="axiom propext : ∀ {a b : Prop}, (a ↔ b) → a = b"
        )
        verdict = replay_fn()(cert, ENV)
        self.assertEqual(verdict["status"], "verified")

        strict = lean_replay_fn(FakeDriver, allowed_axioms=frozenset())
        verdict = strict(cert, ENV)
        self.assertEqual(verdict["status"], "rejected")
        self.assertEqual(verdict["rejection_reason"], "axiom-policy:user-axiom:propext")

    def test_dotted_axiom_names_are_captured_whole(self):
        cert = certificate(lean_source="axiom Foo.helper : False")
        verdict = replay_fn()(cert, ENV)
        self.assertIn("Foo.helper", verdict["rejection_reason"])


class TestReplayThroughTheSeam(unittest.TestCase):
    def test_formal_replay_carries_the_real_verdict(self):
        atp = MathsAIFormalATP(replay_fn=lean_replay_fn(FakeDriver))
        verdict = atp.formal_replay(certificate(), ENV)
        self.assertEqual(verdict["status"], "verified")

    def test_search_certificates_carry_no_payload_until_the_worker_attaches_it(self):
        from mathproof.tests.test_maths_ai_adapter import REQUEST, StubReasoner, solved_graph

        atp = MathsAIFormalATP(reasoner=StubReasoner(solved_graph()), replay_fn=replay_fn())
        result = atp.formal_search_start(REQUEST)
        verdict = atp.formal_replay(result["certificate"], ENV)
        self.assertEqual(verdict["rejection_reason"], "certificate-payload-unavailable")


if __name__ == "__main__":
    unittest.main()
