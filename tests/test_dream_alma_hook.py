"""Security and source boundaries for the Alma tool hook."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dream-alma-hook.py"


class AlmaHookTest(unittest.TestCase):
    def test_only_nightly_dream_metadata_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cron = home / ".config/alma/cron"
            cron.mkdir(parents=True)
            (cron / "jobs.json").write_text(json.dumps([{"id": "job", "name": "nightly dream", "executionMode": "isolated"}]))
            personal = home / ".config/alma/personal"
            personal.mkdir(parents=True)
            (personal / "state.json").write_text(json.dumps({"runs": [
                {"threadId": "dream", "jobId": "job", "kind": "routine", "source": "cron"},
                {"threadId": "other", "jobId": "different", "kind": "routine", "source": "cron"},
            ]}))
            event = {"hook": "tool.didExecute", "input": {
                "tool": "Bash", "context": {"source": "cron", "threadId": "dream"},
                "args": {"command": "secret-command"},
                "result": {"stdout": "secret-stdout", "stderr": "secret-stderr", "exitCode": 0,
                           "durationMs": 4, "stdoutComplete": True, "stderrComplete": True},
            }}
            env = {**os.environ, "HOME": str(home), "WIKI_HOME": str(home / "wiki")}

            def invoke():
                subprocess.run(["python3", str(SCRIPT)], input=json.dumps(event), text=True,
                               env=env, check=True, capture_output=True)

            invoke()
            logs = list((home / "wiki/.dream/logs").glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            contents = logs[0].read_text()
            self.assertNotIn("secret-", contents)
            record = json.loads(contents)
            self.assertEqual(record["tool"], "Bash")
            self.assertEqual(record["exitCode"], 0)
            self.assertEqual(record["output"], "restricted")
            self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
            event["input"]["context"]["threadId"] = "other"
            invoke()
            event["input"]["context"].update(threadId="dream", source="chat")
            invoke()
            self.assertEqual(logs[0].read_text(), contents)
            event["hook"] = "tool.willExecute"
            event["input"]["context"]["source"] = "cron"
            invoke()
            start = json.loads(logs[0].read_text().splitlines()[-1])
            self.assertEqual(start["stage"], "tool.willExecute")
            self.assertEqual(start["thread_ref"], record["thread_ref"])
            self.assertNotIn("secret-", json.dumps(start))
            event["hook"] = "tool.onError"
            event["input"]["error"] = "secret-error-details"
            invoke()
            lines = logs[0].read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[2])["stage"], "tool.onError")
            self.assertNotIn("secret-", lines[2])
            event["hook"] = "tool.didExecute"
            event["input"]["args"] = {"command": "wiki verify"}
            event["input"]["duration"] = 12.5
            event["input"]["result"] = {"stdout": "verify ok: 12 pages (schema)\nsecret-output\n",
                                         "stderr": "secret-error", "exitCode": 0}
            invoke()
            record = json.loads(logs[0].read_text().splitlines()[-1])
            self.assertEqual(record["command"], "wiki verify")
            self.assertEqual(record["stdout"], ["verify ok: 12 pages (schema)"])
            self.assertEqual(record["stdout_omitted_lines"], 1)
            self.assertEqual(record["stderr"], "nonempty (restricted)")
            self.assertNotIn("secret-", json.dumps(record))
            event["input"]["args"] = {"command": "wiki dream read --thread secret-path"}
            invoke()
            record = json.loads(logs[0].read_text().splitlines()[-1])
            self.assertEqual(record["command"], "wiki dream read [arguments restricted]")
            self.assertNotIn("secret-", json.dumps(record))

    def test_rotation_preserves_boundary_and_unowned_files(self):
        spec = importlib.util.spec_from_file_location("dream_log", SCRIPT.parent / "dream_log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            now = 1800000000
            boundary = now - 7 * 24 * 60 * 60
            old = directory / "verbose-2026-01-01.jsonl"
            exact = directory / "verbose-2026-01-02.jsonl"
            foreign = directory / "verbose-2026-01-03.jsonl"
            for path in (old, exact):
                path.write_text('{"format":"bugwiki-dream-verbose-v1"}\n')
            foreign.write_text('{"other":"data"}\n')
            os.utime(old, (boundary - 1, boundary - 1))
            os.utime(exact, (boundary, boundary))
            os.utime(foreign, (boundary - 1, boundary - 1))
            module.rotate(directory, now)
            self.assertFalse(old.exists())
            self.assertTrue(exact.exists())
            self.assertEqual(foreign.read_text(), '{"other":"data"}\n')

    def test_active_command_does_not_block_append_or_allow_rotation(self):
        spec = importlib.util.spec_from_file_location("dream_log", SCRIPT.parent / "dream_log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "verbose-2026-01-01.jsonl"
            path.write_text('{"format":"bugwiki-dream-verbose-v1"}\n')
            os.utime(path, (1, 1))
            code = ('import sys, fcntl; from pathlib import Path; '
                    'sys.path.insert(0, sys.argv[1]); from dream_log import locked; '
                    'lock = locked(Path(sys.argv[2]), ".active.lock", fcntl.LOCK_SH); '
                    'lock.__enter__(); print("ready", flush=True); sys.stdin.readline(); lock.__exit__(None, None, None)')
            proc = subprocess.Popen(["python3", "-c", code, str(SCRIPT.parent), tmp], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                module.rotate(directory, 1800000000)
                module.append(directory, {"stage": "test"})
                self.assertTrue(path.exists())
            finally:
                proc.stdin.write("done\n")
                proc.stdin.close()
                proc.wait(timeout=5)
                proc.stdout.close()
            module.rotate(directory, 1800000000)
            self.assertFalse(path.exists())

    def test_rotation_does_not_delete_replaced_file(self):
        spec = importlib.util.spec_from_file_location("dream_log", SCRIPT.parent / "dream_log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "verbose-2026-01-01.jsonl"
            path.write_text('{"format":"bugwiki-dream-verbose-v1"}\n')
            os.utime(path, (1, 1))
            original_owned = module.owned

            def replace_after_open(fd):
                result = original_owned(fd)
                replacement = directory / "replacement"
                replacement.write_text("unowned data")
                os.replace(replacement, path)
                return result

            with patch.object(module, "owned", side_effect=replace_after_open):
                module.rotate(directory, 1800000000)
            self.assertEqual(path.read_text(), "unowned data")

    def test_rotation_keeps_old_logs_while_run_is_unfinished(self):
        spec = importlib.util.spec_from_file_location("dream_log", SCRIPT.parent / "dream_log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            dream = Path(tmp) / ".dream"
            logs = dream / "logs"
            logs.mkdir(parents=True)
            old = logs / "verbose-2026-01-01.jsonl"
            state = dream / "workflow.json"
            now = 1800000000
            old.write_text('{"format":"bugwiki-dream-verbose-v1"}\n')
            os.utime(old, (now - 604801, now - 604801))
            for contents in ('{"phase":"processing"}', '{"phase":"committing"}', 'invalid'):
                state.write_text(contents)
                module.rotate(logs, now)
                self.assertTrue(old.exists(), contents)
            state.write_text('{"phase":"success"}')
            (dream / "pending-batch.json").write_text('{}')
            module.rotate(logs, now)
            self.assertTrue(old.exists())
            (dream / "pending-batch.json").unlink()
            module.rotate(logs, now)
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
