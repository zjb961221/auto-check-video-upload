import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from queries import load_queries, bind_parameters
from workflows import load_workflows
from parameter_choices import normalize_options
from parameter_widgets import ParameterChoice
import app

ROOT=Path(__file__).parents[1]


class ChoiceTests(unittest.TestCase):
    def test_values_not_labels_and_integer_conversion(self):
        query=load_queries(ROOT/'queries.json')[-1]
        self.assertEqual(bind_parameters(query,{'mine':'150622000013200031','recorder':'10'}),{'mine':'150622000013200031','recorder':10})
        for bad in ('利民','not-configured',"' OR 1=1 --",'',None,[]):
            with self.assertRaises(ValueError):
                bind_parameters(query,{'mine':bad,'recorder':'9'})

    def test_bad_options_and_defaults_rejected(self):
        invalid=[[],[{'label':'A','value':''}],[{'label':'A','value':None}],
                 [{'label':'A','value':'1'},{'label':'A','value':'2'}],
                 [{'label':'A','value':'1'},{'label':'B','value':'1'}]]
        for options in invalid:
            with self.assertRaises(ValueError):
                normalize_options({'label':'测试','type':'text','options':options})
        with self.assertRaises(ValueError):
            normalize_options({'label':'测试','type':'integer','options':[{'label':'A','value':'x'}]})
        with self.assertRaises(ValueError):
            normalize_options({'label':'测试','type':'text','default':'display','options':[{'label':'display','value':'actual'}]})
        spec={'label':'测试','type':'integer','default':9,'options':[{'label':'A','value':9}]}
        normalize_options(spec)
        self.assertEqual(spec['default'],'9')

    def test_workflow_default_must_be_an_option_value(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            (folder/'queries.json').write_text((ROOT/'queries.json').read_text(encoding='utf-8'),encoding='utf-8')
            flow={'version':1,'workflows':[{'id':'f','name':'f','steps':[{'id':'q','title':'q','type':'query','ref':'按煤矿和录像机筛选通道（下拉示例）','defaults':{'recorder':'99'}}]}]}
            (folder/'workflows.json').write_text(json.dumps(flow))
            with self.assertRaisesRegex(ValueError,'defaults'):
                load_workflows(folder/'workflows.json')


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'),'Windows desktop CI')
class ChoiceWindowTests(unittest.TestCase):
    def test_both_forms_preserve_mapping_readonly_and_workflow_selection(self):
        with tempfile.TemporaryDirectory() as folder,patch('app.SETTINGS',Path(folder)/'connection.json'),patch('app.configure_logging',return_value=MagicMock()):
            window=app.App()
            try:
                window.withdraw()
                self.assertIn('客户自查运维工具',window.title())
                window.choice.current(len(window.queries)-1);window.change_query()
                combo=window.parameter_inputs[0]
                self.assertIsInstance(combo,ParameterChoice)
                self.assertEqual(combo.get(),'请选择…')
                combo.current(0);combo.selected()
                self.assertEqual(combo.get(),'利民')
                self.assertEqual(window.parameters['mine'].get(),'150622000013200031')
                window.set_busy(True);window.set_busy(False)
                self.assertEqual(str(combo['state']),'readonly')
                panel=window.workflow
                panel.start_flow(len(panel.flows)-1)
                controls=[w for w,s in panel.controls if isinstance(w,ParameterChoice)]
                self.assertEqual(len(controls),2)
                controls[0].current(1);controls[0].selected()
                controls[1].current(1);controls[1].selected()
                panel.set_busy(True);panel.set_busy(False)
                self.assertTrue(all(str(c['state'])=='readonly' for c in controls))
                self.assertEqual(bind_parameters(panel.run.step['operation'],{k:v.get() for k,v in panel.parameters.items()}),{'mine':'150622000013200063','recorder':10})
                panel.run.ready();panel.next();panel.navigate(-1)
                controls=[w for w,s in panel.controls if isinstance(w,ParameterChoice)]
                self.assertEqual([c.get() for c in controls],['黄白茨','.10 录像机'])
                panel.run.ready()
                controls[0].current(0);controls[0].selected()
                self.assertEqual(panel.run.states[0],'pending')
            finally:
                window.destroy()
