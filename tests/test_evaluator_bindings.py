import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from control_plane.evaluator import verify_evidence,EvaluationError


class EvaluatorBindingTests(unittest.TestCase):
    def verify(self,root,actual,expected,uri='result.json',environment='local'):
        path=root/uri;path.write_text(json.dumps(actual),encoding='utf-8')
        check={'id':'A','environment':'local','assertion':{'type':'json_pointer_equals','path':'result.json','pointer':'/value','value':expected}}
        ref={'id':'e','check_id':'A','uri':uri,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'verdict':'PASS','environment':environment}
        return verify_evidence(ref,[check],root,reviewer_id='reviewer',owner='owner')

    def test_boolean_cannot_impersonate_numeric_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for actual,expected in [(True,1),(0,False),([True],[1]),({'x':1},{'x':True})]:
                with self.subTest(actual=actual),self.assertRaises(EvaluationError):
                    self.verify(root,{'value':actual},expected)
            self.assertEqual('PASS',self.verify(root,{'value':1.0},1)['status'])

    def test_other_file_and_environment_do_not_satisfy_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaisesRegex(EvaluationError,'artifact path'):
                self.verify(root,{'value':42},42,uri='other.json')
            with self.assertRaisesRegex(EvaluationError,'environment'):
                self.verify(root,{'value':42},42,environment='production')


if __name__=='__main__':unittest.main()
