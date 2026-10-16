// Read-only independent structural/semantic checks; no output writes.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import Ajv from '../../00_persona/pipeline/node_modules/ajv/dist/2020.js';

const here = import.meta.dir;
const out = path.join(here, 'selection_v1');
const read = (p:string) => JSON.parse(fs.readFileSync(p, 'utf8'));
const hash = (p:string) => createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const m = read(path.join(out, 'selection_manifest.json'));
const schema = read(path.join(here, 'selection_manifest_v1.schema.json'));
const validate = new Ajv({strict:true, allowUnionTypes:true, allErrors:true}).compile(schema);
assert.ok(validate(m), JSON.stringify(validate.errors));
const checks:string[] = ['JSON_schema_passed'];
assert.equal(hash(m.integrity.semantic_validation_report.path), m.integrity.semantic_validation_report.sha256);
assert.equal(hash(path.join(here,'select_respondents_v1.py')), m.protocol.selector_code_sha256);
assert.equal(hash(m.protocol.selection_plan.path), m.protocol.selection_plan.sha256);
for (const [name,a] of Object.entries(m.source.inputs) as [string,any][]) {
  if (name !== 'evidence') assert.equal(hash(a.path), a.sha256, name);
}
checks.push('saved_report_selector_plan_and_small_input_hashes_match');
const routing = read(m.source.inputs.routing_catalog.path);
const targets = new Set(routing.fields.filter((x:any)=>x.proposed_route==='VALIDATION_TARGET').map((x:any)=>x.field));
const allFields = new Set(routing.fields.filter((x:any)=>x.proposed_route!=='EXCLUDED').map((x:any)=>x.field));
const frames = {
  selection_frame: routing.fields.filter((x:any)=>x.source_disposition!=='source_not_measured' && !['EXCLUDED','VALIDATION_TARGET'].includes(x.proposed_route)).map((x:any)=>x.field),
  P_candidate: routing.fields.filter((x:any)=>x.proposed_route==='P_INIT').map((x:any)=>x.field),
  M0_candidate: routing.fields.filter((x:any)=>x.proposed_route==='M0_CANDIDATE').map((x:any)=>x.field),
  X0_candidate: routing.fields.filter((x:any)=>x.proposed_route==='X0_INIT').map((x:any)=>x.field),
};
const flagRows = fs.readFileSync(m.source.inputs.consistency_flags.path,'utf8').trim().split('\n').map(JSON.parse);
const flags = new Map(flagRows.map((r:any)=>[r.source_record_id,r.flags]));
const records = fs.readFileSync(m.source.inputs.standardized_records.path,'utf8').trim().split('\n').map(JSON.parse);
const lookup = new Map<string,any>();
const profiles = new Map<string,any>();
for (const r of records) {
  assert.equal(r.fields.length,290); assert.deepEqual(new Set(r.fields.map((f:any)=>f.field)),allFields);
  const f = Object.fromEntries(r.fields.filter((f:any)=>!targets.has(f.field)).map((f:any)=>[f.field,f]));
  lookup.set(r.source_record_id,f);
  const p:any = {};
  for (const [key,names] of Object.entries(frames)) {
    const missing:any = {}; let valid=0;
    for (const name of names as string[]) {
      if (f[name].status==='source_category') valid++;
      else {assert.equal(f[name].status,'source_missing'); const k=f[name].missing_reason; missing[k]=(missing[k]??0)+1;}
    }
    p[key]={denominator:names.length,valid_count:valid,missing_count:names.length-valid,missing_by_reason:missing};
  }
  p.consistency_flags=flags.get(r.source_record_id); profiles.set(r.source_record_id,p);
}
for (const key of Object.keys(frames)) {
  const values=[...profiles.values()].map(p=>p[key].valid_count).sort((a,b)=>a-b);
  assert.deepEqual(m.protocol.thresholds[key],{denominator:(frames as any)[key].length,q20:values[Math.ceil(.2*values.length)-1],q80:values[Math.ceil(.8*values.length)-1]});
}
checks.push('independently_recomputed_all_candidate_profiles_and_quantiles');
const countSlots = Array(12).fill(0);
const eligibility = new Map<string,boolean[]>();
const tagMap = new Map<string,string[]>();
const flagPartner = 'partner_employed_occupation_zero';
const flagAge = 'birth_year_plus_age_differs_from_2018_not_necessarily_error';
for (const [id,p] of profiles) {
  const f=lookup.get(id); const valid=(n:string,values:number[])=>f[n].status==='source_category'&&values.includes(f[n].value);
  const t=m.protocol.thresholds;
  const es=[p.selection_frame.valid_count>=t.selection_frame.q80,
    p.selection_frame.valid_count<=t.selection_frame.q20&&p.selection_frame.missing_count>0,
    ...['P_candidate','M0_candidate','X0_candidate'].map(k=>p[k].valid_count<=t[k].q20&&p[k].missing_count>0),
    valid('Q284',[-5]),valid('Q274',[7]),
    valid('Q279',[1,2,3])&&valid('Q281',[1,2,3,4,5,6,7,8,9,10]),
    valid('Q279',[4,5,6,7])&&valid('Q281',[1,2,3,4,5,6,7,8,9,10]),
    f.Q279.status==='source_missing'&&valid('Q281',[1,2,3,4,5,6,7,8,9,10]),
    p.consistency_flags.includes(flagPartner),f.Q286.status==='source_category'];
  es.forEach((v,i)=>{if(v)countSlots[i]++}); eligibility.set(id,es);
  const reasons=p.selection_frame.missing_by_reason;
  const hits=[valid('Q274',[0]),valid('Q33',[3])||valid('Q119',[0]),
    ...['no_answer','dont_know','not_applicable'].map(k=>(reasons[k]??0)>0),p.consistency_flags.includes(flagAge),
    ...['P_candidate','M0_candidate','X0_candidate'].map(k=>p[k].valid_count>=t[k].q80)];
  tagMap.set(id,m.protocol.auxiliary_tag_vocabulary.filter((_:any,i:number)=>hits[i]));
}
function semanticCheck(manifest:any){
  assert.equal(new Set(manifest.samples.map((s:any)=>s.source_record_id)).size,12);
  assert.deepEqual(manifest.samples.map((s:any)=>s.slot_id),Array.from({length:12},(_,i)=>`S${String(i+1).padStart(2,'0')}`));
  for (const [i,s] of manifest.samples.entries()){
    assert.equal(s.sample_alias,`VS${String(i+1).padStart(2,'0')}`);
    assert.ok(eligibility.get(s.source_record_id)![i]);
    assert.deepEqual(s.profile,profiles.get(s.source_record_id));
    assert.deepEqual(s.secondary_tags,tagMap.get(s.source_record_id));
    assert.equal(manifest.coverage.slots[i].candidate_count,countSlots[i]);
    assert.equal(manifest.coverage.slots[i].selected_source_record_id,s.source_record_id);
    for(const w of s.selection_evidence){
      if(w.kind==='aggregate_metric')assert.ok(w.aggregate_profile_ref?.startsWith('profile.'));
      else assert.ok(w.evidence_refs.length>0);
      for(const ref of w.evidence_refs){
        assert.ok((frames.selection_frame as string[]).includes(ref.source_field)); assert.ok(!targets.has(ref.source_field));
        const f=lookup.get(s.source_record_id)[ref.source_field];
        assert.equal(ref.evidence_id,f.evidence_id);assert.equal(ref.raw_value,f.raw);
        assert.equal(ref.normalized_value,f.value);assert.equal(ref.source_status,f.status);assert.equal(ref.missing_reason,f.missing_reason);
        assert.equal(ref.observation_time,'2018');assert.equal(ref.time_precision,'year');
        assert.equal(ref.reference_period,ref.source_field==='Q286'?'2017':null);
      }
    }
  }
  for (const c of manifest.coverage.auxiliary_tags){
    const expected=manifest.samples.filter((s:any)=>tagMap.get(s.source_record_id)!.includes(c.tag)).map((s:any)=>s.source_record_id);
    assert.deepEqual(c.source_record_ids,expected);assert.equal(c.covered,expected.length>0);
  }
}
semanticCheck(m);
checks.push('all_12_slot_memberships_and_candidate_counts_match','unique_ids_slots_and_aliases',
  'all_selected_profiles_missing_reasons_and_flags_match','all_witness_values_and_times_match_snapshot',
  'target_fields_absent_from_all_witnesses','all_auxiliary_coverage_claims_recomputed');
