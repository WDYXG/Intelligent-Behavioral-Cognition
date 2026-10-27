"""Recover an interrupted group by exact offline prefix replay before any new call."""
import argparse
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from public_goods import artifacts

from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.runner import RunConfig
from public_goods.environment.world import World
from public_goods.models.client import ModelResponse

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {'A': AgentRunner, 'B': BRunner, 'C': CRunner}

def read_rows(path):
    return [json.loads(line) for line in artifacts.read_text(path).splitlines() if line.strip()]

class RecoveryReady(Exception):
    pass

class PrefixReplay:
    source_files = ('models/deepseek.py', 'experiments/recover_trial.py')

    def __init__(self, source, client=None, *, restart_failed_call=False, allowed_source_changes=()):
        self.source, self.client = Path(source), client
        self.config = artifacts.read_json(self.source/'config.json')
        self.calls = read_rows(self.source/'calls.jsonl')
        if not self.calls or any(c.get('response') is None or c['status'] not in ('valid','invalid_output') for c in self.calls):
            raise ValueError('Only a complete recorded response prefix can be replayed')
        if artifacts.exists(self.source/'trajectory.jsonl'):
            raise ValueError('The source group is already complete')
        self.excluded_terminal_attempts = []
        if restart_failed_call:
            key = lambda c: tuple(c[k] for k in ('participant_id','phase','round','stage','step'))
            final_key = key(self.calls[-1])
            start = len(self.calls) - 1
            while start and key(self.calls[start-1]) == final_key:
                start -= 1
            tail = self.calls[start:]
            maximum = self.config['settings']['max_attempts']
            if (len(tail) != maximum or [c['attempt'] for c in tail] != list(range(1, maximum+1))
                    or any(c['status'] != 'invalid_output' for c in tail)):
                raise ValueError('Restart requires exactly one exhausted terminal invalid-output call')
            self.excluded_terminal_attempts = deepcopy(tail)
            self.calls = self.calls[:start]
            if not self.calls:
                raise ValueError('This recovery requires an earlier recorded prefix')
        self.source_version_changes = []
        for name, digest in self.config['code_sha256'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest:
                if name not in allowed_source_changes:
                    raise ValueError('Source version changed: '+name)
                self.source_version_changes.append(name)
        self.events = read_rows(self.source/'events.jsonl')
        self.judgments = read_rows(self.source/'judgments.jsonl') if self.config['implementation']!='A' else []
        self.transitions = read_rows(self.source/'state_transitions.jsonl') if self.config['implementation']!='A' else []
        self.index, self.task, self.verified = 0, None, False
        self.mode = 'live' if client else 'mock'
        self.model_id = self.config['model_id']
        self.audit = None
        self.source_hashes = {name: hashlib.sha256(artifacts.read_bytes(self.source/name)).hexdigest()
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
            if hashlib.sha256(artifacts.read_bytes(self.source/name)).hexdigest()!=digest:
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
            if hasattr(self.client, 'budget') and self.client.budget.held:
                self.client.budget.quarantine_unresolved(
                    '2026-10-23 user requested continuation after process interruption; original process absent, last unreturned request remains fully reserved as unknown cost')
        return self.client.complete(request)


class OllamaPrefixReplay(PrefixReplay):
    """Explicit repair branch after exhausted citation retries, not an unchanged run."""
    source_files = ('models/ollama.py', 'experiments/recover_trial.py')

    def __init__(self, source, client=None):
        super().__init__(source, client, restart_failed_call=True,
                         allowed_source_changes=('models/ollama.py',))
        if any('evidence path unavailable:' not in row.get('error', '')
               for row in self.excluded_terminal_attempts):
            raise ValueError('This repair is limited to exhausted evidence-path failures')
        from public_goods.models.ollama import OllamaClient
        provider = self.config['provider']
        if provider.get('provider') != 'ollama' or provider.get('format') != 'json_schema':
            raise ValueError('This repair requires an Ollama structured-output source')
        self.legacy = OllamaClient(self.model_id, provider['endpoint'].removesuffix('/api/chat'),
            num_ctx=provider['requested_num_ctx'], timeout=provider['timeout_seconds'],
            structured_output=True, evidence_paths=provider.get('evidence_paths', 'free'))
        self.legacy._identity = deepcopy(provider['identity'])
        self.future = client or OllamaClient(self.model_id, self.legacy.base_url,
            num_ctx=self.legacy.num_ctx, timeout=self.legacy.timeout,
            structured_output=True, evidence_paths='visible')
        if client is None:
            self.future._identity = deepcopy(provider['identity'])
        if (self.future.model_id != self.model_id or self.future.num_ctx != self.legacy.num_ctx
                or self.future.timeout != self.legacy.timeout or not self.future.structured_output
                or self.future.evidence_paths != 'visible'
                or self.future.metadata()['identity'] != provider['identity']):
            raise ValueError('Recovery model identity or required generation settings differ')

    def build_payload(self, request):
        if self.index < len(self.calls):
            saved = self.calls[self.index]['provider_request']
            if self.legacy.build_payload(request) != saved:
                raise ValueError('Original Ollama payload cannot be reproduced')
            return deepcopy(saved)
        return self.future.build_payload(request)

    def metadata(self):
        return {**self.future.metadata(), 'recovery': self.recovery_metadata()}

    def recovery_metadata(self):
        return {**super().recovery_metadata(), 'repair_branch': True,
                'source_version_changes': self.source_version_changes,
                'original_terminal_failures_retained': len(self.excluded_terminal_attempts),
                'restart_policy': 'Replay every earlier accepted response and earlier retry exactly. Preserve the three exhausted terminal failures separately; restart only that unaccepted logical judgment with visible-path constrained decoding. Do not resample accepted actions.',
                'interpretation': 'Generation constraint changes at the repair boundary. This is an engineering continuation with a protocol deviation, not an unchanged confirmatory sample.'}


def recover(source, output=None, client=None, *, replay_class=PrefixReplay):
    model=replay_class(source,client)
    if client is not None and output is None:
        raise ValueError('Live recovery requires a new output directory')
    initial=model.events[0]
    config=RunConfig(**model.config['settings'])
    cls=RUNNERS[model.config['implementation']]
    args={} if model.config['implementation']=='A' else {'prompt_revision':model.config['prompt_revision'],
        'previous_x_mode':model.config.get('previous_x_mode','retain')}
    task=cls(World(initial['group_id'],initial['participants'],initial['seed']),model,config,output=output,**args)
    model.task=task
    if output is not None and model.excluded_terminal_attempts:
        task._append('original_terminal_failures.jsonl', model.excluded_terminal_attempts)
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

@artifacts.compact_cli
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
