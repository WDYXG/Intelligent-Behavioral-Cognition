"""Audit complete saved groups and compare to frozen human development statistics."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from public_goods import artifacts
import numpy as np
from public_goods.agents.agent_a import AgentRunner
from public_goods.agents.agent_bc import BRunner, CRunner
from public_goods.agents.runner import RunConfig
from public_goods.environment.world import World
from public_goods.evaluation.preflight import audit_saved_run
from public_goods.evaluation.human_reference import load_agent, group_histograms, w1_cells, behavior_statistics
from public_goods.experiments.sequential_diagnostic import _RecordedModel
from public_goods.models.deepseek import cost_upper

ROOT=Path(__file__).resolve().parents[1]
RUNNERS={'A':AgentRunner,'B':BRunner,'C':CRunner}
def read(path):return artifacts.read_json(path)
def rows(path):return [json.loads(s) for s in artifacts.read_text(path).splitlines() if s.strip()]

def review(arms):
    human_path=ROOT/'data/reference/开发集统计.json'
    human=read(human_path)
    reference=np.array([r['probabilities_0_to_20'] for r in human['round_contributions']])
    result={'status':'completed','purpose':'Descriptive development pilot; not confirmatory inference',
            'human_groups':human['groups'],'human_phase_means':human['phase_means'],
            'reference_sha256':hashlib.sha256(human_path.read_bytes()).hexdigest(),
            'evaluation_split_used':False,'model_api_calls_in_review':0,'uncertainty_estimated':False,
            'arms':{},'audits':{},'limitations':[
                'One independent group per arm; no estimate of run-to-run variability or significance.',
                'A is direct decision; B/C additionally use a derived fact view and judgment pathway. B-C is the primary module contrast.',
                'N-P differences combine condition, order and experience.',
                'Recovered C preserves recorded outputs; execution crossed a process interruption and calendar day.',
                'Replay and legal output checks do not validate all free-text judgments.']}
    total=Decimal(0)
    all_costs_known=True
    for arm,folder in arms.items():
        folder=Path(folder);config=read(folder/'config.json');summary=read(folder/'summary.json')
        assert summary['status']=='completed'
        audit=audit_saved_run(folder)
        calls=rows(folder/'calls.jsonl');events=rows(folder/'events.jsonl');initial=events[0]
        model=_RecordedModel(calls)
        args={} if arm=='A' else {'prompt_revision':config['prompt_revision'], 'previous_x_mode':config.get('previous_x_mode','retain')}
        task=RUNNERS[arm](World(initial['group_id'],initial['participants'],initial['seed']),model,
                          RunConfig(**config['settings']),**args)
        task.run()
        assert next(model.calls,None) is None
        assert task.world.audit_log()==events
        assert task.world.trajectory()==rows(folder/'trajectory.jsonl')
        for actual,saved in zip(task.calls,calls):
            for key in ('call_id','participant_id','phase','round','stage','step','attempt','input_bytes',
                        'request','status','action','judgment','normalizations','error'):
                assert actual.get(key)==saved.get(key),(arm,key)
        assert len(task.calls)==len(calls)
        if arm!='A':
            assert task.state_transitions==rows(folder/'state_transitions.jsonl')
            assert all(s.snapshot()['version']==50 for s in task.states.values())
        audit['exact_request_and_retry_replay']=True
        result['audits'][arm]=audit
        cost=Decimal(0)
        costs_known=True
        for call in calls:
            response=call['response']
            if response['cost_cny_upper'] is None:
                costs_known=False
                continue
            amount=cost_upper(response['input_tokens'],response['output_tokens'],response['cached_input_tokens'])
            assert amount==Decimal(response['cost_cny_upper'])
            cost+=amount
        total+=cost
        all_costs_known=all_costs_known and costs_known
        people,peers=load_agent(folder/'trajectory.jsonl')
        histogram,_=group_histograms(people)
        distances=w1_cells(histogram.mean(axis=0),reference)
        means={phase:float(people.loc[people.phase==phase,'contribution'].mean()) for phase in ('N','P')}
        result['arms'][arm]={'source':str(folder.resolve()),'groups':int(people.group_id.nunique()),
            'prompt_revision':config.get('prompt_revision','direct-decision-baseline'),
            'phase_means':means,'phase_gap':means['P']-means['N'],
            'mean_round_normalized_W1':float(distances.mean()),
            'N_normalized_W1':float(distances[:10].mean()),'P_normalized_W1':float(distances[10:].mean()),
            'total_assigned_punishment':int(peers.assigned_points.sum()),
            'round_means':[{'phase':phase,'round':int(r),'mean':float(g.contribution.mean())}
                           for (phase,r),g in people.groupby(['phase','round'])],
            'contribution_counts':{phase:{str(k):int(v) for k,v in people.loc[people.phase==phase,'contribution'].value_counts().sort_index().items()} for phase in ('N','P')},
            'behavior':behavior_statistics(people,peers),'recorded_responses':len(calls),
            'invalid_outputs':sum(c['status']=='invalid_output' for c in calls),
            'known_cost_upper_cny_including_inherited':str(cost) if costs_known else None,
            'returned_models':sorted({c['response']['returned_model'] for c in calls}),
            'fingerprints':sorted({str(c['response']['system_fingerprint']) for c in calls})}
    result['known_trial_cost_upper_cny']=str(total) if all_costs_known else None
    return result

@artifacts.compact_cli
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for arm in RUNNERS:parser.add_argument('--'+arm,required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    if args.output.exists():raise FileExistsError('Preserve the previous comparison')
    result=review({arm:getattr(args,arm) for arm in RUNNERS})
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'human':result['human_phase_means'],'arms':{
        a:{k:v for k,v in row.items() if k in ('phase_means','mean_round_normalized_W1','total_assigned_punishment','recorded_responses','invalid_outputs')}
        for a,row in result['arms'].items()},'known_trial_cost_upper_cny':result['known_trial_cost_upper_cny']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
