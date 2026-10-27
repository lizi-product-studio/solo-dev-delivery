import tempfile
import unittest
from pathlib import Path
import subprocess
from benchmark import fixtures, check_submission

class SubmissionTests(unittest.TestCase):
    def test_declared_worktree_wins_without_selecting_highest_score(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            root=base/'workspace'
            fixtures.build(fixtures.TASK_IDS[0],root)
            commands=[['init','--quiet'],['config','user.name','Evaluation'],
                      ['config','user.email','evaluation@localhost.invalid'],['add','.'],
                      ['-c','commit.gpgSign=false','commit','--quiet','-m','task']]
            for args in commands:
                subprocess.run(['git','-C',str(root)]+args,check=True,capture_output=True)
            one,two=base/'one',base/'two'
            for branch,path in [('one',one),('two',two)]:
                subprocess.run(['git','-C',str(root),'worktree','add','--quiet','-b',branch,str(path)],check=True,capture_output=True)
            answer=base/'answer.txt'
            answer.write_text('交付目录：'+str(two),encoding='utf-8')
            result=check_submission({'base':base,'workspace':root,'env':None},fixtures.TASK_IDS[0],answer)
            self.assertEqual(result['selected'],'declared-owned-worktree')
            self.assertEqual(Path(result['submitted_workspace']),two)
            self.assertFalse(result['result']['completed'])
            answer.write_text('尚未指明交付目录。',encoding='utf-8')
            result=check_submission({'base':base,'workspace':root,'env':None},fixtures.TASK_IDS[0],answer)
            self.assertEqual(result['selected'],'root')
            self.assertTrue(result['selection_unknown'])

if __name__=='__main__': unittest.main()
