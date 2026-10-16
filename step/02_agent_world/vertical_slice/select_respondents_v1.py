"""Read-only selection computation. Emit an artifact bundle; never write source/output files.

The caller saves the returned artifacts using apply_patch. No AgentState, routing
activation, model call or ExperimentRolePlan is produced here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix, vstack

BASE = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN_ID = 'WVS_full_2026-09-17T09-33-15-228Z'
RUN = BASE / '00_persona/derived/WVS7_CHN_2018' / RUN_ID
VERSION = 'selection_v1.0.0'
SLOTS = [f'S{i:02}' for i in range(1, 13)]
TAGS = ['valid_zero_children', 'valid_unsure_category', 'missing_no_answer',
        'missing_dont_know', 'missing_not_applicable', 'age_year_discrepancy',
        'P_rich', 'M_rich', 'X_rich']
AGE_FLAG = 'birth_year_plus_age_differs_from_2018_not_necessarily_error'
PARTNER_FLAG = 'partner_employed_occupation_zero'
PURPOSES = ['高可用度参照', '整体缺失较多', 'P 候选稀疏', 'M0 候选稀疏', 'X0 候选稀疏',
            '合法负编码', 'top-code', '当前职业条件分支', '过去职业条件分支', '条件未知分支',
            '跨题一致性标记', '时间语义边界']


def check(condition, message):
    if not condition:
        raise ValueError(message)


def progress(message):
    print('PROGRESS ' + message, flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def artifact(path):
    return {'path': path.as_posix(), 'sha256': digest(path)}


def metric(fields, frame):
    missing = Counter()
    valid = 0
    for name in frame:
        f = fields[name]
        if f['status'] == 'source_category':
            valid += 1
        else:
            check(f['status'] == 'source_missing' and f['missing_reason'], f'Unknown status: {name}')
            missing[f['missing_reason']] += 1
    return {'denominator': len(frame), 'valid_count': valid,
            'missing_count': sum(missing.values()), 'missing_by_reason': dict(sorted(missing.items()))}


def load_pool():
    routing_path = BASE / '00_persona/rules/WVS_Evidence_Routing_v1.json'
    audit_path = BASE / '00_persona/working/WVS7_full_audit_2026-09-14/final_audit_v0.3/final_field_status.json'
    routing = read_json(routing_path)
    audit = {x['field']: x for x in read_json(audit_path)}
    manifest = read_json(RUN / 'manifest.json')
    check(digest(audit_path) == manifest['audit_sha256'] == routing['source_audit_sha256'], 'Audit hash mismatch')
    for name in ['standardized_records.jsonl', 'consistency_flags.jsonl']:
        expected = next(x['sha256'] for x in manifest['outputs'] if x['file'] == name)
        check(digest(RUN / name) == expected, f'Input hash mismatch: {name}')
    check(all(x['activation_status'] == 'not_enabled' for x in routing['fields']), 'Routing changed')
    target_fields = {x['field'] for x in routing['fields'] if x['proposed_route'] == 'VALIDATION_TARGET'}
    frames = {
        'selection_frame': [x['field'] for x in routing['fields'] if x['source_disposition'] != 'source_not_measured'
                            and x['proposed_route'] not in ['EXCLUDED', 'VALIDATION_TARGET']],
        'P_candidate': [x['field'] for x in routing['fields'] if x['proposed_route'] == 'P_INIT'],
        'M0_candidate': [x['field'] for x in routing['fields'] if x['proposed_route'] == 'M0_CANDIDATE'],
        'X0_candidate': [x['field'] for x in routing['fields'] if x['proposed_route'] == 'X0_INIT'],
    }
    check([len(x) for x in frames.values()] == [215, 13, 137, 11], 'Frozen denominators changed')
    check(not (set(frames['selection_frame']) & target_fields), 'Targets entered selection')
    flags = {}
    for line in (RUN / 'consistency_flags.jsonl').open(encoding='utf-8'):
        r = json.loads(line)
        check(r['source_record_id'] not in flags and r['automatic_correction'] is False, 'Invalid flag record')
        flags[r['source_record_id']] = r['flags']
    codes = {k: {str(x['code']): x for x in v['code_audit']} for k, v in audit.items() if k != 'Q263'}
    required = set(audit) - {'Q263'}
    pool = []
    for line_no, line in enumerate((RUN / 'standardized_records.jsonl').open(encoding='utf-8'), 1):
        r = json.loads(line)
        rid = r['source_record_id']
        check(isinstance(rid, str) and rid in flags, 'Missing ID/flags')
        check(r['source_dataset'] == 'WVS7_CHN_2018' and r['source_version'] == '6-0-0 (2024-04-30)', 'Wrong source')
        check(r['persona_created'] is False and r['excluded_fields'] == ['Q263'], 'Unexpected source state')
        # Field-name integrity is checked for all 290; target answer payloads are
        # neither inspected nor retained in selection features/metrics.
        check(len(r['fields']) == 290 and {f['field'] for f in r['fields']} == required, 'Field structure mismatch')
        fields = {f['field']: f for f in r['fields'] if f['field'] not in target_fields}
        for name, f in fields.items():
            c = codes[name].get(f['raw'])
            check(c is not None, f'Unknown code: {rid}/{name}')
            expected_status = 'source_category' if c['missing_reason'] is None else 'source_missing'
            check(f['status'] == expected_status and f['missing_reason'] == c['missing_reason'], 'Code/missingness mismatch')
            check(f['value'] == (c['code'] if expected_status == 'source_category' else None), 'Value mismatch')
            check(f['evidence_id'] == f'ev:WVS7_CHN_2018:{rid}:{name}:audit03', 'Evidence ID mismatch')
            if audit[name]['current_disposition'] == 'source_not_measured':
                check(expected_status == 'source_missing', 'Unmeasured field has value')
        pool.append({'id': rid, 'fields': fields, 'flags': flags[rid], 'line': line_no,
                     'profile': {**{k: metric(fields, v) for k, v in frames.items()}, 'consistency_flags': flags[rid]}})
    check(len(pool) == 3036 and len({r['id'] for r in pool}) == 3036 and set(flags) == {r['id'] for r in pool}, 'Pool integrity failure')
    pool.sort(key=lambda r: r['id'].encode('utf-8'))
    thresholds = {}
    for key, frame in frames.items():
        ordered = sorted(r['profile'][key]['valid_count'] for r in pool)
        thresholds[key] = {'denominator': len(frame), 'q20': ordered[math.ceil(.2 * len(pool)) - 1],
                           'q80': ordered[math.ceil(.8 * len(pool)) - 1]}
    for r in pool:
        f = r['fields']
        v = lambda n, values: f[n]['status'] == 'source_category' and f[n]['value'] in values
        p = r['profile']
        r['eligible'] = [
            p['selection_frame']['valid_count'] >= thresholds['selection_frame']['q80'],
            p['selection_frame']['valid_count'] <= thresholds['selection_frame']['q20'] and p['selection_frame']['missing_count'] > 0,
            *[p[k]['valid_count'] <= thresholds[k]['q20'] and p[k]['missing_count'] > 0
              for k in ['P_candidate', 'M0_candidate', 'X0_candidate']],
            v('Q284', [-5]), v('Q274', [7]),
            v('Q279', [1, 2, 3]) and v('Q281', range(1, 11)),
            v('Q279', [4, 5, 6, 7]) and v('Q281', range(1, 11)),
            f['Q279']['status'] == 'source_missing' and v('Q281', range(1, 11)),
            PARTNER_FLAG in r['flags'], f['Q286']['status'] == 'source_category']
        reasons = p['selection_frame']['missing_by_reason']
        r['tags'] = [tag for tag, hit in zip(TAGS, [
            v('Q274', [0]), v('Q33', [3]) or v('Q119', [0]),
            reasons.get('no_answer', 0) > 0, reasons.get('dont_know', 0) > 0,
            reasons.get('not_applicable', 0) > 0, AGE_FLAG in r['flags'],
            *[p[k]['valid_count'] >= thresholds[k]['q80'] for k in ['P_candidate', 'M0_candidate', 'X0_candidate']]]) if hit]
        r['min_fraction_scaled'] = min(p['P_candidate']['valid_count'] * 1507,
                                       p['M0_candidate']['valid_count'] * 143,
                                       p['X0_candidate']['valid_count'] * 1781)
    return pool, frames, thresholds, manifest, routing_path, audit_path, target_fields


def optimize(pool, label):
    edges = [(s, i) for s in range(12) for i, r in enumerate(pool) if r['eligible'][s]]
    counts = [sum(r['eligible'][s] for r in pool) for s in range(12)]
    check(all(counts), 'EMPTY_SLOT: ' + json.dumps(dict(zip(SLOTS, counts))))
    size, n = len(edges) + 9, len(pool)
    rr, cc, vv = [], [], []
    for j, (s, i) in enumerate(edges):
        rr.extend([s, 12 + i]); cc.extend([j, j]); vv.extend([1., 1.])
        for tag in pool[i]['tags']:
            rr.append(12 + n + TAGS.index(tag)); cc.append(j); vv.append(-1.)
    for t in range(9):
        rr.append(12 + n + t); cc.append(len(edges) + t); vv.append(1.)
    matrix = coo_matrix((vv, (rr, cc)), shape=(12 + n + 9, size)).tocsc()
    lower = np.concatenate([np.ones(12), np.zeros(n), np.full(9, -np.inf)])
    upper = np.concatenate([np.ones(12), np.ones(n), np.zeros(9)])
    stages = []
    c = np.zeros(size); c[len(edges):] = -1
    stages.append(('max_auxiliary_tags', c))
    for name, slot, key, sign in [
        ('max_S01_min_fraction_scaled_19591', 0, 'min_fraction_scaled', -1),
        ('max_S01_valid_select', 0, 'selection_frame', -1),
        ('min_S02_valid_select', 1, 'selection_frame', 1),
        ('min_S03_valid_P', 2, 'P_candidate', 1),
        ('min_S04_valid_M', 3, 'M0_candidate', 1),
        ('min_S05_valid_X', 4, 'X0_candidate', 1)]:
        c = np.zeros(size)
        for j, (s, i) in enumerate(edges):
            if s == slot:
                c[j] = sign * (pool[i][key] if key == 'min_fraction_scaled' else pool[i]['profile'][key]['valid_count'])
        stages.append((name, c))
    rank = {r: i for i, r in enumerate(sorted([x['id'] for x in pool], key=lambda s: s.encode('utf-8')))}
    for slot in range(12):
        c = np.zeros(size)
        for j, (s, i) in enumerate(edges):
            if s == slot: c[j] = rank[pool[i]['id']]
        stages.append((f'min_{SLOTS[slot]}_id_rank', c))
    records = []
    for name, c in stages:
        result = milp(c, integrality=np.ones(size), bounds=Bounds(0, 1),
                      constraints=LinearConstraint(matrix, lower, upper),
                      options={'time_limit': 45., 'mip_rel_gap': 0.})
        check(result.status == 0, f'SELECTION_INCOMPLETE {label}/{name}: {result.message}')
        rounded = np.rint(result.x)
        check(np.max(np.abs(rounded - result.x)) < 1e-5, 'Noninteger solution')
        value = int(round(float(c @ rounded)))
        check(abs(float(result.fun) - value) < 1e-5, 'Objective rounding mismatch')
        records.append({'objective_id': name, 'value': -value if name.startswith('max_') else value})
        matrix = vstack([matrix, csr_matrix(c.reshape(1, -1))], format='csc')
        lower = np.append(lower, value); upper = np.append(upper, value)
        progress(f'{label}: {name} = {records[-1]["value"]}')
    selected = {}
    for j, (s, i) in enumerate(edges):
        if rounded[j] == 1: selected[s] = pool[i]
    check(len(selected) == len({r['id'] for r in selected.values()}) == 12, 'Matching not unique')
    return [selected[s] for s in range(12)], records, counts


def verify_evidence(selected, manifest, targets):
    wanted = {f['evidence_id']: (r, name, f) for r in selected for name, f in r['fields'].items()}
    found, h = {}, hashlib.sha256()
    ids = {r['id'].encode('ascii') for r in selected}
    total = 0
    with (RUN / 'evidence.jsonl').open('rb') as stream:
        for line_no, line in enumerate(stream, 1):
            h.update(line); total += 1
            # Only parse records belonging to selected IDs. Target payloads,
            # although co-resident in the hashed file, never enter witnesses.
            prefix = line.split(b'"evidence_id":"', 1)[1].split(b'"', 1)[0]
            parts = prefix.split(b':')
            if parts[2] not in ids or parts[3].decode('ascii') in targets: continue
            e = json.loads(line)
            eid = e['evidence_id']
            check(eid in wanted and eid not in found, 'Unexpected/duplicate selected evidence')
            r, name, f = wanted[eid]
            check(e['provenance']['source_record_id'] == r['id'] and e['extensions']['source_field'] == name, 'Evidence identity mismatch')
            check(e['provenance']['source_dataset'] == 'WVS7_CHN_2018' and e['provenance']['source_version'] == '6-0-0 (2024-04-30)', 'Evidence source mismatch')
            check(e['provenance']['source_hash'] == manifest['source_sha256'], 'Evidence raw hash mismatch')
            check(e['extensions']['audit_sha256'] == manifest['audit_sha256'], 'Evidence audit hash mismatch')
            check(e['observation']['observed_value'] == f['raw'] and e['normalization']['normalized_value'] == f['value'], 'Evidence value mismatch')
            check(e['extensions']['missing_reason'] == f['missing_reason'], 'Evidence missingness mismatch')
            check(e['projection']['target'] == 'survey_snapshot_only' and e['projection']['persona_path'] is None, 'Unexpected projection')
            t = e['observation']['time']
            check(t == {'observation_time': '2018', 'time_precision': 'year', 'reference_period': '2017' if name == 'Q286' else None}, 'Time mismatch')
            found[eid] = (e, line_no)
    check(total == 880440 and len(found) == len(wanted) == 12 * 259, 'Evidence counts mismatch')
    expected = next(x['sha256'] for x in manifest['outputs'] if x['file'] == 'evidence.jsonl')
    check(h.hexdigest() == expected, 'Full evidence hash mismatch')
    return found, h.hexdigest(), total


def create_outputs(pool, frames, thresholds, selected, objectives, counts, found, inputs,
                   evidence_hash, total_evidence, targets):
    output_dir = HERE / 'selection_v1'
    def ref_for(r, name):
        check(name in frames['selection_frame'] and name not in targets, 'Forbidden witness field')
        f = r['fields'][name]; e, line_no = found[f['evidence_id']]
        t = e['observation']['time']
        return {'evidence_id': e['evidence_id'], 'source_field': name,
                'snapshot_locator': f'{(RUN / "standardized_records.jsonl").as_posix()}#line={r["line"]};field={name};evidence_line={line_no}',
                **t, 'time_limitations': [e['extensions']['reference_period_note'], '调查年份不是模拟时刻，尚未进行时间对齐。'],
                'raw_value': f['raw'], 'normalized_value': f['value'], 'source_status': f['status'], 'missing_reason': f['missing_reason']}
    def witness(r, criterion, names=(), kind='field_condition', metric_key=None, flag=None):
        return {'criterion_id': criterion, 'kind': kind, 'evidence_refs': [ref_for(r, n) for n in names],
                'aggregate_profile_ref': f'profile.{metric_key}' if metric_key else None,
                'consistency_flag': flag, 'verification_note': '由固定快照和 Evidence 回读核实；仅用于选样审查，不授予状态投影权限。'}
    primary_fields = {5:['Q284'], 6:['Q274'], 7:['Q279','Q281'], 8:['Q279','Q281'],
                      9:['Q279','Q281'], 10:['Q280','Q282'], 11:['Q286']}
    metrics = ['selection_frame', 'selection_frame', 'P_candidate', 'M0_candidate', 'X0_candidate']
    samples = []
    for slot, r in enumerate(selected):
        if slot < 5:
            names = [] if slot == 0 else [next(n for n in frames[metrics[slot]] if r['fields'][n]['status'] == 'source_missing')]
            witnesses = [witness(r, SLOTS[slot], names, 'aggregate_metric', metrics[slot])]
        else:
            witnesses = [witness(r, SLOTS[slot], primary_fields[slot], 'consistency_flag' if slot == 10 else 'field_condition', flag=PARTNER_FLAG if slot == 10 else None)]
        for tag in r['tags']:
            if tag.endswith('_rich'):
                key = {'P_rich':'P_candidate','M_rich':'M0_candidate','X_rich':'X0_candidate'}[tag]
                witnesses.append(witness(r, tag, kind='aggregate_metric', metric_key=key))
            elif tag.startswith('missing_'):
                reason = tag.removeprefix('missing_')
                name = next(n for n in frames['selection_frame'] if r['fields'][n]['missing_reason'] == reason)
                witnesses.append(witness(r, tag, [name]))
            elif tag == 'valid_zero_children': witnesses.append(witness(r, tag, ['Q274']))
            elif tag == 'valid_unsure_category':
                name = 'Q33' if r['fields']['Q33']['value'] == 3 else 'Q119'
                witnesses.append(witness(r, tag, [name]))
            else: witnesses.append(witness(r, tag, ['Q261','Q262'], 'consistency_flag', flag=AGE_FLAG))
        samples.append({'slot_id': SLOTS[slot], 'sample_alias': f'VS{slot+1:02}', 'source_record_id': r['id'],
                        'primary_test_purpose': PURPOSES[slot], 'secondary_tags': r['tags'], 'profile': r['profile'],
                        'selection_evidence': witnesses,
                        'expected_checks': [PURPOSES[slot] + '：核验来源解释、缺失/时间/条件边界。', '后续仅用获准 Evidence 初始化；P/M 运行期冻结。'],
                        'limitations': ['这是覆盖式架构测试样本，不代表人口或行为。', '候选有值数不是获准输入数；实际输入 unknown 覆盖待 ExperimentRolePlan 后复查。',
                                        '一致性标记不是确定的回答错误；不补值、不自动修正。']})
    coverage = {'slots': [{'slot_id': s, 'candidate_count': counts[i], 'selected_source_record_id': selected[i]['id']} for i,s in enumerate(SLOTS)],
                'auxiliary_tags': [{'tag': tag, 'covered': any(tag in r['tags'] for r in selected),
                                    'source_record_ids': [r['id'] for r in selected if tag in r['tags']],
                                    'uncovered_reason': None if any(tag in r['tags'] for r in selected) else '固定规则下未覆盖，未自动放宽。'} for tag in TAGS],
                'global_guards': {'source_unmeasured_remain_unknown': True,'projection_holds_preserved': True,
                                  'Q290_specific_ethnicity_blocked': True,'Q263_not_used': True},
                'gaps': [tag for tag in TAGS if not any(tag in r['tags'] for r in selected)],
                'runtime_input_coverage_status': 'pending_experiment_role_plan'}
    semantic = {'status':'passed','scope':'selection_and_source_consistency_only',
                'checks': {'unique_12_source_ids':len({r['id'] for r in selected}) == 12,
                           'all_primary_slots_satisfied':all(r['eligible'][s] for s,r in enumerate(selected)),
                           'all_objective_stages_proven_optimal':True, 'reversed_pool_replay_same_assignment_and_objectives':True,
                           '31_target_candidates_excluded_from_selection_metrics_and_witnesses':True,
                           'full_evidence_sha256_matches':True, 'snapshot_and_flag_sha256_match':True,
                           'selected_non_target_evidence_matches_snapshot':True, 'no_route_activation':True,
                           'no_agent_state_or_experiment_role_plan_created':True},
                'candidate_count':len(pool),'primary_slots_covered':12,'auxiliary_tags_covered':sum(x['covered'] for x in coverage['auxiliary_tags']),
                'evidence_file_lines_hashed':total_evidence,'selected_non_target_evidence_checked':len(found),
                'evidence_sha256':evidence_hash,'schema_validation':'performed_separately_with_Ajv_after_artifact_save',
                'limits':['文件解析/哈希不等于目标信息用于选样；31 项目标候选载荷未用于指标、排序或输出证据。',
                          '实际路由及运行时 unknown 覆盖尚未验证。','没有行为效度结论。']}
    semantic_text = encode(semantic)
    manifest = {'schema_version':'selection_manifest_v1','selection_id':'WVS_CHN2018_VS12_selection_v1',
                'status':'selected_pending_confirmation','purpose':'architecture_test_coverage_only',
                'created_at':datetime.now(timezone.utc).isoformat(),'requested_n':12,
                'source':{'dataset':'WVS7_CHN_2018','version':'6-0-0 (2024-04-30)','source_run':RUN_ID,'candidate_count':len(pool),'inputs':inputs},
                'protocol':{'selection_plan':artifact(HERE/'Selection_Plan_v1.md'),'plan_version':'1.0.0-draft',
                            'selector_version':VERSION,'selector_code_sha256':digest(Path(__file__)),
                            'quantile_method':'nearest_rank_ceil_pN','thresholds':thresholds,'auxiliary_tag_vocabulary':TAGS,
                            'algorithm':'deterministic_global_slot_matching_lexicographic_v1',
                            'optimization':{'solver_name':'scipy.optimize.milp (HiGHS)','solver_version':scipy.__version__,
                                            'status':'optimal_all_stages','objective_values':objectives,
                                            'tie_break':'S01_to_S12_source_id_vector_utf8_lexicographic_ascending','random_selection':False}},
                'boundary':{'architecture_test_only':True,'population_representative':False,'routing_activation_granted':False,
                            'future_holdout_independence_claimed':False,'validation_target_payload_used_for_selection':False,
                            'learning_enabled':False,'P_frozen_at_runtime':True,'M_frozen_at_runtime':True,'experiment_role_plan_id':None,
                            'access_scope':'restricted_selection_audit_not_decision_context'},
                'coverage':coverage,'samples':samples,
                'integrity':{'source_ids_unique':True,'slots_unique_and_complete':True,'cross_source_joined':False,
                             'automatic_correction':False,'imputation':False,'source_hashes_verified':True,
                             'selected_evidence_verified':True,'repeated_selection_same_members_and_slots':True,
                             'semantic_validation_report':{'path':(output_dir/'selection_validation.json').as_posix(),'sha256':text_digest(semantic_text)}}}
    lines = ['# 12 人覆盖式选择报告', '', '状态：第 1 步已执行，所选名单待用户审阅；未进入第 2 步。', '',
             '[Selection manifest](selection_manifest.json) · [语义检查](selection_validation.json)', '',
             '## 结论', '', f'- 通过结构检查的候选：{len(pool)} 人；选择 12 个不同来源 ID。',
             '- 12/12 主槽位满足；附加覆盖 '+str(sum(x['covered'] for x in coverage['auxiliary_tags']))+'/9。',
             '- 不放宽选择规则，不修正回答，不插补，不启用路由；没有生成 AgentState 或 ExperimentRolePlan。',
             '- 19 个逐级优化目标全部达到求解器最优；逆序候选池重跑后，人员、槽位与目标值完全一致。', '',
             '## 槽位与实际覆盖', '', '| 槽位 | 来源内 ID | 测试目的 | 候选人数 | 有值数 select/P/M/X | 核验触发条件 |',
             '| --- | --- | --- | --- | --- | --- |']
    for i,r in enumerate(selected):
        vals='/'.join(str(r['profile'][k]['valid_count']) for k in frames)
        if i<5: trigger=f'{metrics[i]}={r["profile"][metrics[i]]["valid_count"]}'
        else: trigger='；'.join(f'{n}={r["fields"][n]["raw"]} ({r["fields"][n]["status"]})' for n in primary_fields[i])
        if i==10: trigger+='；已有 partner_employed_occupation_zero'
        if i==11: trigger+='；观测 2018，参考时期 2017'
        lines.append(f'| {SLOTS[i]} | {r["id"]} | {PURPOSES[i]} | {counts[i]} | {vals} | {trigger} |')
    lines += ['', '有值数分母依次为 215 / 13 / 137 / 11。这些是选样候选集合，不是正式输入集合。', '',
              '## 分位数阈值', '', '| 集合 | 分母 | q20 | q80 |', '| --- | --- | --- | --- |']
    for key,t in thresholds.items(): lines.append(f'| {key} | {t["denominator"]} | {t["q20"]} | {t["q80"]} |')
    lines += ['', '分位数采用 nearest-rank；稀疏槽位还要求至少一个真实缺失。并列不随机拆分。', '',
              '## 附加覆盖', '', '| 标签 | 命中人数 | 来源内 ID |', '| --- | --- | --- |']
    for x in coverage['auxiliary_tags']: lines.append(f'| {x["tag"]} | {len(x["source_record_ids"])} | {", ".join(x["source_record_ids"])} |')
    lines += ['', '## Selection gap 与尚未测试的范围', '',
              ('当前定义的主槽位及 9 个附加标签无 selection gap。' if not coverage['gaps'] else '未覆盖标签：'+', '.join(coverage['gaps'])),
              '这不表示所有架构边界已经通过运行验证：', '',
              '- ExperimentRolePlan 尚未定义；实际启用字段上的 unknown/特殊值/条件分支覆盖尚待复查。',
              '- M/P/X 的“稀疏”是来源候选有值数相对较少，不代表已初始化出空状态。',
              '- Q284、职业上下文、配偶标记与回顾收支等只用于来源审查；不能因入选而改变路由。',
              '- 未来若把选样依据变为目标，必须披露选择依赖；本批是开发/架构测试样本，不是独立 holdout。',
              '- 数据不提供未知/异常编码的真实实例时，不能编造受访者；此处也未做人工故障注入。', '',
              '## 溯源与核验', '',
              f'- 对完整 Evidence 文件 {total_evidence:,} 行计算 SHA256，与正式清单一致。',
              f'- 对选中 12 人的 {len(found):,} 条非目标候选 Evidence 回读核对；其中包含保留的未测量证据。',
              '- 每人的来源 ID、字段、原码/标准码、缺失、观测/参考时期与快照一致；文件和规则哈希记录在 manifest。',
              '- 31 个 VALIDATION_TARGET 候选的载荷不进入选样指标、优化、触发证据或输出清单；整体文件哈希不等于使用目标选样。',
              '- 44 项未测量保持缺失；14 项 hold、Q290 粒度限制和 Q263 排除保持有效。',
              '- Schema 校验见同目录 schema_validation.json；语义校验和确定性重跑不能被 Schema 替代。', '',
              '## 审阅建议', '',
              '先检查 12 个主槽位、候选人数和触发证据，再检查附加标签是否满足需要。确认这份真实名单后，才单独进入第 2 步。', '',
              '本报告含来源内 ID 和缺失/差异信息，限本地受限研究审查，不整体传入 Agent 或公开回放。', '']
    return {'selection_manifest.json':encode(manifest),'coverage_report.md':'\n'.join(lines),'selection_validation.json':semantic_text}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile-only', action='store_true')
    args = parser.parse_args()
    pool, frames, thresholds, source_manifest, routing_path, audit_path, targets = load_pool()
    progress('pool verified: '+json.dumps({'candidate_count':len(pool),'thresholds':thresholds,'slot_counts':dict(zip(SLOTS,[sum(r['eligible'][s] for r in pool) for s in range(12)]))}))
    if args.profile_only: return
    selected, objectives, counts = optimize(pool, 'forward')
    again, again_objectives, _ = optimize(list(reversed(pool)), 'reverse')
    check([r['id'] for r in selected] == [r['id'] for r in again] and objectives == again_objectives, 'Non-deterministic selection')
    progress('exact matching and reversed-pool replay passed; reading selected evidence and hashing full file')
    found, evidence_hash, total = verify_evidence(selected, source_manifest, targets)
    paths = {'standardized_records':RUN/'standardized_records.jsonl','consistency_flags':RUN/'consistency_flags.jsonl',
             'run_manifest':RUN/'manifest.json','source_audit':audit_path,'routing_catalog':routing_path,
             'architecture_spec':BASE/'02_agent_world/Architecture_Spec_v1.md','routing_spec':BASE/'00_persona/rules/Evidence_Routing_v1.md'}
    inputs = {k:artifact(p) for k,p in paths.items()}
    inputs['evidence'] = {'path':(RUN/'evidence.jsonl').as_posix(),'sha256':evidence_hash}
    outputs = create_outputs(pool, frames, thresholds, selected, objectives, counts, found, inputs, evidence_hash, total, targets)
    print('ARTIFACT_BUNDLE='+json.dumps(outputs,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
