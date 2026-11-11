import concurrent.futures
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
TMP = Path(__file__).resolve().parents[3] / "evaluation" / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            self.api = importlib.import_module("order_mapping_core.sequence")
        except ModuleNotFoundError as exc:
            self.fail(f"编号契约尚未实现: {exc}")
        self.policy = {"confirmed": True, "prefix": "OR_", "daily_start": 0}

    def test_same_order_identity_reuses_number_across_revision_and_restart(self):
        store = self.api.NumberStore(self.root, self.policy)
        first = store.reserve("batch-a", "songbei", "2026-11-03")
        self.assertEqual(first, "OR_20261103000001")
        restarted = self.api.NumberStore(self.root, self.policy)
        self.assertEqual(restarted.reserve("batch-a", "songbei", "2026-11-03"), first)
        self.assertEqual(restarted.reserve("batch-a", "xingyue", "2026-11-03"), "OR_20261103000002")
        self.assertEqual(restarted.reserve("batch-a", "songbei", "2026-11-04"), "OR_20261104000001")

    def test_parallel_orders_do_not_duplicate_or_lose_reservations(self):
        def reserve(index):
            return self.api.NumberStore(self.root, self.policy).reserve(f"batch-{index}", "a", "2026-11-03")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(reserve, range(12)))
        self.assertEqual(len(set(values)), 12)
        self.assertEqual(sorted(int(x[-6:]) for x in values), list(range(1, 13)))

    def test_unconfirmed_policy_does_not_allocate_and_existing_max_is_respected(self):
        store = self.api.NumberStore(self.root, {**self.policy, "confirmed": False})
        with self.assertRaises(ValueError):
            store.reserve("b", "c", "2026-11-03")
        store = self.api.NumberStore(self.root, {**self.policy, "daily_start": 9})
        self.assertEqual(store.reserve("b", "c", "2026-11-03"), "OR_20261103000010")

    def test_publication_transaction_is_recoverable(self):
        store = self.api.NumberStore(self.root, self.policy)
        store.prepare("run-1", "digest-a", {"file": "candidate.xlsx"})
        self.assertEqual(store.publication("run-1")["status"], "prepared")
        restarted = self.api.NumberStore(self.root, self.policy)
        restarted.commit("run-1", "digest-a", {"file": "final.xlsx"})
        self.assertEqual(restarted.publication("run-1")["status"], "committed")
        self.assertEqual(restarted.publication("run-1")["payload"]["file"], "final.xlsx")

    def test_changed_digest_preserves_old_committed_revision(self):
        store = self.api.NumberStore(self.root, self.policy)
        store.prepare("run-1", "digest-a", {"file": "candidate-a.xlsx"})
        store.commit("run-1", "digest-a", {"file": "final-a.xlsx"})
        store.prepare("run-1", "digest-b", {"file": "candidate-b.xlsx"})
        self.assertEqual(store.publication("run-1")["status"], "prepared")
        self.assertEqual(store.publication_history("run-1"), [
            {"digest": "digest-a", "payload": {"file": "final-a.xlsx"}}])
        store.commit("run-1", "digest-b", {"file": "final-b.xlsx"})
        self.assertEqual(store.publication_history("run-1"), [
            {"digest": "digest-a", "payload": {"file": "final-a.xlsx"}},
            {"digest": "digest-b", "payload": {"file": "final-b.xlsx"}}])

    def test_commit_requires_matching_prepared_digest(self):
        store = self.api.NumberStore(self.root, self.policy)
        store.prepare("run-1", "digest-a", {"file": "candidate.xlsx"})
        with self.assertRaises(ValueError):
            store.commit("run-1", "wrong-digest", {"file": "wrong.xlsx"})
        self.assertEqual(store.publication("run-1")["status"], "prepared")
        store.commit("run-1", "digest-a", {"file": "final.xlsx"})
        with self.assertRaises(ValueError):
            store.commit("run-1", "digest-a", {"file": "overwritten.xlsx"})
        store.prepare("run-1", "digest-a", {"file": "final.xlsx"})
        self.assertEqual(store.publication("run-1")["payload"], {"file": "final.xlsx"})
        self.assertEqual(len(store.publication_history("run-1")), 1)

    def test_same_digest_can_publish_replacement_for_damaged_export(self):
        store = self.api.NumberStore(self.root, self.policy)
        store.prepare("run-1", "digest-a", {"file": "old-candidate.xlsx"})
        store.commit("run-1", "digest-a", {"file": "old-final.xlsx"})
        store.prepare("run-1", "digest-a", {"file": "new-final.xlsx"})
        self.assertEqual(store.publication("run-1")["status"], "prepared")
        store.commit("run-1", "digest-a", {"file": "new-final.xlsx"})
        self.assertEqual(store.publication_history("run-1"), [
            {"digest": "digest-a", "payload": {"file": "old-final.xlsx"}},
            {"digest": "digest-a", "payload": {"file": "new-final.xlsx"}}])


if __name__ == "__main__":
    unittest.main()
