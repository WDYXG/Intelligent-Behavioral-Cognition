import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

export const auditPath = 'working/WVS7_full_audit_2026-09-14/final_audit_v0.3/final_field_status.json';
export const adapterVersion = 'wvs_audit_interpretation_v0.1';

export async function loadAudit(root: string) {
  const bytes = await fs.readFile(path.join(root, auditPath));
  const fields = JSON.parse(bytes.toString('utf8'));
  assert.equal(fields.length, 291);
  const index = new Map<string, any>();
  for (const field of fields) {
    assert.ok(!index.has(field.field), 'Duplicate audit field');
    assert.equal(field.audit_workflow, 'closed_with_documented_limits');
    assert.equal(new Set(field.code_audit.map((c: any) => c.code)).size, field.code_audit.length);
    index.set(field.field, field);
  }
  return {index, sha256: createHash('sha256').update(bytes).digest('hex'), relativePath: auditPath};
}

// Interpret released source codes only. This is not a Persona projection or a score.
export function interpretAuditedField(field: string, raw: string | undefined, audit: Awaited<ReturnType<typeof loadAudit>>) {
  const spec = audit.index.get(field);
  assert.ok(spec, `Field outside audited primary questions: ${field}`);
  const excluded = spec.current_disposition === 'excluded_existing_decision';
  const integer = typeof raw === 'string' && /^-?\d+$/.test(raw.trim());
  const code = integer ? Number(raw!.trim()) : null;
  const definition = code === null ? undefined : spec.code_audit.find((c: any) => c.code === code);
  const status = excluded ? 'excluded' : raw === undefined ? 'field_absent'
    : raw.trim() === '' ? 'empty' : !definition ? 'unrecognized_code'
    : definition.missing_reason !== null ? 'source_missing' : 'source_category';
  return {
    adapter_version: adapterVersion,
    source_field: field,
    raw_value: excluded ? null : raw ?? null,
    interpretation_status: status,
    source_category_code: status === 'source_category' ? code : null,
    published_or_approved_label: excluded ? null : definition?.published_or_approved_label ?? null,
    missing_reason: excluded ? null : definition?.missing_reason ?? null,
    specific_missing_reason: excluded ? null : definition?.specific_reason ?? null,
    audit_disposition: spec.current_disposition,
    persona_projection: {allowed: false, reason: 'This adapter produces source interpretation only; legacy projections remain separate.'},
    source_context: {
      title_zh: spec.title_zh,
      chinese_pdf_pages: spec.chinese_pdf_pages,
      codebook_pdf_pages: spec.codebook_pdf_pages,
      code_definition_source: definition?.source ?? null,
      decisions: structuredClone(spec.effective_decisions),
      issue_handling: structuredClone(spec.effective_issue_handling),
    },
    provenance: {audit_file: audit.relativePath, audit_sha256: audit.sha256},
    quality_flags: status === 'unrecognized_code' ? ['not_in_audited_observed_code_set_do_not_guess'] : [],
  };
}

export function interpretAuditedRow(row: Record<string, string>, audit: Awaited<ReturnType<typeof loadAudit>>) {
  assert.equal(row.B_COUNTRY, '156');
  assert.equal(row.B_COUNTRY_ALPHA, 'CHN');
  assert.equal(row.A_YEAR, '2018');
  assert.equal(row.version, '6-0-0 (2024-04-30)');
  assert.match(row.S007 ?? '', /^\d+$/);
  return {
    source_dataset: 'WVS7_CHN_2018', source_record_id: row.S007,
    source_version: row.version, observation_year: 2018,
    cross_source_joined: false,
    fields: [...audit.index.keys()].map(field => interpretAuditedField(field, row[field], audit)),
    persona_created: false,
  };
}
