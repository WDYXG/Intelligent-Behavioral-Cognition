import json
from pathlib import Path
import tempfile
import unittest
from public_goods.agents.agent_bc import CRunner
from public_goods.environment.world import World
from public_goods.experiments.recover_trial import recover, read_rows
from public_goods.models.client import MockModel
from public_goods.models.deepseek import CNYBudget

class LocalClient(MockModel):
    mode='live'
    @property
    def sent_calls(self):return len(self.requests)
    def metadata(self):return {'test_only':True}

class RecoveryTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source'
        task=CRunner(World('recover-fixture',['a','b','c','d']),MockModel(),output=source)
        task.run()
        trajectory=read_rows(source/'trajectory.jsonl')
        for name,n in [('calls.jsonl',3),('events.jsonl',1),('judgments.jsonl',0),('state_transitions.jsonl',0)]:
            rows=read_rows(source/name)[:n]
            (source/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        (source/'trajectory.jsonl').unlink()
        # The runner configuration must explicitly permit the injected live client.
        config=json.loads((source/'config.json').read_text());config['settings']['allow_live']=True
        (source/'config.json').write_text(json.dumps(config))
        return source,trajectory

    def test_mid_batch_prefix_is_replayed_without_new_calls_and_continues_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source,expected=self.fixture(root)
            self.assertEqual(recover(source)['recorded_requests_reproduced'],3)
            original=(source/'calls.jsonl').read_bytes()
            client=LocalClient();client.budget=CNYBudget(root/'ledger')
            try:
                result=recover(source,root/'continued',client)
                self.assertEqual(result['new_network_requests'],237)
                self.assertEqual(read_rows(root/'continued/trajectory.jsonl'),expected)
                self.assertEqual((source/'calls.jsonl').read_bytes(),original)
            finally:client.budget.close()

    def test_corrupt_request_or_event_prevents_any_new_call(self):
        for target in ('calls.jsonl','events.jsonl'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);source,_=self.fixture(root)
                rows=read_rows(source/target)
                if target=='calls.jsonl':rows[0]['request']['messages'][0]['content']='tampered'
                else:rows[0]['extra']='tampered'
                (source/target).write_text(''.join(json.dumps(r)+'\n' for r in rows))
                client=LocalClient();client.budget=CNYBudget(root/'ledger')
                try:
                    with self.assertRaises(ValueError):recover(source,root/'blocked',client)
                    self.assertEqual(client.sent_calls,0)
                finally:client.budget.close()

if __name__=='__main__':unittest.main()
