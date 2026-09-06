#!/usr/bin/env python3
import json, os, subprocess, tempfile, unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "ao-refresh-orchestrator"

class RefreshTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                              capture_output=True).stdout.strip()

    def make_fixture(self):
        tmp = Path(self._tmp.name); remote=tmp/'remote.git'; source=tmp/'source'
        subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        subprocess.run(['git','init','-q','-b','main',str(source)],check=True)
        self.git(source,'config','user.email','test@example.com'); self.git(source,'config','user.name','Test')
        (source/'a.txt').write_text('one\n'); self.git(source,'add','.'); self.git(source,'commit','-m','one')
        self.git(source,'remote','add','origin',str(remote)); self.git(source,'push','-u','origin','main')
        subprocess.run(['git','--git-dir',str(remote),'symbolic-ref','HEAD','refs/heads/main'],check=True)
        data=tmp/'.ao/data'; wt=data/'worktrees/demo/orchestrator/demo-orchestrator'; wt.parent.mkdir(parents=True)
        self.git(source,'worktree','add','-b','ao/demo-orchestrator',str(wt),'HEAD')
        self.git(wt,'remote','set-head','origin','main')
        return tmp, source, wt, data

    def setUp(self): self._tmp=tempfile.TemporaryDirectory(prefix='ao-refresh-test-')
    def tearDown(self): self._tmp.cleanup()

    def run_helper(self, cwd, data, project="demo"):
        env={**os.environ,"AO_DATA_DIR":str(data),"AO_PROJECT_ID":project,
             "XDG_STATE_HOME":str(Path(data).parents[1]/"state")}
        return subprocess.run([str(HELPER),"run"],cwd=cwd,env=env,text=True,capture_output=True)

    def test_clean_orchestrator_fast_forwards(self):
        tmp, source, wt, data = self.make_fixture(); before=self.git(wt,"rev-parse","HEAD")
        (source/"a.txt").write_text("two\n"); self.git(source,"commit","-am","two"); self.git(source,"push","origin","main")
        target=self.git(source,"rev-parse","HEAD"); result=self.run_helper(wt,data)
        self.assertEqual(result.returncode,0,result.stderr); self.assertNotEqual(before,target)
        self.assertEqual(self.git(wt,"rev-parse","HEAD"),target)
        records=list((tmp/"state/ao-orchestrator-refresh/demo").glob("*/last-run.json"))
        self.assertEqual(len(records),1); self.assertEqual(json.loads(records[0].read_text())["head"],target)

    def test_worker_path_is_noop(self):
        tmp, source, wt, data = self.make_fixture(); worker=data/"worktrees/demo/worker/demo-worker"
        worker.parent.mkdir(parents=True); self.git(source,"worktree","add","-b","ao/demo-worker",str(worker),"HEAD")
        result=self.run_helper(worker,data); self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((tmp/"state").exists())

    def test_dirty_orchestrator_fails_closed(self):
        tmp, source, wt, data = self.make_fixture(); (wt/"dirty.txt").write_text("dirty\n")
        result=self.run_helper(wt,data); self.assertNotEqual(result.returncode,0)
        self.assertIn("uncommitted or untracked",result.stderr)

if __name__ == "__main__": unittest.main()
