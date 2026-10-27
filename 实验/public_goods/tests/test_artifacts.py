"""Compaction must preserve evidence and keep saved-run consumers working."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from public_goods import artifacts
from public_goods.agents.agent_bc import BRunner
from public_goods.environment.world import World
from public_goods.evaluation.preflight import audit_saved_run
from public_goods.models.client import MockModel


class ArtifactTests(unittest.TestCase):
    def test_full_run_replays_and_scores_after_compaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "trial"
            task = BRunner(World("compact", ["a", "b", "c", "d"]), MockModel(), output=root / "B")
            task.run()
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            artifacts.compact_run(root)
            self.assertEqual({p.name for p in root.iterdir()}, {"records.zip"})
            for name, data in before.items():
                self.assertEqual(artifacts.read_bytes(root / name), data)
            self.assertTrue(audit_saved_run(root / "B")["passed"])
            destination = Path(temporary) / "copied"
            artifacts.copy_tree(root / "B", destination)
            self.assertTrue(audit_saved_run(destination)["passed"])

    def test_preserves_new_reports_when_compacted_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "report.json").write_text('{"status":"completed"}')
            (root / "calls.jsonl").write_text('{"action":0}\n')
            artifacts.compact_run(root)
            (root / "audit.json").write_text('{"passed":true}')
            artifacts.compact_run(root)
            artifacts.compact_run(root)
            self.assertTrue(artifacts.read_json(root / "audit.json")["passed"])
            self.assertEqual(artifacts.read_rows(root / "calls.jsonl"), [{"action": 0}])
            self.assertEqual({p.name for p in root.iterdir()}, {"report.json", "records.zip"})

    def test_damaged_existing_archive_does_not_delete_new_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "records.zip").write_bytes(b"broken")
            (root / "calls.jsonl").write_text("evidence")
            with self.assertRaises(Exception):
                artifacts.compact_run(root)
            self.assertEqual((root / "calls.jsonl").read_text(), "evidence")
            self.assertEqual((root / "records.zip").read_bytes(), b"broken")

    def test_history_members_keep_old_logical_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ZipFile(root / "history.zip", "w", ZIP_DEFLATED) as handle:
                handle.writestr("old/B/config.json", '{"implementation":"B"}')
            self.assertEqual(artifacts.read_json(root / "old/B/config.json"), {"implementation": "B"})
            self.assertFalse(artifacts.exists(root / "old/B/absent.json"))

    def test_cli_compacts_failed_new_run_but_never_existing_directory(self):
        @artifacts.compact_cli
        def command(argv):
            root = Path(argv[1])
            root.mkdir(exist_ok=False)
            (root / "calls.jsonl").write_text('{"status":"failed"}\n')
            raise RuntimeError("inference failed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "failed"
            with self.assertRaises(RuntimeError):
                command(["--output", str(root)])
            self.assertTrue(artifacts.exists(root / "calls.jsonl"))
            self.assertFalse((root / "calls.jsonl").exists())
            with patch.object(artifacts, "compact_run") as compact:
                with self.assertRaises(FileExistsError):
                    command(["--output", str(root)])
                compact.assert_not_called()
