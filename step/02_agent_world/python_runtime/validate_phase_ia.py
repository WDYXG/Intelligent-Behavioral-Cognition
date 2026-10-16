"""Dedicated offline acceptance entry point; never calls the experiment Runner.

Optional --report writes a NEW acceptance artifact, never updates Specs/history.
All skips, failures and errors block acceptance. Tests install network guards.
"""
import argparse
import ast
import io
import json
import platform
import sys
import unittest
from pathlib import Path

from common import file_hash, write_json


class GateResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def record(self, test, status, detail=None):
        name = test.id()
        self.records.append({'test': name, 'gate': name.rsplit('.', 1)[-1].split('_')[1],
                             'status': status, 'detail': detail})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'failed', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'error', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'skipped', reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='New JSON file; existing files are never overwritten')
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error('Report already exists; choose a new filename')
    root = Path(__file__).resolve().parent
    static = []
    # Parse, do not import/run the production runner, scene or CLI entry points.
    for path in sorted(root.glob('*.py')):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        static.append(path.name)
    import test_phase_ia
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_phase_ia)
    text_stream = io.StringIO()
    result = unittest.TextTestRunner(stream=text_stream, verbosity=2, resultclass=GateResult).run(suite)
    gates = {gate: [r for r in result.records if r['gate'] == gate] for gate in 'ABCDEFGHI'}
    passed = (result.wasSuccessful() and not result.skipped
              and len(result.records) == result.testsRun
              and all(records and all(r['status'] == 'passed' for r in records) for records in gates.values()))
    specs = root.parents[1] / 'specs'
    report = {'status': 'Phase I-A Engineering Validated' if passed else 'Implemented / Not Validated',
              'scope': 'offline engineering contracts validated on static synthetic fixtures only',
              'python': platform.python_version(), 'executable': sys.executable,
              'suite': 'test_phase_ia.py', 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'gates': {gate: {'passed': bool(records) and all(r['status'] == 'passed' for r in records),
                                'tests': [r['test'] for r in records]} for gate, records in gates.items()},
              'results': result.records, 'static_python_files_parsed': static,
              'source_hashes': {p.name: file_hash(p) for p in sorted(root.glob('*.py'))},
              'spec_hashes_read_only': {name: file_hash(specs / name) for name in
                                      ('Architecture_Spec.md', 'Experiment_Spec.md', 'Data_Contract.md')},
              'network_guard': 'socket/socket.create_connection and urllib open blocked; dispatch uses in-memory transport double',
              'external_api_calls': 0, 'formal_simulation_runs': 0,
              'fixture_origin': 'test-only static synthetic objects; no existing Persona loaded or modified',
              'tokenizer_scope': 'hash/model/path/interface and missing-dependency fail-closed validated using interface double; actual tokenizers/provider equivalence NOT validated',
              'not_validated': ['I-B runtime closed loop', 'I-C reconstruction on a formal runtime trace',
                                'actual provider token counts', 'behavioral effects or internal mechanisms',
                                'all existing scene-specific regression suites'],
              'test_output': text_stream.getvalue()}
    if args.report:
        # Caller chooses a new file in an existing directory, no historical overwrite.
        write_json(args.report, report)
    print(text_stream.getvalue())
    print(json.dumps({k: report[k] for k in ('status', 'tests_run', 'failures', 'errors', 'skipped',
                                           'external_api_calls', 'formal_simulation_runs')}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
