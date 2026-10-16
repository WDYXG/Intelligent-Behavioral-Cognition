"""Two-decision acceptance harness around the existing runner.run_one.

No alternate simulation loop, extra cognition call, fallback or science condition.
Only main/run_check dispatches; missing credentials fail before any network call.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import uuid
from pathlib import Path

from common import STEP, digest, file_hash, read_json, require, sanitized_error, utc_now, validate_config, write_json
from cognition import TERMINAL
from llm import CostLedger, preflight
from population import load_population
from runner import run_one
from run_runtime_smoke import RuntimeAuditLog, resolve_smoke


def resolve_readiness(path):
    spec = read_json(path)
    require(spec['schema_version'] == 'two_decision_readiness_v1', 'Wrong readiness config')
    base = (path.parent / spec['base_smoke_config']).resolve()
    require(file_hash(base) == spec['base_smoke_config_sha256'], 'Pinned smoke config changed')
    smoke, cfg, _ = resolve_smoke(base)
    require(spec['budget']['max_calls'] == 2 and 0 < spec['budget']['max_usd'] <= 0.04,
            'Readiness capped at two attempts and USD 0.04; no automatic rerun')
    require(spec['budget']['max_tokens'] == 2 * (cfg['api']['max_input_tokens'] + cfg['api']['max_output_tokens']),
            'Token reservation must cover exactly two existing per-call caps')
    cfg.update(experiment_id='two_decision_readiness_v1', kernel='two_decision_readiness_v1',
               scene={'adapter': 'scene_two_decision:TwoDecisionScene'}, rounds=2)
    cfg['budget'] = copy.deepcopy(spec['budget'])
    cfg['time_alignment']['simulation_time'] = 'two consecutive abstract engineering cycles; not real respondent behavior'
    validate_config(cfg)
    return spec, smoke, cfg, base


def readiness_gates(member):
    def validate(world, agents, log, ledger):
        rows = log.rows
        def kinds(kind):
            return [r for r in rows if r['type'] == kind]
        def pair(kind):
            found = kinds(kind)
            require(len(found) == 2, f'Readiness requires exactly two {kind}')
            return found
        prepared, budgets, dispatched = pair('decision_input'), pair('context_budget_check'), pair('api_request')
        transports, outputs, requests = pair('api_transport_completed'), pair('decision_output'), pair('action_request')
        results, transactions = pair('action_result'), pair('world_transaction')
        observations, transitions = kinds('observation_delivered'), kinds('state_transition')
        require(len(observations) == 4, 'Expected opening/feedback for each cycle')
        actor = member['alias']
        checks, chains = {}, []
        checks['two_calls_no_retry'] = ledger.calls == 2 and all(r['payload']['attempt'] == 1 for r in dispatched)
        checks['no_failure_events'] = not any(r['type'] in ('api_error', 'decision_failed', 'context_phase_blocked') for r in rows)
        checks['continuous_X_chain'] = all(a['payload']['after'] == b['payload']['before'] for a, b in zip(transitions, transitions[1:]))
        checks['final_X_matches_trace'] = transitions[-1]['payload']['after'] == agents[actor].X
        checks['P_M_frozen'] = agents[actor].snapshot()['P'] == member['initial']['P'] and agents[actor].snapshot()['M'] == member['initial']['M0']
        for i in range(2):
            p, b, d, t, out, req, result, tx = [items[i]['payload'] for items in
                (prepared, budgets, dispatched, transports, outputs, requests, results, transactions)]
            ctx = p['context']
            opening, feedback = [o['payload'] for o in observations[2*i:2*i+2]]
            before_pd = [r for r in transitions if r['seq'] < prepared[i]['seq']][-1]
            x = before_pd['payload']['after']
            records = [ctx['Observation']['content'], *ctx['episodes']]
            ev = [e for record in records for e in record.get('events', [])]
            rs = [record['action_result'] for record in records if record.get('action_result')]
            delivered = {r['payload']['observation_id']: r['payload'] for r in observations if r['seq'] < prepared[i]['seq']}
            historical_ok = all(h['observation_id'] in delivered and
                all(h[k] == delivered[h['observation_id']][k] for k in ('round', 'phase', 'world_version')) and
                all(e in delivered[h['observation_id']]['content']['events'] for e in h['events']) and
                (h['action_result'] is None or h['action_result'] == delivered[h['observation_id']]['content']['action_result'])
                for h in ctx['episodes'])
            closed = [key for key, task in x['tasks'].items() if task['status'] in TERMINAL]
            recent = set(sorted(closed, key=lambda key: x['tasks'][key]['updated']['cognition_revision'])[-2:])
            task_view = {key: {k: copy.deepcopy(v) for k, v in task.items() if k not in ('history', 'attempts', 'last_feedback')}
                         for key, task in x['tasks'].items() if task['status'] not in TERMINAL or key in recent}
            for key, view in task_view.items():
                last = x['tasks'][key].get('last_feedback')
                view['last_result'] = ({k: last.get(k) for k in ('request_id', 'accepted', 'rejection_reason', 'possible_next_steps')} if last else None)
            prefix = f'decision_{i}_'
            checks[prefix+'hash'] = (p['messages'] == d['messages'] and
                digest(d['messages']) == p['messages_sha256'] == d['messages_sha256'] == t['messages_sha256'] and
                digest(ctx) == p['context_sha256'] == d['context_sha256'] == b['context_sha256'])
            checks[prefix+'X_provenance'] = (digest(x) == b['local_state']['X_sha256'] and
                all(ctx['X'].get(k) == x.get(k) for k in ('version', 'cognition_revision', 'current_round', 'judgments', 'interpretation_status')) and
                ctx['X']['tasks'] == task_view and ctx['X']['used_task_ids'] == list(x['tasks']) and
                ctx['X']['archived_terminal_task_count'] == len(closed) - len(recent) and
                x['last_observation'] == ctx['Observation'] == opening)
            checks[prefix+'authorization'] = (ctx['P'] == member['view'] and ctx['M'] == member['initial']['M0'] and historical_ok and
                all(actor in e['visibility'] for e in ev) and all(r['agent_id'] == actor for r in rs) and
                opening['recipient'] == actor and opening['visibility'] == [actor])
            checks[prefix+'unique_records'] = (len(ev) == len({e['event_id'] for e in ev}) and len(rs) == len({r['request_id'] for r in rs}))
            checks[prefix+'budget'] = b['budget']['fits'] and d['context_input_reservation'] == b['budget']['input_reservation']
            parsed = json.loads(out['raw'])
            checks[prefix+'response_to_request'] = (req['actor_id'] == actor and req['action'] == parsed['action'] and
                req['arguments'] == parsed['arguments'] and req['basis_observation_id'] == opening['observation_id'] and t['http_success'])
            checks[prefix+'execution_feedback'] = (result['accepted'] and result == tx['results'][0] and
                result['request_id'] == req['request_id'] and result['result_id'] == 'result:' + req['request_id'] and
                tx['before']['resource'] - tx['after']['resource'] == tx['after']['container'] - tx['before']['container'] == 1 and
                result['effects']['authorization'] == 'owner_and_MOVE_permission_and_resources_checked' and
                feedback['content']['action_result'] == result and feedback['content']['events'] == tx['world_events'] and
                len(tx['world_events']) == 1 and feedback['content']['self'] ==
                {'resource': tx['after']['resource'], 'container': tx['after']['container']} and
                all(e['request_id'] == req['request_id'] and e['result_id'] == result['result_id'] and e['visibility'] == [actor] for e in tx['world_events']) and
                feedback['recipient'] == actor and feedback['visibility'] == [actor])
            seqs = [observations[2*i]['seq'], before_pd['seq'], prepared[i]['seq'], budgets[i]['seq'], dispatched[i]['seq'],
                    transports[i]['seq'], outputs[i]['seq'], requests[i]['seq'], transactions[i]['seq'], results[i]['seq'], observations[2*i+1]['seq']]
            checks[prefix+'chronology'] = seqs == sorted(set(seqs))
            chains.append({'cycle': i+1, 'opening_observation_id': opening['observation_id'],
                'prepared_context_sha256': p['context_sha256'], 'prepared_messages_sha256': p['messages_sha256'],
                'dispatch_messages_sha256': d['messages_sha256'], 'api_request_log_seq': dispatched[i]['seq'],
                'provider_response_id': out['metadata'].get('provider_response_id'), 'provider_usage': out['metadata'].get('usage'),
                'request_id': req['request_id'], 'result_id': result['result_id'],
                'feedback_event_ids': [e['event_id'] for e in tx['world_events']],
                'feedback_observation_id': feedback['observation_id'], 'budget': b['budget'],
                'X_at_preparation_sha256': digest(x), 'X_version': x['version'], 'X_cognition_revision': x['cognition_revision']})
        feedback0 = observations[1]['payload']
        pd1 = prepared[1]['payload']['context']
        matching = [h for h in pd1['episodes'] if h['observation_id'] == feedback0['observation_id']]
        checks['feedback0_in_PD1'] = (len(matching) == 1 and matching[0]['action_result'] == results[0]['payload'] and
            matching[0]['events'] == feedback0['content']['events'] and matching[0]['context_role'] == 'mandatory_new_delivery')
        checks['feedback_before_next_opening_and_PD'] = observations[1]['seq'] < observations[2]['seq'] < prepared[1]['seq']
        feedback_updates = [r for r in transitions if r['payload']['cause_refs'] == [feedback0['observation_id']]]
        checks['feedback_X_update'] = (len(feedback_updates) == 1 and
            observations[1]['seq'] < feedback_updates[0]['seq'] < observations[2]['seq'] and
            feedback_updates[0]['payload']['after']['last_observation'] == feedback0 and
            feedback_updates[0]['payload']['after']['observed_self'] == feedback0['content']['self'])
        checks['final_world'] = world.snapshot() == transactions[1]['payload']['after']
        return {'passed': all(checks.values()), 'checks': checks, 'chains': chains,
            'handoff': {'result_id_0': results[0]['payload']['result_id'],
                'feedback_event_ids': [e['event_id'] for e in feedback0['content']['events']],
                'feedback_observation_id': feedback0['observation_id'],
                'intervening_opening_observation_id': pd1['Observation']['observation_id'],
                'feedback_X_sha256': digest(feedback_updates[0]['payload']['after']) if feedback_updates else None,
                'feedback_X_version': feedback_updates[0]['payload']['after']['version'] if feedback_updates else None,
                'entry_into_PD1': 'mandatory_new_delivery in episodes; current Observation is the next opening',
                'prepared_context_sha256_1': prepared[1]['payload']['context_sha256'], 'request_id_1': requests[1]['payload']['request_id']},
            'scope': 'feedback-to-next-decision runtime readiness only; exposure does not establish causal influence or belief',
            'dedup_scope': 'one full event/result record across Observation and episodes; task last_result is a provenance/status reference, not a second full result',
            'cost': ledger.snapshot()}
    return validate


def run_check(config_path):
    stamp = utc_now().replace(':', '-').replace('.', '-')
    directory = STEP / '03_experiment/runtime_validation' / f'two_decision_{stamp}_{uuid.uuid4().hex[:8]}'
    directory.mkdir(parents=True)
    manifest = {'status': 'Not Validated', 'stage': 'read_config', 'started_at': utc_now(), 'runs': [],
                'scope': 'pre-experiment two-decision readiness; not a new Phase or scientific experiment'}
    ledger = None
    try:
        spec, smoke, cfg, base = resolve_readiness(config_path)
        write_json(directory / 'readiness_config.json', spec)
        write_json(directory / 'base_smoke_config.json', read_json(base))
        write_json(directory / 'resolved_runtime_config.json', cfg)
        manifest['source_config_sha256'] = file_hash(config_path)
        population, source = load_population(cfg, config_path.parent)
        selected = [m for m in population if m['alias'] == smoke['selected_alias']]
        require(len(selected) == 1, 'Selected existing Persona not found')
        manifest['population_source'] = source
        write_json(directory / 'population.loaded.json', selected)
        snapshot = directory / 'code_snapshot'
        snapshot.mkdir()
        manifest['code_hashes'] = {}
        for path in sorted(Path(__file__).parent.glob('*.py')):
            shutil.copyfile(path, snapshot / path.name)
            manifest['code_hashes'][path.name] = file_hash(path)
        manifest['spec_hashes'] = {name: file_hash(STEP / 'specs' / name) for name in
            ('Architecture_Spec.md', 'Experiment_Spec.md', 'Data_Contract.md')}
        manifest['stage'] = 'API_preflight_no_network'
        resolved = preflight(cfg, population_count=1)
        ledger = CostLedger(cfg)
        manifest['stage'] = 'shared_runner_two_cycles'
        run = run_one(directory / 'two_cycles', selected, cfg, resolved, ledger, cfg['seed'], 1,
                      log_factory=RuntimeAuditLog, runtime_validator=readiness_gates(selected[0]))
        manifest['runs'].append({'path': 'two_cycles/run_manifest.json', 'status': run['status']})
        manifest['readiness'] = run.get('runtime_validation')
        require(run['status'] == 'completed' and run.get('runtime_validation', {}).get('passed'),
                'Readiness not validated; inspect two_cycles events/run_manifest; no automatic rerun')
        require(file_hash(Path(source['path'])) == source['sha256'], 'Source manifest changed')
        manifest.update(status='Feedback-to-next-decision Runtime Readiness Validated', stage='finished')
    except Exception as exc:
        manifest['error'] = sanitized_error(exc)
        write_json(directory / 'failure.json', {'stage': manifest['stage'], **manifest['error']})
    finally:
        manifest['finished_at'] = utc_now()
        manifest['cost'] = ledger.snapshot() if ledger else {'calls': 0, 'reason': 'preflight_not_completed'}
        manifest['external_api_attempts'] = manifest['cost']['calls']
        manifest['artifacts'] = {p.relative_to(directory).as_posix(): {'sha256': file_hash(p), 'bytes': p.stat().st_size}
            for p in sorted(directory.rglob('*')) if p.is_file()}
        write_json(directory / 'readiness_manifest.json', manifest)
    return directory, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    directory, manifest = run_check(args.config.resolve())
    print(json.dumps({'status': manifest['status'], 'output_directory': str(directory),
        'external_api_attempts': manifest['external_api_attempts'], 'error': manifest.get('error')}, ensure_ascii=False, indent=2))
    return 0 if manifest.get('stage') == 'finished' else 1


if __name__ == '__main__':
    raise SystemExit(main())
