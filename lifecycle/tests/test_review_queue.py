#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "ao-review-queue"
SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ao-review-queue-test-")
        self.state = Path(self.tmp.name) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def runq(self, *args):
        env = {**os.environ, "AO_REVIEW_QUEUE_STATE_DIR": str(self.state)}
        return subprocess.run([str(HELPER), *map(str, args)], text=True, capture_output=True, env=env)

    def request(self, n, sha, orch=None, resume=False):
        args = [
            "request", "--review-key", f"owner/repo#{n}@{sha}",
            "--repository", "owner/repo", "--pr-number", str(n),
            "--pr-url", f"https://github.com/owner/repo/pull/{n}",
            "--expected-head", sha, "--orchestrator-session-id", orch or f"orch-{n}",
            "--worker-session-id", f"worker-{n}", "--repair-cycle", "0",
        ]
        if resume:
            args.append("--resume-attempt")
        result = self.runq(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_first_request_granted_second_queued_fifo(self):
        self.assertEqual(self.request(1, SHA1)["status"], "granted")
        second = self.request(2, SHA2)
        third = self.request(3, SHA3)
        self.assertEqual((second["status"], second["position"]), ("queued", 1))
        self.assertEqual((third["status"], third["position"]), ("queued", 2))
        released = json.loads(self.runq("release", 1).stdout)
        self.assertEqual(released["next"]["ticketId"], 2)
        self.assertEqual(set(released["next"]), {"ticketId", "reviewKey", "orchestratorSessionId"})
        released = json.loads(self.runq("release", 2).stdout)
        self.assertEqual(released["next"]["ticketId"], 3)

    def test_same_review_key_is_idempotent_for_same_orchestrator(self):
        first = self.request(1, SHA1, orch="orch")
        again = self.request(1, SHA1, orch="orch")
        self.assertEqual(first["ticketId"], again["ticketId"])
        self.assertTrue(again["existing"])

    def test_same_review_key_different_orchestrator_fails_closed(self):
        self.request(1, SHA1, orch="orch-a")
        result = self.runq(
            "request", "--review-key", f"owner/repo#1@{SHA1}", "--repository", "owner/repo",
            "--pr-number", "1", "--pr-url", "https://github.com/owner/repo/pull/1",
            "--expected-head", SHA1, "--orchestrator-session-id", "orch-b",
            "--worker-session-id", "worker-1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different orchestrator", result.stderr)

    def test_cancel_pending_preserves_active_and_order(self):
        self.request(1, SHA1); self.request(2, SHA2); self.request(3, SHA3)
        cancelled = json.loads(self.runq("cancel", 2).stdout)
        self.assertFalse(cancelled["wasActive"])
        released = json.loads(self.runq("release", 1).stdout)
        self.assertEqual(released["next"]["ticketId"], 3)

    def test_cancel_active_promotes_next_for_manual_recovery(self):
        self.request(1, SHA1); self.request(2, SHA2)
        cancelled = json.loads(self.runq("cancel", 1).stdout)
        self.assertTrue(cancelled["wasActive"])
        self.assertEqual(cancelled["next"]["ticketId"], 2)

    def test_resume_reenters_at_back_after_release(self):
        self.request(1, SHA1, orch="orch-a"); self.request(2, SHA2, orch="orch-b")
        released = json.loads(self.runq("release", 1).stdout)
        self.assertEqual(released["next"]["ticketId"], 2)
        resumed = self.request(1, SHA1, orch="orch-a", resume=True)
        self.assertEqual((resumed["status"], resumed["position"]), ("queued", 1))
        status = json.loads(self.runq("status").stdout)
        self.assertTrue(status["pending"][0]["resumeAttempt"])

    def test_concurrent_requests_have_exactly_one_grant(self):
        requests = [(1, SHA1), (2, SHA2), (3, SHA3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda x: self.request(*x), requests))
        self.assertEqual(sum(r["status"] == "granted" for r in results), 1)
        status = json.loads(self.runq("status").stdout)
        self.assertEqual(len(status["pending"]), 2)
        ids = [status["active"]["ticketId"], *[t["ticketId"] for t in status["pending"]]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ticket_scoped_status_avoids_full_queue_output(self):
        self.request(1, SHA1); second = self.request(2, SHA2)
        scoped = json.loads(self.runq("status", second["ticketId"]).stdout)
        self.assertEqual(scoped["status"], "queued")
        self.assertEqual(scoped["ticket"]["reviewKey"], f"owner/repo#2@{SHA2}")
        self.assertNotIn("pending", scoped)
        self.assertNotIn("active", scoped)

    def test_malformed_state_fails_closed(self):
        self.state.mkdir(parents=True)
        (self.state / "state.json").write_text("{}\n")
        result = self.runq("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed", result.stderr)

    def test_release_wrong_ticket_fails_without_mutation(self):
        self.request(1, SHA1); self.request(2, SHA2)
        result = self.runq("release", 2)
        self.assertNotEqual(result.returncode, 0)
        status = json.loads(self.runq("status").stdout)
        self.assertEqual(status["active"]["ticketId"], 1)
        self.assertEqual(status["pending"][0]["ticketId"], 2)


if __name__ == "__main__":
    unittest.main()
