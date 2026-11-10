"""Public CLI regressions for resumable dream processing and publication."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "scripts" / "wiki"
TEMP_ROOT = ROOT / ".agents" / "test-tmp"


class DreamWorkflowCLI(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.root = Path(self.tmp.name) / "wiki"
        (self.root / ".raw" / "batch").mkdir(parents=True)
        for folder in ("facts", "solutions", "reports"):
            (self.root / folder).mkdir()
        self.env = dict(os.environ, WIKI_HOME=str(self.root))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        (self.root / "README.md").write_text("base\n")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        self.transcript = self.root / ".raw" / "batch" / "t.txt"
        self.transcript.write_text("transcript body\n")
        manifest = {"batch_id": "batch-one", "source": "input", "source_boundary": "2026-11-10T00:00:00+00:00", "source_hash": "abc", "transcripts": ["t.txt"]}
        (self.transcript.parent / "manifest.json").write_text(json.dumps(manifest))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, input=None):
        return subprocess.run(["bash", str(WIKI), "dream", *args], env=self.env, input=input, capture_output=True, text=True)

    def begin(self):
        result = self.cli("begin", "--batch", str(self.transcript.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / ".dream" / "workflow.json").read_text())

    def finish_transcript(self, gaps=""):
        path = str(self.transcript)
        first = self.cli("read", "--thread", path, "--limit", "5")
        self.assertEqual(first.stdout, "trans")
        self.assertEqual(self.cli("ack", "--thread", path).returncode, 0)
        remainder = self.cli("read", "--thread", path)
        self.assertEqual(remainder.stdout, "cript body\n")
        self.assertEqual(self.cli("ack", "--thread", path).returncode, 0)
        complete_args = ["complete", "--thread", path, "--status", "analyzed", "--source-complete"]
        if gaps:
            complete_args.extend(["--gaps", gaps])
        result = self.cli(*complete_args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def own_fact(self, rel="facts/known.md"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nsource: thread-1\nstatus: open\n---\n\nA reviewed fact.\n")
        result = self.cli("own", input=rel + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unacknowledged_read_replays_identical_chunk_and_caps_limit(self):
        self.begin()
        args = ("read", "--thread", str(self.transcript), "--limit", "5")
        first = self.cli(*args)
        again = self.cli(*args)
        self.assertEqual(first.stdout, "trans")
        self.assertEqual(again.stdout, first.stdout)
        self.assertNotEqual(self.cli("read", "--thread", str(self.transcript), "--limit", "999999").returncode, 0)
        self.assertNotEqual(self.cli("complete", "--thread", str(self.transcript), "--status", "analyzed", "--source-complete").returncode, 0)

    def test_transcript_change_after_analysis_rejects_commit(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        self.transcript.write_text("changed after analysis")
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)

    def test_scope_traversal_readme_and_promoted_paths_rejected(self):
        self.begin()
        for rel in ("README.md", "facts/../README.md", "facts/promoted.md", "AGENTS.md", ".dream/config.md"):
            result = self.cli("own", input=rel + "\n")
            self.assertNotEqual(result.returncode, 0, rel)

    def test_preexisting_dirty_and_staged_paths_cannot_be_adopted(self):
        path = self.root / "facts" / "dirty.md"
        path.write_text("preexisting dirty\n")
        staged = self.root / "solutions" / "staged.md"
        staged.write_text("staged\n")
        subprocess.run(["git", "-C", str(self.root), "add", "solutions/staged.md"], check=True)
        self.begin()
        self.assertNotEqual(self.cli("own", input="facts/dirty.md\n").returncode, 0)
        self.assertNotEqual(self.cli("own", input="solutions/staged.md\n").returncode, 0)

    def test_mixed_owned_staged_and_unstaged_rejected_without_losing_index(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        fact = self.root / "facts" / "known.md"
        fact.write_text(fact.read_text() + "staged\n")
        subprocess.run(["git", "-C", str(self.root), "add", "facts/known.md"], check=True)
        fact.write_text(fact.read_text() + "unstaged\n")
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)

    def test_report_no_facts_checkpoint_commit_retry_and_idempotence(self):
        self.begin()
        self.finish_transcript()
        first = self.cli("commit")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        receipt = json.loads(first.stdout.strip().splitlines()[-1])
        report = self.root / receipt["report"]
        content = report.read_text()
        self.assertIn("Analyzed: 1", content)
        self.assertIn("Skipped: 0", content)
        self.assertEqual(receipt["report_hash"], __import__("hashlib").sha256(report.read_bytes()).hexdigest())
        head = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        state_path = self.root / ".dream" / "workflow.json"
        state = json.loads(state_path.read_text())
        state["phase"] = "committing"
        state.pop("completed", None)
        state.pop("commit", None)
        state_path.write_text(json.dumps(state))
        (self.root / ".dream" / "receipts" / f"{receipt['run']}.json").unlink()
        (self.root / ".dream" / "processed-batches" / "batch-one.json").unlink()
        reconciled = self.cli("commit")
        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        recovered_receipt = json.loads(reconciled.stdout.strip().splitlines()[-1])
        self.assertEqual(recovered_receipt["commit"], receipt["commit"])
        self.assertEqual(recovered_receipt["report_hash"], receipt["report_hash"])
        retry = self.cli("commit")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        retry_receipt = json.loads((self.root / ".dream" / "receipts" / f"{receipt['run']}.json").read_text())
        self.assertEqual(retry_receipt, recovered_receipt)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip(), head)
        self.assertFalse((self.root / ".dream" / f"index-{receipt['run']}").exists())
        self.assertFalse((self.root / ".dream" / "last-run-input").exists(), "source-independent input must not change watermarks")
        self.assertNotEqual(self.cli("begin", "--batch", str(self.transcript.parent)).returncode, 0)

    def test_verify_failure_keeps_run_pending_and_creates_no_commit(self):
        self.begin()
        self.finish_transcript()
        fact = self.root / "facts" / "invalid.md"
        fact.write_text("---\nstatus: impossible\nsource: thread-1\n---\n\nBad schema.\n")
        self.assertEqual(self.cli("own", input="facts/invalid.md\n").returncode, 0)
        head = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        state = json.loads((self.root / ".dream" / "workflow.json").read_text())
        self.assertEqual(state["phase"], "processing")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip(), head)

    def test_lint_failure_keeps_run_resumable(self):
        self.begin()
        self.finish_transcript()
        fact = self.root / "facts" / "thin.md"
        fact.write_text("---\ntitle: Thin\nslug: thin\nstatus: open\nsource: thread-1\nrecurrence: 1\n---\n\nShort.\n")
        self.assertEqual(self.cli("own", input="facts/thin.md\n").returncode, 0)
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wiki lint failed", result.stderr)
        state = json.loads((self.root / ".dream" / "workflow.json").read_text())
        self.assertEqual(state["phase"], "processing")

    def test_skipped_transcript_has_reason_and_is_reported(self):
        self.begin()
        result = self.cli("complete", "--thread", str(self.transcript), "--status", "skipped", "--gaps", "dream job transcript excluded")
        self.assertEqual(result.returncode, 0, result.stderr)
        committed = self.cli("commit")
        self.assertEqual(committed.returncode, 0, committed.stderr + committed.stdout)
        report = next((self.root / "reports").glob("*.dream.md")).read_text()
        self.assertIn("Skipped: 1", report)
        self.assertIn("dream job transcript excluded", report)
        self.assertIn("Unresolved gaps: 1", report)

    def test_source_watermark_advances_to_captured_boundary_after_commit(self):
        manifest_path = self.transcript.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["source"] = "alma"
        manifest["source_boundary"] = "2026-11-09T12:00:00+00:00"
        manifest_path.write_text(json.dumps(manifest))
        self.begin()
        self.finish_transcript(gaps="missing evidence")
        partial = self.cli("commit")
        self.assertEqual(partial.returncode, 0, partial.stderr + partial.stdout)
        self.assertEqual(json.loads((self.root / ".dream" / "workflow.json").read_text())["phase"], "partial")
        self.assertFalse((self.root / ".dream" / "last-run-alma").exists())
        resolution = self.cli("resolve", "--thread", str(self.transcript), "--gaps", "reviewed source message m-17")
        self.assertEqual(resolution.returncode, 0, resolution.stderr)
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.root / ".dream" / "last-run-alma").read_text().strip(), manifest["source_boundary"])

    def test_failed_completion_command_is_checked(self):
        self.begin()
        result = self.cli("complete", "--thread", str(self.transcript), "--status", "analyzed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unread or unacknowledged", result.stderr)


if __name__ == "__main__":
    unittest.main()
