"""Read-only reconstruction of the single-cycle I-B engineering run.

No Runner, Scene, Decision or network imports/calls. Not a generic scientific
evaluator. Writes only a new analysis directory outside the source run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2] / '02_agent_world' / 'python_runtime'
sys.path.insert(0, str(RUNTIME))
from common import digest, file_hash, read_json, require, write_json
from trace_readback import read_trace


def inventory(root):
    return {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob('*')) if p.is_file()}


def validate(source: Path) -> tuple[dict, dict]:
    source = source.resolve()
    original_hashes = inventory(source)
    cycle = source / 'cycle_001'
    top = read_json(source / 'runtime_validation_manifest.json')
    run = read_json(cycle / 'run_manifest.json')
    checks = []

    def check(name, condition, evidence):
        checks.append({'check': name, 'passed': bool(condition), 'evidence': evidence})
        require(condition, 'I-C check failed: ' + name)

    def verify_index(root, index):
        for relative, meta in index.items():
            path = (root / relative).resolve()
            require(path.is_relative_to(root.resolve()), 'Artifact path escapes source')
            require(file_hash(path) == meta['sha256'] and path.stat().st_size == meta['bytes'],
                    'Artifact integrity mismatch: ' + relative)

    verify_index(source, top['artifacts'])
    verify_index(cycle, run['artifacts'])
    check('source_artifact_indices', True, {'top_entries': len(top['artifacts']), 'cycle_entries': len(run['artifacts'])})
    check('declared_scope', run['population_count'] == 1 and run['kernel'] == 'phase_ib_runtime_smoke_v1'
          and run['status'] == 'completed', 'single-agent single-cycle completed engineering fixture only')
    trace = read_trace(cycle / 'events.jsonl')
    check('hash_chain_and_request_result_links', trace['final_event_hash'] == run['final_event_hash']
          and trace['gaps'] == [] and len(trace['chains']) == 1, {'events': trace['event_count'], 'gaps': trace['gaps']})
    rows = [json.loads(line) for line in (cycle / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    groups = {}
    for row in rows:
        groups.setdefault(row['type'], []).append(row)

    def one(kind):
        require(len(groups.get(kind, [])) == 1, 'Expected one ' + kind)
        return groups[kind][0]

    pdi, api, transport, model = (one(k) for k in ('decision_input', 'api_request', 'api_transport_completed', 'decision_output'))
    req, tx, res, world_event = (one(k) for k in ('action_request', 'world_transaction', 'action_result', 'world_event'))
    observations = groups['observation_delivered']
    check('observation_count', len(observations) == 2, [r['payload']['observation_id'] for r in observations])
    o0, o1 = observations
    actor, rid = req['payload']['actor_id'], req['payload']['request_id']
    context = pdi['payload']['context']
    messages = api['payload']['messages']
    order = [o0, pdi, api, transport, model, req, tx, world_event, res, o1]
    check('recorded_order', [r['seq'] for r in order] == sorted(r['seq'] for r in order),
          [{'seq': r['seq'], 'type': r['type']} for r in order])
    check('prepared_to_dispatched', pdi['payload']['messages'] == messages
          and digest(messages) == pdi['payload']['messages_sha256'] == api['payload']['messages_sha256'] == transport['payload']['messages_sha256']
          and digest(context) == api['payload']['context_sha256']
          and json.loads(messages[1]['content']) == context,
          {'context_sha256': digest(context), 'messages_sha256': digest(messages)})
    population = read_json(source / 'population.loaded.json')
    member = population[0]
    initial = read_json(cycle / 'agents.initial.json')
    final = read_json(cycle / 'agents.final.json')
    check('authorized_persona_and_frozen_P_M', len(population) == 1 and member['alias'] == actor
          and context['P'] == member['view'] and initial[actor]['P'] == final[actor]['P'] == member['initial']['P']
          and initial[actor]['M'] == final[actor]['M'] == context['M'] == member['initial']['M0'],
          {'authorized_view_sha256': digest(member['view']), 'P_sha256': digest(initial[actor]['P']),
           'M_sha256': digest(initial[actor]['M'])})
    check('legal_current_information', context['Observation'] == o0['payload'] and context['episodes'] == []
          and context['Observation']['visibility'] == [actor], 'Opening Observation is the delivered record; no retrieved history in this cycle')
    decoded = json.loads(model['payload']['raw'])
    check('response_request_parse', decoded['action'] == req['payload']['action']
          and decoded['arguments'] == req['payload']['arguments']
          and req['payload']['basis_observation_id'] == o0['payload']['observation_id'],
          {'request_id': rid, 'action': req['payload']['action'], 'arguments': req['payload']['arguments']})
    world_initial, world_final = read_json(cycle / 'world.initial.json'), read_json(cycle / 'world.final.json')
    boundary = one('world_round_boundary')['payload']
    transaction, result = tx['payload'], res['payload']
    check('world_snapshot_continuity', world_initial == boundary['before']
          and boundary['after'] == transaction['before'] and transaction['after'] == world_final,
          {'initial_version': world_initial['version'], 'decision_version': boundary['after']['version'], 'final_version': world_final['version']})
    before, after = transaction['before'], transaction['after']
    delta = {key: {'before': before[key], 'after': after[key], 'delta': after[key] - before[key]}
             for key in ('resource', 'container')}
    check('execution_not_request_or_rationale', result == transaction['results'][0] and result['accepted'] is True
          and result['executed_action'] == req['payload']['action'] == 'MOVE'
          and result['effects']['parameters_executed'] == req['payload']['arguments'] == {'amount': 1}
          and result['request_id'] == rid and result['result_id'] == 'result:' + rid
          and delta['resource']['delta'] == result['effects']['resource_delta'] == -1
          and delta['container']['delta'] == result['effects']['container_delta'] == 1
          and before['resource'] + before['container'] == after['resource'] + after['container'] == 2,
          {'result_id': result['result_id'], 'world_changes': delta})
    check('recorded_execution_permission', before['owner'] == actor and before['permission']['MOVE'] is True
          and before['resource'] >= req['payload']['arguments']['amount'],
          'Check logged preconditions and effects; no World replay or model rationale used')
    ev = world_event['payload']
    feedback = o1['payload']
    check('world_event_and_feedback_delivery', transaction['world_events'] == [ev] == after['events']
          and ev['request_id'] == rid and ev['result_id'] == result['result_id']
          and ev['payload'] == result['effects'] and ev['visibility'] == [actor]
          and feedback['content']['events'] == [ev] and feedback['content']['action_result'] == result
          and feedback['recipient'] == actor and feedback['visibility'] == [actor]
          and feedback['content']['self'] == {'resource': after['resource'], 'container': after['container']},
          {'event_id': ev['event_id'], 'feedback_observation_id': feedback['observation_id']})
    episodes = read_json(cycle / 'episodes.json')
    check('episodes_are_delivered_observations', episodes == {actor: [r['payload'] for r in observations]}, 'episodes.json == observation_delivered records')
    latest_x = initial[actor]['X']
    x_changes = []
    for row in groups['state_transition']:
        transition = row['payload']
        check('X_continuity_seq_' + str(row['seq']), transition['agent_id'] == actor and transition['before'] == latest_x,
              {'writer': transition['writer'], 'cause_refs': transition['cause_refs']})
        if row['seq'] < pdi['seq']:
            check('X_decision_view', context['X']['version'] == transition['after']['version']
                  and context['X']['cognition_revision'] == transition['after']['cognition_revision']
                  and context['X']['judgments'] == transition['after']['judgments'], pdi['seq'])
        latest_x = transition['after']
        x_changes.append({'event_seq': row['seq'], 'writer': transition['writer'], 'cause_refs': transition['cause_refs'],
                          'before_sha256': digest(transition['before']), 'after_sha256': digest(latest_x),
                          'observation_version': latest_x['version'], 'cognition_revision': latest_x['cognition_revision'],
                          'judgment_count': len(latest_x['judgments']), 'task_ids': list(latest_x['tasks']),
                          'epistemic_boundary': 'Agent reports/tasks are not verified beliefs or causal mechanisms'})
    check('X_final_snapshot', latest_x == final[actor]['X'] and latest_x['last_observation'] == feedback
          and latest_x['observed_self'] == feedback['content']['self'], 'X final state includes the legally delivered feedback')
    terminal = one('run_completed')
    check('completion_hashes', rows[-1] == terminal and digest(world_final) == terminal['payload']['world_hash']
          and digest(final[actor]) == terminal['payload']['agent_hashes'][actor], 'Terminal event matches persisted World/Agent snapshots')
    reconstructed = {'observation_id': o0['payload']['observation_id'], 'prepared_decision_hash': digest(context),
                     'prepared_messages_hash': pdi['payload']['messages_sha256'], 'dispatched_messages_hash': digest(messages),
                     'request_id': rid, 'gm_execution_log_seq': tx['seq'], 'result_id': result['result_id'],
                     'world_event_ids': [ev['event_id']], 'next_observation_id': feedback['observation_id']}
    check('independent_reconstruction_matches_IB_chain', reconstructed == top['runtime_validation']['chain'] == run['runtime_validation']['chain'],
          'Chain assembled from raw log records first, then compared with both I-B manifests')
    no_next = trace['chains'][0]['continuation_status'] == 'no_next_input_recorded'
    check('explicit_end_of_trace', no_next and len(groups['decision_input']) == 1,
          'Run ends after feedback/X update. No second model input/action exists; no continuation fabricated.')
    check('source_unchanged', original_hashes == inventory(source), 'All source file hashes unchanged after analysis')
    trajectory = {'schema_version': 'single_cycle_behavioral_trace_v1', 'agent': actor,
                  'time_semantics': 'log sequence + recorded round/phase/world_version; no invented time=0/1',
                  'source_events': str(cycle / 'events.jsonl'), 'chain': reconstructed,
                  'trajectory': [
                      {'stage': 'observation', 'event_seq': o0['seq'], 'observation_id': reconstructed['observation_id'],
                       'received_information': o0['payload']['content']},
                      {'stage': 'decision_context', 'event_seq': pdi['seq'], 'context_hash': digest(context),
                       'messages_hash': digest(messages), 'full_content_location': 'events.jsonl payload.context/messages',
                       'authorized_persona_hash': digest(context['P']), 'retrieved_episodes': context['episodes']},
                      {'stage': 'model_invocation', 'event_seq': api['seq'], 'response_event_seq': model['seq'],
                       'model': model['payload']['metadata']['model'],
                       'provider_response_id': model['payload']['metadata'].get('provider_response_id'),
                       'provider_reported_usage': model['payload']['metadata']['usage']},
                      {'stage': 'action_request', 'event_seq': req['seq'], 'request': req['payload']},
                      {'stage': 'execution_result', 'gm_event_seq': tx['seq'], 'result_event_seq': res['seq'], 'result': result},
                      {'stage': 'world_transition', 'event_seq': tx['seq'], 'changes': delta},
                      {'stage': 'feedback', 'source_event_seq': world_event['seq'], 'event_id': ev['event_id'], 'received': True,
                       'recipient': actor, 'delivery_event_seq': o1['seq']},
                      {'stage': 'next_observation', 'event_seq': o1['seq'], 'observation_id': feedback['observation_id'],
                       'received_information': feedback['content']}
                  ], 'X_transitions': x_changes, 'next_model_decision': None,
                  'continuation_status': 'not_observed_run_ended_after_feedback',
                  'limitations': ['No inference about model attention/belief or cognition causality',
                                  'No behavioral-choice evidence: only one executable action offered']}
    report = {'status': 'Phase I-C Engineering Validated (single-cycle trace reconstruction scope)',
              'scope': 'one real I-B Agent/decision/MOVE through next Observation and X update only',
              'checks': checks, 'source_directory': str(source), 'source_file_hashes': original_hashes,
              'analysis_code_hashes': {str(p): file_hash(p) for p in (Path(__file__), RUNTIME / 'trace_readback.py', RUNTIME / 'common.py')},
              'event_count': len(rows), 'request_count': 1, 'result_count': 1, 'observation_count': 2,
              'external_api_calls_in_this_analysis': 0, 'simulation_runs_in_this_analysis': 0,
              'trace_readback': trace, 'phase_I_globally_complete': False,
              'not_covered': ['feedback affecting a subsequent decision/action', 'multi-agent/private cross-agent cases',
                              'rejected/failed runtime branches', 'longitudinal traces', 'scientific behavioral validation'],
              'source_integrity_boundary': 'Hashes establish internal consistency with supplied manifests, not independent provider authentication'}
    return report, trajectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    require(not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output),
            'Choose a new analysis directory outside the source run')
    report, trajectory = validate(source)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'validation_report.json', report)
    write_json(output / 'behavioral_trace.json', trajectory)
    print(json.dumps({'status': report['status'], 'checks_passed': len(report['checks']),
                      'event_count': report['event_count'], 'output': str(output),
                      'next_model_decision': 'not observed; run ended after feedback',
                      'API_calls': 0, 'simulation_runs': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
