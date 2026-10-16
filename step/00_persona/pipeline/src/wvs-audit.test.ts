import {test, expect} from 'bun:test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadAudit, interpretAuditedField, interpretAuditedRow} from './wvs-audit.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const audit = await loadAudit(root);
const read = (field: string, raw?: string) => interpretAuditedField(field, raw, audit);

test('all 1834 audited observed codes are recognized without permitting projections', () => {
  let count = 0;
  for (const [field, spec] of audit.index) for (const code of spec.code_audit) {
    const result = read(field, String(code.code));
    expect(['excluded', 'source_category', 'source_missing']).toContain(result.interpretation_status);
    expect(result.persona_projection.allowed).toBe(false);
    if (field !== 'Q263') expect(result.missing_reason).toBe(code.missing_reason);
    count++;
  }
  expect(count).toBe(1834);
});
test('valid zero and negative category are not missing', () => {
  expect(read('Q119', '0').source_category_code).toBe(0);
  expect(read('Q284', '-5').source_category_code).toBe(-5);
});
test('missing causes remain distinct and ambiguous cause is not narrowed', () => {
  expect(read('Q289', '-2').missing_reason).toBe('no_answer_or_refused');
  expect(read('Q284', '-2').missing_reason).toBe('no_answer');
  expect(read('Q284', '-3').missing_reason).toBe('not_applicable');
});
test('exclusions and projection holds remain effective', () => {
  expect(read('Q263', '1').raw_value).toBe(null);
  for (const field of ['Q94', 'Q277', 'Q278']) expect(read(field, '1').audit_disposition).toBe('retained_projection_hold');
  expect(read('Q290', '156001').audit_disposition).toBe('retained_no_specific_ethnicity');
});
test('blank, absent, fractional and unrecognized values do not become categories', () => {
  expect(read('Q119').interpretation_status).toBe('field_absent');
  expect(read('Q119', '').interpretation_status).toBe('empty');
  for (const raw of ['1.5', 'abc', '999', 'NaN']) {
    expect(read('Q119', raw).interpretation_status).toBe('unrecognized_code');
    expect(read('Q119', raw).source_category_code).toBe(null);
  }
});
test('row interpretation is deterministic, source-bound and does not mutate input', () => {
  const row = {B_COUNTRY:'156', B_COUNTRY_ALPHA:'CHN', A_YEAR:'2018', version:'6-0-0 (2024-04-30)', S007:'1', Q119:'0'};
  const before = JSON.stringify(row);
  const output = interpretAuditedRow(row, audit);
  expect(output).toEqual(interpretAuditedRow(row, audit));
  expect(output.fields.length).toBe(291);
  expect(output.persona_created).toBe(false);
  expect(JSON.stringify(row)).toBe(before);
  expect(() => interpretAuditedRow({...row, A_YEAR:'2020'}, audit)).toThrow();
  expect(() => interpretAuditedRow({...row, B_COUNTRY_ALPHA:'USA'}, audit)).toThrow();
  expect(() => read('Q999', '1')).toThrow();
});
