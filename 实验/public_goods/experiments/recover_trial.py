"""Recover an interrupted group by exact offline prefix replay before any new call."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.runner import RunConfig
from public_goods.environment.world import World
from public_goods.models.client import ModelResponse

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {'A': AgentRunner, 'B': BRunner, 'C': CRunner}

def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

class RecoveryReady(Exception):
    pass

class PrefixReplay:
    source_files = ('models/deepseek.py', 'experiments/recover_trial.py')

    def __init__(self, source, client=None):
        self.source, self.client = Path(source), client
        self.config = json.loads((self.source/'config.json').read_text())
        self.calls = read_rows(self.source/'calls.jsonl')
        if not self.calls or any(c.get('response') is None or c['status'] not in ('valid','invalid_output') for c in self.calls):
            raise ValueError('Only a complete recorded response prefix can be replayed')
        if (self.source/'trajectory.jsonl').exists():
            raise ValueError('The source group is already complete')
        for name, digest in self.config['code_sha256'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest:
                raise ValueError('Source version changed: '+name)
        self.events = read_rows(self.source/'events.jsonl')
        self.judgments = read_rows(self.source/'judgments.jsonl') if self.config['implementation']!='A' else []
        self.transitions = read_rows(self.source/'state_transitions.jsonl') if self.config['implementation']!='A' else []
        self.index, self.task, self.verified = 0, None, False
        self.mode = 'live' if client else 'mock'
        self.model_id = self.config['model_id']
        self.audit = None
        self.source_hashes = {name: hashlib.sha256((self.source/name).read_bytes()).hexdigest()
                              for name in ('config.json','calls.jsonl','events.jsonl')}

    @property
    def sent_calls(self):
        return self.client.sent_calls if self.client else 0

    @property
    def budget(self):
        return self.client.budget

    def metadata(self):
        return {'recovery': self.recovery_metadata(),
                'provider': self.client.metadata() if self.client else None}

    def recovery_metadata(self):
        return {'source':str(self.source.resolve()),'source_hashes':self.source_hashes,
                'inherited_calls':len(self.calls),
                'note':'Inherited responses are replayed without HTTP and retain their original cost/response IDs. Only new calls increment network_requests.'}

    def verify_boundary(self):
        task = self.task
        if (task.world.audit_log()!=self.events or getattr(task,'judgments',[])!=self.judgments
                or getattr(task,'state_transitions',[])!=self.transitions):
            raise ValueError('Recovered committed World/state differs from the saved prefix')
        if len(task.calls)!=len(self.calls):
            raise ValueError('Recovered call count differs')
        for actual, saved in zip(task.calls,self.calls):
            for key in ('call_id','participant_id','phase','round','stage','step','attempt','input_bytes',
                        'request','status','action','judgment','normalizations','error'):
                if actual.get(key)!=saved.get(key):
                    raise ValueError('Recovered call differs: '+key)
        for name, digest in self.source_hashes.items():
            if hashlib.sha256((self.source/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('Source changed while recovering')
        self.audit={'passed':True,'recorded_requests_reproduced':len(self.calls),
                    'committed_events_checked':len(self.events),'state_transitions_checked':len(self.transitions),
                    'pending_participants':sorted(getattr(task,'_pending',{})),
                    'next_stage':{k:getattr(task.world,k) for k in ('phase','round','stage')}}
        self.verified=True

    def complete(self, request):
        if self.index<len(self.calls):
            saved=self.calls[self.index]
            if asdict(request)!=saved['request']:
                raise ValueError('Reconstructed request differs before any new network call')
            self.index+=1
            return ModelResponse(**saved['response'])
        if not self.verified:
            self.verify_boundary()
            if self.client is None:
                raise RecoveryReady()
            if self.client.budget.held:
                self.client.budget.quarantine_unresolved(
                    '2026-10-23 user requested continuation after process interruption; original process absent, last unreturned request remains fully reserved as unknown cost')
        return self.client.complete(request)

def recover(source, output=None, client=None):
    model=PrefixReplay(source,client)
    if client is not None and output is None:
        raise ValueError('Live recovery requires a new output directory')
    initial=model.events[0]
    config=RunConfig(**model.config['settings'])
    cls=RUNNERS[model.config['implementation']]
    args={} if model.config['implementation']=='A' else {'prompt_revision':model.config['prompt_revision'],
        'previous_x_mode':model.config.get('previous_x_mode','retain')}
    task=cls(World(initial['group_id'],initial['participants'],initial['seed']),model,config,output=output,**args)
    model.task=task
    try:
        task.run()
    except RecoveryReady:
        return model.audit
    finally:
        if output is not None:
            result={'recovery':model.recovery_metadata(),'prefix_audit':model.audit,
                    'summary':task.summary,'new_network_requests':model.sent_calls}
            (Path(output)/'recovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return {'prefix_audit':model.audit,'summary':task.summary,'new_network_requests':model.sent_calls}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    if args.execute:
        from public_goods.models.deepseek import CNYBudget,DeepSeekClient,load_key
        if args.output is None:parser.error('--execute requires --output')
        budget=CNYBudget(ROOT/'runs/live/deepseek_budget_20261020.jsonl')
        try: result=recover(args.source,args.output,DeepSeekClient(load_key(),budget))
        finally: budget.close()
    else: result=recover(args.source)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