const badDuplicate=structuredClone(m);badDuplicate.samples[1].source_record_id=badDuplicate.samples[0].source_record_id;
assert.throws(()=>semanticCheck(badDuplicate));
const badMissing=structuredClone(m);badMissing.samples[0].profile.P_candidate.valid_count--;
assert.throws(()=>semanticCheck(badMissing));
const badTime=structuredClone(m);badTime.samples[11].selection_evidence[0].evidence_refs[0].reference_period='2018';
assert.throws(()=>semanticCheck(badTime));
const badTarget=structuredClone(m);badTarget.samples[11].selection_evidence[0].evidence_refs[0].source_field=[...targets][0];
assert.ok(!validate(badTarget));
const badCount=structuredClone(m);badCount.samples.pop();assert.ok(!validate(badCount));
checks.push('negative_test_duplicate_person_rejected','negative_test_false_profile_rejected',
  'negative_test_wrong_reference_period_rejected','negative_test_target_witness_rejected','negative_test_11_person_manifest_rejected');
console.log(JSON.stringify({status:'passed',scope:'saved_manifest_schema_and_independent_selection_semantics',
  checked_at:new Date().toISOString(),manifest_sha256:hash(path.join(out,'selection_manifest.json')),
  schema_sha256:hash(path.join(here,'selection_manifest_v1.schema.json')),verifier_sha256:hash(import.meta.path),
  checks,limits:['Optimization optimality comes from SciPy/HiGHS exact stages plus reversed-pool replay, not from this independent verifier.',
  'Full Evidence hashing and selected-record readback were performed by selector; this verifier rechecks compact snapshots and saved provenance.',
  'No runtime, AgentState or behavioral validation.']},null,2));
