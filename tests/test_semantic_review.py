"""Focused review protocol checks; model double, actual evidence files."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.semantic_review import SemanticReviewer


class SemanticReviewTests(unittest.TestCase):
    def test_review_uses_fresh_evidence_and_reuses_only_identical_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';work.mkdir();(work/'out.txt').write_text('answer42')
            calls=[]
            def factory(directory):
                def model(request):
                    calls.append(request)
                    self.assertEqual([],request['memory']);self.assertEqual([],request['observations'])
                    evidence=request['review_evidence']
                    return {'action':'tool','name':'submit_review','arguments':{'verdict':'PASS','findings':[],
                        'checks':[{'check_id':'A','status':'PASS','reason':'observed answer'}],
                        'citations':[{'path':f['path'],'sha256':f['sha256'],'reason':'actual bytes'} for f in evidence['files']]}}
                return model
            state={'task_id':'t','revision':1,'candidate':'v','contract_hash':'h','owner':'executor',
                   'acceptance':[{'id':'A','expected':'answer42'}]}
            reviewer=SemanticReviewer(factory,root/'reviews')
            receipt=reviewer.review('return answer42',work,state,[],['out.txt'])
            self.assertTrue(reviewer.fresh(receipt,work))
            self.assertEqual(receipt,reviewer.review('return answer42',work,state,[],['out.txt']))
            self.assertEqual(1,len(calls))
            (work/'out.txt').write_text('changed')
            self.assertFalse(reviewer.fresh(receipt,work))

    def test_uncited_pass_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'out').write_text('x')
            def factory(_):return lambda request:{'action':'tool','name':'submit_review','arguments':{
                'verdict':'PASS','findings':[],'checks':[{'check_id':'A','status':'PASS','reason':'claim'}],'citations':[]}}
            reviewer=SemanticReviewer(factory,root/'reviews')
            with self.assertRaises(ValueError):reviewer.review('g',root,{'task_id':'t','revision':1,'candidate':'v',
                'contract_hash':'h','owner':'executor','acceptance':[{'id':'A'}]},[],['out'])


if __name__=='__main__':unittest.main()
