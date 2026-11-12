"""Historical source recovery at the public CLI, never rerunning tool commands."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from tests import test_dream_workflow as workflow


class OriginalCLI(unittest.TestCase):
    def setUp(self):
        self.f = workflow.DreamWorkflowCLI()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli

    def source(self, output, *, pages=None):
        snapshot = {'id': 'body', 'messages': [{'id': 'original-message', 'role': 'assistant', 'content': [
            {'type': 'tool_call', 'name': 'Bash', 'toolCallId': 'original-call', 'input': {'command': 'exit 99'}},
            {'type': 'tool_result', 'name': 'Bash', 'toolCallId': 'original-call', 'output': output}]}]}
        if pages is not None:
            snapshot['source_pages'] = pages
        path = self.f.transcript.parent / 'body.json'
        path.write_text(json.dumps(snapshot))
        manifest = self.f.transcript.parent / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['source_files'] = {'body.json': hashlib.sha256(path.read_bytes()).hexdigest()}
        manifest.write_text(json.dumps(data))
        self.f.begin()
        self.cli('read', '--thread', str(self.f.transcript))
        self.cli('ack', '--thread', str(self.f.transcript))

    def complete(self):
        return self.cli('complete', '--thread', str(self.f.transcript), '--status', 'analyzed', '--source-complete')

    def recover_all(self, limit=12000):
        offset, output = 0, ''
        while True:
            result = self.cli('recover', '--thread', 'body', '--offset', str(offset), '--limit', str(limit))
            self.assertEqual(result.returncode, 0, result.stderr)
            output += result.stdout
            receipt = self.f.state()['transcripts'][str(self.f.transcript)]['evidence']
            offset = receipt['emitted_through']
            if offset == receipt['characters']:
                return output, receipt

    def test_truncated_snapshot_recovers_retained_original_and_records_identity(self):
        original = self.f.root / 'original.log'
        original.write_text('historical result ' * 1000)
        self.source(f'...[alma compacted 9000 chars]... Full output saved to {original}. For full coverage, Read this existing archive')
        output, receipt = self.recover_all()
        self.assertIn('historical result', output)
        self.assertIn('original-call', output)
        self.assertEqual(receipt['gaps'], [])
        self.assertEqual(self.complete().returncode, 0)
        evidence = json.loads(Path(receipt['path']).read_text())
        retained = next(p for p in evidence['provenance'] if p['kind'] == 'retained-output')
        self.assertEqual(retained['path'], str(original))
        self.assertEqual(retained['sha256'], hashlib.sha256(original.read_bytes()).hexdigest())
        self.assertEqual(retained['message_id'], 'original-message')
        self.assertEqual(retained['tool_call_id'], 'original-call')

    def test_retained_source_code_template_is_not_a_real_truncation_marker(self):
        original = self.f.root / 'source-output.log'
        original.write_text('text = f"…[thread truncated; {len(text) - MAX} chars cut]"')
        self.source(f'...[alma compacted 999 chars]... Full output saved to {original}. For full coverage, Read this existing archive')
        _, receipt = self.recover_all()
        self.assertEqual(receipt['gaps'], [])
        self.assertEqual(self.complete().returncode, 0)

    def test_incomplete_stderr_cannot_be_filled_by_recovering_stdout(self):
        original = self.f.root / 'stdout.log'
        original.write_text('complete stdout')
        self.source({'stdout': f'...[alma compacted 999 chars]... Full output saved to {original}. For full coverage, Read this existing archive', 'stdoutComplete': False, 'stderr': '', 'stderrComplete': False})
        _, receipt = self.recover_all()
        self.assertIn('stderr incomplete', json.dumps(receipt['gaps']))
        self.assertNotEqual(self.complete().returncode, 0)

    def test_missing_original_remains_gap_and_resolve_text_cannot_clear_it(self):
        self.source(f'...[alma compacted 999 chars]... Full output saved to {self.f.root}/missing.log. For full coverage, Read this existing archive')
        _, receipt = self.recover_all()
        self.assertTrue(receipt['gaps'])
        self.assertNotEqual(self.complete().returncode, 0)
        done = self.cli('complete', '--thread', str(self.f.transcript), '--status', 'analyzed', '--gaps', 'original unavailable')
        self.assertEqual(done.returncode, 0, done.stderr)
        resolved = self.cli('resolve', '--thread', str(self.f.transcript), '--gaps', 'I say it is complete')
        self.assertNotEqual(resolved.returncode, 0)
        self.assertFalse(self.f.state()['transcripts'][str(self.f.transcript)]['source_complete'])

    def test_partial_pages_and_compact_ack_do_not_prove_source_coverage(self):
        self.source('complete retained source ' * 200)
        self.assertNotEqual(self.complete().returncode, 0)
        first = self.cli('recover', '--thread', 'body', '--limit', '50')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(self.complete().returncode, 0)
        # Jumping to EOF does not fill the missing interval.
        receipt = self.f.state()['transcripts'][str(self.f.transcript)]['evidence']
        skip = self.cli('recover', '--thread', 'body', '--offset', str(receipt['characters'] - 1))
        self.assertNotEqual(skip.returncode, 0)
        self.recover_all()
        self.assertEqual(self.complete().returncode, 0)

    def test_missing_source_message_ids_block_completion_after_all_output_read(self):
        self.source('only surviving message', pages=[{'missingMessageIds': ['lost-message'], 'hasMore': False}])
        _, receipt = self.recover_all()
        self.assertIn('lost-message', json.dumps(receipt['gaps']))
        self.assertNotEqual(self.complete().returncode, 0)

    def test_old_attestation_can_recover_but_cannot_publish_without_evidence(self):
        self.source('retained original')
        state = self.f.state()
        state['transcripts'][str(self.f.transcript)].update(analysis='analyzed', source_complete=True)
        (self.f.root / '.dream/workflow.json').write_text(json.dumps(state))
        refused = self.cli('commit')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('source coverage missing', refused.stderr)
        self.recover_all()
        self.assertEqual(self.complete().returncode, 0)

    def test_original_ids_lookup_uses_live_full_pages_not_tool_reexecution(self):
        bindir = self.f.root / 'bin'
        bindir.mkdir()
        calls = self.f.root / 'calls.jsonl'
        stub = bindir / 'alma'
        stub.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ['thread', 'messages', 'body'] and '--full' in args and '--json' in args
with Path(%r).open('a') as f: f.write(json.dumps(args)+'\\n')
offset = int(args[args.index('--offset')+1])
messages = [{'id': 'other', 'message': {'role':'user','parts':[{'type':'text','text':'NOT IN FROZEN SCOPE'}]}}] if offset == 0 else [{'id':'original-message','message':{'role':'assistant','parts':[{'type':'tool-Bash','toolCallId':'original-call','output':{'stdout':'historical uncut output','stdoutComplete':True}}]}}]
print(json.dumps({'messages':messages,'hasMore':offset==0,'nextOffset':1 if offset==0 else None,'missingMessageIds':[],'sourceTextTruncated':False,'contentMode':'full'}))
''' % str(calls))
        stub.chmod(0o755)
        self.f.env['PATH'] = str(bindir) + os.pathsep + os.environ['PATH']
        self.source('...[alma compacted 999 chars]...')
        # Select the actual Alma adapter, limited to the frozen message identity.
        state = self.f.state()
        state['source'] = 'alma'
        (self.f.root / '.dream/workflow.json').write_text(json.dumps(state))
        output, receipt = self.recover_all()
        self.assertIn('historical uncut output', output)
        self.assertNotIn('NOT IN FROZEN SCOPE', output)
        self.assertEqual(len(calls.read_text().splitlines()), 2)
        self.assertEqual(receipt['gaps'], [])
        self.assertEqual(self.complete().returncode, 0)
        self.recover_all()
        self.assertEqual(len(calls.read_text().splitlines()), 2, 'cached evidence must not repeat lookup')


if __name__ == '__main__':
    unittest.main()
