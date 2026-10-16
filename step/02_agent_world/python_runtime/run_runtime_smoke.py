"""Phase I-B entry using the SAME runner.run_one/run_phase, Agent, GM and client.

No community initialization, science conditions, implicit fallback or I-C readback.
Each invocation creates a new immutable run directory, including preflight failures.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import uuid
from pathlib import Path

from common import STEP, EventLog, digest, file_hash, read_json, require, sanitized_error, utc_now, validate_config, write_json
from population import load_population
from llm import CostLedger, preflight
from runner import run_one


class RuntimeAuditLog(EventLog):
    """Persistent standard hash-chain log plus same-run gate observations."""
    def __init__(self, path):
        super().__init__(path)
        self.rows = []

    def add(self, kind, payload):
        super().add(kind, payload)
        self.rows.append({'type': kind, 'payload': copy.deepcopy(payload), 'seq': self.sequence})


def resolve_smoke(path: Path) -> tuple[dict, dict, Path]:
    spec = read_json(path)
    require(spec['schema_version'] == 'phase_ib_runtime_smoke_v1', 'Wrong smoke configuration')
    require(set(spec['api_overrides']) == {'concurrency', 'max_retries'}
            and spec['api_overrides'] == {'concurrency': 1, 'max_retries': 0}, 'Smoke cannot enlarge input limits or retry implicitly')
    require(spec['budget']['max_calls'] == 1 and 0 < spec['budget']['max_usd'] <= 0.02,
            'Smoke capped at one API attempt and USD 0.02 per invocation')
    base_path = (path.parent / spec['base_runtime_config']).resolve()
    require(file_hash(base_path) == spec['base_runtime_config_sha256'], 'Base config changed; review before updating smoke hash')
    base = read_json(base_path)
    cfg = copy.deepcopy(base)
    # Read the pinned source batch with its original 12-person integrity contract;
    # only selected_alias is instantiated in the smoke World or sent to the model.
    cfg['population']['initialization_manifest'] = str((base_path.parent / base['population']['initialization_manifest']).resolve())
    for name in ('daily_life', 'world', 'notes'):
        cfg.pop(name, None)
    cfg.update(experiment_id='phase_ib_runtime_smoke_v1', kernel='phase_ib_runtime_smoke_v1',
               scene={'adapter': 'scene_runtime_smoke:RuntimeSmokeScene'}, rounds=1, repetitions=1, seed=spec['seed'])
    cfg['api'].update(spec['api_overrides'])
    cfg['budget'] = copy.deepcopy(spec['budget'])
    cfg['observation'].update(scope='fixture_owner_only', public_history_window=0)
    cfg['time_alignment'] = {'mode': 'counterfactual_runtime_fixture_from_2018_snapshot',
                            'source_time_policy': base['time_alignment']['source_time_policy'],
                            'simulation_time': 'one abstract engineering cycle; not real respondent behavior'}
    validate_config(cfg)
    return spec, cfg, base_path


def runtime_gates(member):
    def validate(world, agents, log, ledger):
        by_kind = {}
        for row in log.rows:
            by_kind.setdefault(row['type'], []).append(row)
        def one(kind):
            require(len(by_kind.get(kind, [])) == 1, f'I-B expected one {kind}')
            return by_kind[kind][0]['payload']
        prepared, budget = one('decision_input'), one('context_budget_check')
        dispatched, transport, output = one('api_request'), one('api_transport_completed'), one('decision_output')
        request, result, transaction = one('action_request'), one('action_result'), one('world_transaction')
        observations = [r['payload'] for r in by_kind.get('observation_delivered', [])]
        require(len(observations) == 2, 'I-B expected opening and feedback Observation only')
        opening, feedback = observations
        actor, rid = member['alias'], request['request_id']
        after, before = transaction['after'], transaction['before']
        changes = [r['payload'] for r in by_kind.get('world_event', [])]
        decoded = json.loads(output['raw'])
        gates = {
            'B1': prepared['messages'] == dispatched['messages']
                  and digest(dispatched['messages']) == prepared['messages_sha256'] == transport['messages_sha256']
                  and prepared['context_sha256'] == dispatched['context_sha256'],
            'B2': ledger.calls == 1 and transport['http_success'] is True
                  and output['metadata']['latency_seconds'] >= 0,
            'B3': request['actor_id'] == actor and request['action'] == decoded['action']
                  and request['arguments'] == decoded['arguments'] and bool(rid),
            'B4': before['owner'] == actor and before['permission']['MOVE'] is True
                  and result['effects'].get('authorization') == 'owner_and_MOVE_permission_and_resources_checked',
            'B5': result['accepted'] is True and result['executed_action'] == 'MOVE'
                  and result['request_id'] == rid and result['result_id'] == 'result:' + rid
                  and result == transaction['results'][0]
                  and before['resource'] - after['resource'] == after['container'] - before['container'] == 1
                  and result['effects']['parameters_executed'] == request['arguments'] == {'amount': 1}
                  and world.snapshot() == after,
            'B6': feedback['recipient'] == actor and feedback['visibility'] == [actor]
                  and feedback['content']['action_result'] == result
                  and len(changes) == 1 and feedback['content']['events'] == changes
                  and changes[0]['request_id'] == rid and changes[0]['result_id'] == result['result_id']
                  and changes[0]['visibility'] == [actor]
                  and feedback['content']['self'] == {'resource': after['resource'], 'container': after['container']},
            'B7': budget['budget']['fits'] and ledger.unknown_usage_calls == 0
                  if output['metadata'].get('usage') is not None else budget['budget']['fits'],
            'B8': request['basis_observation_id'] == opening['observation_id'] == prepared['context']['Observation']['observation_id']
                  and opening['observation_id'] != feedback['observation_id']
                  and prepared['context']['P'] == member['view']
                  and agents[actor].snapshot()['P'] == member['initial']['P']
                  and agents[actor].snapshot()['M'] == member['initial']['M0']
                  and len(agents[actor].episodes) == 2}
        return {'passed': all(gates.values()), 'gates': gates,
                'scope': 'minimal real runtime closed-loop scope tested in this run; not I-C or behavioral validation',
                'chain': {'observation_id': opening['observation_id'], 'prepared_decision_hash': prepared['context_sha256'],
                          'prepared_messages_hash': prepared['messages_sha256'], 'dispatched_messages_hash': dispatched['messages_sha256'],
                          'request_id': rid, 'gm_execution_log_seq': by_kind['world_transaction'][0]['seq'],
                          'result_id': result['result_id'], 'world_event_ids': [e['event_id'] for e in changes],
                          'next_observation_id': feedback['observation_id']},
                'backend': 'OpenAI-compatible HTTPS', 'model_requested': dispatched['model'],
                'model_reported': output['metadata']['model'],
                'provider_response_id': output['metadata'].get('provider_response_id'),
                'provider_reported_usage': output['metadata'].get('usage'),
                'latency_seconds': output['metadata']['latency_seconds'],
                'local_budget': budget['budget'], 'cost': ledger.snapshot(),
                'I_C_performed': False, 'negative_runtime_case_performed': False}
    return validate


def run_smoke(config_path: Path):
    stamp = utc_now().replace(':', '-').replace('.', '-')
    directory = STEP / '03_experiment' / 'runtime_validation' / f'phase_ib_{stamp}_{uuid.uuid4().hex[:8]}'
    directory.mkdir(parents=True)
    manifest = {'status': 'Phase I-B remains Not Validated', 'stage': 'read_config',
                'scope': 'one-agent one-cycle runtime engineering fixture; not scientific experiment',
                'started_at': utc_now(), 'runs': [], 'external_api_attempts': 0, 'I_C_performed': False}
    ledger = None
    try:
        spec, cfg, base_path = resolve_smoke(config_path)
        write_json(directory / 'smoke_config.json', spec)
        write_json(directory / 'resolved_runtime_config.json', cfg)
        manifest['source_config_hash'] = file_hash(config_path)
        manifest['base_config_hash'] = file_hash(base_path)
        manifest['stage'] = 'load_existing_persona_no_projection'
        population, source = load_population(cfg, config_path.parent)
        selected = [m for m in population if m['alias'] == spec['selected_alias']]
        require(len(selected) == 1, 'Selected alias not in existing frozen batch')
        manifest['population_source'] = source
        manifest['selected_alias'] = selected[0]['alias']
        manifest['selected_origin'] = selected[0]['origin']
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
        manifest['backend'] = {'model': resolved['model'], 'estimate': resolved['estimate']}
        ledger = CostLedger(cfg)
        manifest['stage'] = 'shared_runner_runtime_cycle'
        run = run_one(directory / 'cycle_001', selected, cfg, resolved, ledger, cfg['seed'], 1,
                      log_factory=RuntimeAuditLog, runtime_validator=runtime_gates(selected[0]))
        manifest['runs'].append({'path': 'cycle_001/run_manifest.json', 'status': run['status']})
        manifest['runtime_validation'] = run.get('runtime_validation')
        require(run['status'] == 'completed' and run.get('runtime_validation', {}).get('passed'),
                'I-B failed; inspect cycle_001 run_manifest/events, no fallback or automatic new run')
        require(file_hash(Path(source['path'])) == source['sha256'], 'Source initialization manifest changed')
        manifest['status'] = 'Phase I-B Engineering Validated'
        manifest['stage'] = 'finished'
    except Exception as exc:
        manifest['error'] = sanitized_error(exc)
        write_json(directory / 'failure.json', {'stage': manifest['stage'], **manifest['error']})
    finally:
        manifest['finished_at'] = utc_now()
        manifest['cost'] = ledger.snapshot() if ledger else {'calls': 0, 'reason': 'preflight_not_completed'}
        manifest['external_api_attempts'] = manifest['cost']['calls']
        manifest['artifacts'] = {p.relative_to(directory).as_posix(): {'sha256': file_hash(p), 'bytes': p.stat().st_size}
                                 for p in sorted(directory.rglob('*')) if p.is_file()}
        write_json(directory / 'runtime_validation_manifest.json', manifest)
    return directory, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    directory, manifest = run_smoke(args.config.resolve())
    print(json.dumps({'status': manifest['status'], 'output_directory': str(directory),
                      'external_api_attempts': manifest['external_api_attempts'], 'error': manifest.get('error')},
                     ensure_ascii=False, indent=2))
    return 0 if manifest['status'] == 'Phase I-B Engineering Validated' else 1


if __name__ == '__main__':
    raise SystemExit(main())
