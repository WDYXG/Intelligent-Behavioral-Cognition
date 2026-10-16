import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {hash,readJSON,loadBundle,validator} from './wvs.ts';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.resolve(process.argv[2]);
assert.ok(output.startsWith(root+path.sep),'Run must be inside this Persona project');
const bundle=await loadBundle(root), v=await validator(root);
const manifest=await readJSON(path.join(output,'manifest.json'));
v.schema('processing_manifest',manifest);
for(const a of [...manifest.input_artifacts,...manifest.output_artifacts]) {
  const file=path.resolve(root,a.relative_path);
  assert.ok(file.startsWith(root+path.sep),'Path escaped project');
  assert.equal(hash(await fs.readFile(file)),a.sha256,`Hash mismatch: ${a.relative_path}`);
}
const readLines=async(name:string)=>(await fs.readFile(path.join(output,name),'utf8')).trim().split('\n').map(s=>JSON.parse(s));
const snapshots=await readLines('snapshots.jsonl'), evidence=await readLines('evidence.jsonl'), personas=await readLines('personas.jsonl'), review=await readJSON(path.join(output,'review_rows.json'));
assert.equal(snapshots.length,manifest.record_counts.snapshot_records);
assert.equal(evidence.length,manifest.record_counts.evidence_records);
assert.equal(personas.length,manifest.record_counts.persona_records);
assert.equal(new Set(evidence.map(e=>e.evidence_id)).size,evidence.length);
assert.equal(new Set(personas.map(p=>p.persona_id)).size,personas.length);
for(const snapshot of snapshots) {
  const id=snapshot.source_identity.source_record_id;
  const row=Object.fromEntries(Object.values(snapshot.fields).map((f:any)=>[f.source_field,f.raw_value]));
  const result={snapshot,evidence:evidence.filter(e=>e.provenance.source_record_id===id),persona:personas.find(p=>p.source_identity.source_record_id===id),review:review.filter((r:any)=>r.record_id===id)};
  const meta={recordId:id,ordinal:snapshot.extensions.source_record_ordinal,hash:snapshot.source_artifact.source_hash,artifact:snapshot.source_artifact.artifact};
  v.check(result,bundle,meta,row);
}
const artifacts=await readJSON(path.join(output,'review_artifacts.json'));
assert.equal(hash(await fs.readFile(path.join(output,artifacts.workbook))),artifacts.sha256);
const report={checked_at:new Date().toISOString(),run_id:manifest.run_id,hashed_inputs:manifest.input_artifacts.length,hashed_core_outputs:manifest.output_artifacts.length,verified_snapshots:snapshots.length,verified_evidence_records:evidence.length,verified_personas:personas.length,workbook_hash_verified:true,status:'machine_checks_passed_human_review_pending'};
await fs.writeFile(path.join(output,'saved_files_verification.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
