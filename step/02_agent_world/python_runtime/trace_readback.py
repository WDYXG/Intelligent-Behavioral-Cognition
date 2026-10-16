"""Read-only offline behavior-chain reconstruction; never invokes a model/World.

Evidence references use existing event sequence numbers and request/observation
IDs. Missing links are gaps, not inferred actions. No rationale is interpreted.
This is a Phase I-A fixture-validated reader, not Phase I-C runtime validation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from common import digest, require


def reconstruct_trace(rows: list[dict]) -> dict:
    observations, prepared, requests, results, settlements, events = {}, {}, {}, {}, {}, {}
    delivered_events, delivered_results = {}, {}
    chains, gaps, inputs = [], [], []
    for seq, row in enumerate(rows, 1):
        kind, p = row['type'], row['payload']
        if kind == 'world_event':
            eid = p['event_id']
            require(eid not in events, 'Trace duplicate WorldEvent ID')
            rid = p.get('request_id')
            if rid is not None:
                # PLAN substeps point at their parent's request; keep raw ID too.
                parent = rid.rsplit(':step', 1)[0] if ':step' in rid else rid
                if parent not in requests:
                    gaps.append({'event_seq': seq, 'reason': 'missing_source_request', 'request_id': rid})
            events[eid] = (seq, copy.deepcopy(p))
        elif kind == 'observation_delivered':
            oid, actor = p['observation_id'], p['recipient']
            key = (actor, oid)
            require(key not in observations and p['visibility'] == [actor], 'Trace duplicate/foreign Observation')
            ids = [e['event_id'] for e in p['content'].get('events', [])]
            require(len(ids) == len(set(ids)), 'Trace duplicate delivered event')
            for ev in p['content'].get('events', []):
                require(ev['event_id'] in events and events[ev['event_id']][1] == ev
                        and actor in ev['visibility'], 'Trace uncommitted/altered/unauthorized event')
                require(ev['round'] <= p['round'], 'Trace future event delivery')
                delivered_events.setdefault(actor, set()).add(ev['event_id'])
            feedback = p['content'].get('action_result')
            if feedback is not None:
                rid = feedback['request_id']
                require(feedback.get('agent_id') == actor, 'Trace wrong feedback recipient')
                require(rid in results and results[rid][1] == feedback, 'Trace feedback without matching prior ActionResult')
                delivered_results.setdefault(actor, set()).add(rid)
            observations[key] = (seq, copy.deepcopy(p))
        elif kind == 'decision_input':
            context, messages = p['context'], p['messages']
            actor, oid = context['agent_id'], context['Observation']['observation_id']
            require(p['agent_id'] == actor, 'Trace foreign decision input')
            require(digest(context) == p['context_sha256'] and digest(messages) == p['messages_sha256'], 'Trace prepared hash mismatch')
            require(len(messages) == 2 and messages[1]['role'] == 'user'
                    and json.loads(messages[1]['content']) == context, 'Trace messages do not encode prepared context')
            require((actor, oid) in observations and observations[actor, oid][1] == context['Observation'],
                    'Trace decision uses undelivered/changed current Observation')
            seen_e, seen_r = set(), set()
            for record in [context['Observation']['content'], *context.get('episodes', [])]:
                if 'observation_id' in record:
                    require((actor, record['observation_id']) in observations, 'Trace foreign memory Observation')
                    source = observations[actor, record['observation_id']][1]['content']
                else:
                    source = context['Observation']['content']
                for ev in record.get('events', []):
                    eid = ev['event_id']
                    require(eid not in seen_e and eid in delivered_events.get(actor, set())
                            and events[eid][1] == ev and ev in source.get('events', []), 'Trace duplicate/unauthorized/altered context event')
                    seen_e.add(eid)
                feedback = record.get('action_result')
                if feedback is not None:
                    rid = feedback['request_id']
                    require(rid not in seen_r and rid in delivered_results.get(actor, set())
                            and results[rid][1] == feedback and source.get('action_result') == feedback,
                            'Trace duplicate/unauthorized context feedback')
                    seen_r.add(rid)
            entry = {'event_seq': seq, 'agent_id': actor, 'observation_id': oid,
                     'context_sha256': p['context_sha256'], 'messages_sha256': p['messages_sha256'],
                     'event_ids': sorted(seen_e), 'result_ids': sorted(seen_r)}
            inputs.append(entry)
            prepared[actor, oid] = entry
        elif kind == 'action_request':
            rid, actor, oid = p['request_id'], p['actor_id'], p['basis_observation_id']
            require(rid not in requests, 'Trace duplicate request ID')
            if (actor, oid) not in observations:
                gaps.append({'request_id': rid, 'reason': 'missing_basis_observation'})
            view = prepared.get((actor, oid))
            if view is None:
                gaps.append({'request_id': rid, 'reason': 'missing_prepared_decision'})
            requests[rid] = (seq, copy.deepcopy(p))
            chains.append({'request_id': rid, 'agent_id': actor, 'observation_id': oid,
                           'request_seq': seq, 'prepared_decision': copy.deepcopy(view),
                           'execution_status': 'unexecuted', 'executed_action': None,
                           'gm_transaction_seq': None, 'result_seq': None, 'world_event_ids': [],
                           'feedback_observation_ids': [], 'next_decision_observation_id': None,
                           'next_decision_includes_feedback': None})
        elif kind == 'world_transaction':
            for res in p['results']:
                rid = res['request_id']
                require(rid in requests and rid not in settlements, 'Trace orphan/duplicate GM settlement')
                require(res['agent_id'] == requests[rid][1]['actor_id'], 'Trace foreign GM settlement')
                settlements[rid] = (seq, copy.deepcopy(res))
        elif kind == 'action_result':
            rid = p['request_id']
            require(rid in requests and rid not in results, 'Trace orphan/duplicate ActionResult')
            require(p['agent_id'] == requests[rid][1]['actor_id'], 'Trace foreign ActionResult')
            require(type(p['accepted']) is bool, 'Trace invalid acceptance flag')
            require((p['accepted'] and isinstance(p.get('executed_action'), str))
                    or (not p['accepted'] and p.get('executed_action') is None), 'Trace inconsistent executed/rejected result')
            if rid in settlements:
                require(settlements[rid][1] == p, 'Trace ActionResult differs from GM settlement')
            elif p.get('rejection_reason') != 'phase_aborted_due_to_decision_failure':
                gaps.append({'request_id': rid, 'reason': 'missing_gm_settlement'})
            results[rid] = (seq, copy.deepcopy(p))
    for chain in chains:
        rid, actor = chain['request_id'], chain['agent_id']
        if rid in results:
            seq, res = results[rid]
            chain.update(execution_status=('executed' if res['accepted'] else
                         'unexecuted' if res.get('rejection_reason') == 'phase_aborted_due_to_decision_failure' else 'rejected'),
                         executed_action=res.get('executed_action'), result_seq=seq,
                         rejection_reason=res.get('rejection_reason'),
                         gm_transaction_seq=settlements[rid][0] if rid in settlements else None)
        chain['world_event_ids'] = [eid for eid, (_, ev) in events.items()
                                   if ev.get('request_id') == rid or str(ev.get('request_id', '')).startswith(rid + ':step')]
        feedbacks = [(seq, oid) for (recipient, oid), (seq, obs) in observations.items()
                     if recipient == actor and (obs['content'].get('action_result') or {}).get('request_id') == rid]
        chain['feedback_observation_ids'] = [oid for _, oid in feedbacks]
        if feedbacks:
            later = [view for view in inputs if view['agent_id'] == actor and view['event_seq'] > feedbacks[0][0]]
            if later:
                chain['next_decision_observation_id'] = later[0]['observation_id']
                chain['next_decision_includes_feedback'] = rid in later[0]['result_ids']
                if not chain['next_decision_includes_feedback']:
                    gaps.append({'request_id': rid, 'reason': 'delivered_feedback_missing_from_next_input'})
            chain['continuation_status'] = 'next_input_recorded' if later else 'no_next_input_recorded'
        else:
            chain['continuation_status'] = 'no_feedback_recorded'
    return {'scope': 'offline_trace_links_not_runtime_or_behavior_validation', 'chains': chains, 'gaps': gaps,
            'prepared_inputs': inputs, 'meaning': 'prepared input is not evidence of API dispatch or model attention'}


def read_trace(path: Path) -> dict:
    """Verify the existing hash-chain format, then reconstruct without writing."""
    rows, previous = [], None
    with path.open(encoding='utf-8') as handle:
        for seq, line in enumerate(handle, 1):
            row = json.loads(line)
            hashed = row.pop('hash')
            require(row['seq'] == seq and row['previous_hash'] == previous and digest(row) == hashed, 'Broken event chain')
            previous = hashed
            rows.append(row)
    report = reconstruct_trace(rows)
    report.update(event_count=len(rows), final_event_hash=previous)
    return report
