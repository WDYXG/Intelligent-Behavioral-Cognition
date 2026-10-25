"""Local audit of one published task. Scripted outputs only; no model API."""
from pathlib import Path
import hashlib
import json
import math
import re
import socket
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, '/private/tmp/behaviorbench-offline-deps')
sys.path.insert(0, str(ROOT / 'official/src'))

def no_network(*args, **kwargs):
    raise RuntimeError('Network is disabled during offline verification')

socket.create_connection = no_network
socket.socket.connect = no_network

import numpy as np
import scipy
import pandas as pd
import yaml
from behaviorbench.eval.tasks.game_behavior import GameBehaviorTask
from behaviorbench.eval.base import load_task_config

data_file = ROOT / 'dictator_test.jsonl'
rows = [json.loads(line) for line in data_file.read_text().splitlines() if line.strip()]
assert rows and all(set(row) == {'system', 'user', 'assistant'} for row in rows)
unique_prompts = {(row['system'], row['user']) for row in rows}
assert len(unique_prompts) == 1
labels = []
for row in rows:
    match = re.fullmatch(r'\[\$?(-?\d+(?:\.\d+)?)\]', row['assistant'])
    assert match is not None, 'Unexpected human label format'
    value = float(match.group(1))
    assert math.isfinite(value) and 0 <= value <= 100
    labels.append(value)

config = load_task_config()['game_behavior_dictator']

def make_task():
    return GameBehaviorTask(data_path=str(data_file), name='game_behavior_dictator',
        metrics=config['metrics'], clip_range=config['clip_range'],
        normalize_range=config['normalize_range'])

def run_fixture(outputs):
    iterator = iter(outputs)
    def scripted(prompt, **kwargs):
        assert set(prompt) == {'system', 'user'}, 'Human label exposed to model input'
        assert (prompt['system'], prompt['user']) in unique_prompts
        return next(iterator)
    result = make_task().run_evaluation(scripted, num_samples=len(outputs))
    return {'metrics':result.metrics, 'metadata':result.metadata,
        'parsed_outputs':[p['parsed_prediction'] for p in result.predictions]}

fixtures = {
    'human_labels_as_fixture_not_model_result': [row['assistant'] for row in rows],
    'constant_fifty': ['[50]'] * len(rows),
    'constant_zero': ['[0]'] * len(rows),
    'invalid_and_out_of_range': ['[150]', '[-10]', 'no decision', '[50]'],
    'all_invalid': ['no decision'] * 4,
}
results = {name:run_fixture(values) for name,values in fixtures.items()}
distance_key = 'Wasserstein_with_ks_distance'
assert math.isclose(results['human_labels_as_fixture_not_model_result']['metrics'][distance_key], 0, abs_tol=1e-10)
assert math.isclose(results['constant_zero']['metrics'][distance_key], float(np.mean(labels)), abs_tol=1e-10)
assert math.isclose(results['constant_fifty']['metrics'][distance_key], float(np.mean(np.abs(np.array(labels)-50))), abs_tol=1e-10)
assert results['invalid_and_out_of_range']['parsed_outputs'] == [100.0, 0.0, None, 50.0]
assert results['invalid_and_out_of_range']['metadata']['num_failed_parses'] == 1
assert results['all_invalid']['metrics'] == {}

parser_probe = {s:make_task()._parse_output(s) for s in [
    'The budget is 100. I cannot decide.', '[20] then [50]', '[$25]', 'no decision']}

summary = {
    'date':'2026-10-16', 'scope':'offline data and official task evaluation only',
    'research_model_api_calls':0, 'network_disabled_during_verification':True,
    'code_commit':'ddd646ed2bbecdb79094eb56763ee7664d2f8e1a',
    'data_url':'https://huggingface.co/datasets/befm/BehaviorBench/resolve/main/moblab/game_behavior/dictator_test.jsonl',
    'data_sha256':hashlib.sha256(data_file.read_bytes()).hexdigest(),
    'data_revision_note':'Downloaded main; content pinned locally by SHA-256, HF revision not resolved',
    'rows':len(rows), 'unique_prompts':len(unique_prompts), 'fields':list(rows[0]),
    'participant_ids_available':False, 'unique_participants_independently_verified':False,
    'human_summary':{'min':min(labels), 'max':max(labels), 'mean':float(np.mean(labels)),
        'median':float(np.median(labels)), 'std_population':float(np.std(labels)),
        'zero_count':labels.count(0), 'fifty_count':labels.count(50),
        'distinct_values':len(set(labels)), 'counts':dict(sorted(Counter(labels).items()))},
    'task_config':config, 'fixture_results':results, 'parser_probe':parser_probe,
    'versions':{'python':sys.version, 'numpy':np.__version__, 'scipy':scipy.__version__,
        'pandas':pd.__version__, 'pyyaml':yaml.__version__},
    'official_files_sha256':{str(p.relative_to(ROOT/'official')):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT/'official').rglob('*') if p.is_file() and '__pycache__' not in str(p)},
    'checks_passed':True,
}
(ROOT/'offline_verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:summary[k] for k in ['rows','unique_prompts','human_summary','parser_probe','checks_passed']},ensure_ascii=False))
print(json.dumps({k:{'metrics':v['metrics'],'metadata':v['metadata']} for k,v in results.items()},ensure_ascii=False))
