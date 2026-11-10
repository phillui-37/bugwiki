import json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WIKI=ROOT/'scripts'/'wiki'

class DreamWorkflowCLI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)/'wiki'; self.root.mkdir(); (self.root/'.raw'/'batch').mkdir(parents=True); (self.root/'facts').mkdir(); (self.root/'solutions').mkdir(); (self.root/'reports').mkdir()
  self.env=dict(os.environ,WIKI_HOME=str(self.root)); subprocess.run(['git','init','-q',str(self.root)],check=True); subprocess.run(['git','-C',str(self.root),'config','user.name','Test'],check=True); subprocess.run(['git','-C',str(self.root),'config','user.email','test@example.invalid'],check=True); (self.root/'README.md').write_text('base\n'); subprocess.run(['git','-C',str(self.root),'add','README.md'],check=True); subprocess.run(['git','-C',str(self.root),'commit','-qm','base'],check=True)
  self.transcript=self.root/'.raw'/'batch'/'t.txt'; self.transcript.write_text('transcript body\n')
 def tearDown(self): self.tmp.cleanup()
 def cli(self,*args,input=None): return subprocess.run(['bash',str(WIKI),'dream',*args],env=self.env,input=input,capture_output=True,text=True)
 def run_started(self):
  r=self.cli('begin','--batch',str(self.root/'.raw'/'batch')); self.assertEqual(r.returncode,0,r.stderr); return json.loads((self.root/'.dream'/'workflow.json').read_text())
 def test_bounded_resume_and_explicit_ack_gate(self):
  self.run_started(); p=str(self.transcript)
  a=self.cli('read','--thread',p,'--limit','5'); self.assertEqual(a.stdout,'trans'); self.assertEqual(a.returncode,0,a.stderr)
  self.assertNotEqual(self.cli('complete','--thread',p,'--status','analyzed','--source-complete').returncode,0)
  self.cli('ack','--thread',p); self.cli('read','--thread',p,'--limit','100'); self.cli('ack','--thread',p)
  self.assertEqual(self.cli('complete','--thread',p,'--status','analyzed','--source-complete').returncode,0)
  self.assertEqual(self.cli('commit').returncode,0)
 def test_changed_hash_and_unread_content_reject_completion(self):
  self.run_started(); p=str(self.transcript)
  self.assertNotEqual(self.cli('complete','--thread',p,'--status','analyzed','--source-complete').returncode,0)
  self.transcript.write_text('changed'); self.assertNotEqual(self.cli('read','--thread',p).returncode,0)
 def test_gaps_and_uncommitted_owned_edits_block_success(self):
  self.run_started(); p=str(self.transcript)
  self.cli('read','--thread',p,'--limit','100'); self.cli('ack','--thread',p); self.cli('complete','--thread',p,'--status','analyzed','--gaps','source omitted')
  self.assertNotEqual(self.cli('commit').returncode,0)
 def test_unrelated_staged_and_unstaged_work_survive_commit(self):
  self.run_started(); p=str(self.transcript); self.cli('read','--thread',p,'--limit','100'); self.cli('ack','--thread',p); self.cli('complete','--thread',p,'--status','analyzed','--source-complete')
  (self.root/'README.md').write_text('dream output\n'); self.cli('own',input='README.md\n')
  (self.root/'unrelated.md').write_text('unstaged'); subprocess.run(['git','-C',str(self.root),'add','unrelated.md'],check=True); (self.root/'unrelated.md').write_text('staged plus change')
  before=(self.root/'unrelated.md').read_bytes(); r=self.cli('commit'); self.assertEqual(r.returncode,0,r.stderr); self.assertEqual((self.root/'unrelated.md').read_bytes(),before)
  self.assertEqual(subprocess.check_output(['git','-C',str(self.root),'diff','--cached','--name-only'],text=True).strip(),'unrelated.md')

if __name__=='__main__': unittest.main()
