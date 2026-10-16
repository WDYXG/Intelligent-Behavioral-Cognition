import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';

export const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
export const readJSON = async (file: string) => JSON.parse(await fs.readFile(file,'utf8'));
export const writeJSON = async (file: string, value: unknown) => fs.writeFile(file,JSON.stringify(value,null,2)+'\n',{flag:'wx'});
export const sections = ['stable_attributes','slow_attributes','fast_state','long_term_normative_state'];
export const asOf = {date_or_period:'2018',time_precision:'year'};

export async function loadBundle(root: string) {
  const approved = await readJSON(path.join(root,'rules/WVS7_approved_tables_v0.1.json'));
  const pack = await readJSON(path.join(root,'rules/WVS7_CHN_2018_rules_v0.1.json'));
  assert.equal(hash(await fs.readFile(path.join(root,approved.approved_workbook))),approved.sha256,'Approved workbook changed; export and reconcile rules first');
  assert.equal(approved.field_rules.length,18);
  assert.equal(approved.value_map.length,55);
  assert.equal(approved.persona_projection.length,18);
  for (const r of approved.field_rules) {
    const compiled = pack.field_rules.find((x:any)=>x.rule_id===r.rule_id);
    assert.ok(compiled,`Missing rule ${r.rule_id}`);
    assert.equal(compiled.source_field,r.source_field);
    assert.equal(compiled.standardized_field,r.standardized_field);
    assert.equal(compiled.retain_raw_as,r.retain_raw_as);
    const methodAliases:Record<string,string>={self_reported:'self_report',derived_from_source:'deterministic_derivation'};
    assert.equal(compiled.observation_method,methodAliases[r.observation_method]??r.observation_method);
    assert.ok(['confirmed','blocked_excluded'].includes(r.decision_status));
  }
  for (const v of approved.value_map) {
    assert.equal(v.mapping_status,'confirmed');
    assert.equal(v.is_valid,true);
    assert.deepEqual(pack.categorical_value_maps[v.source_field][String(v.raw_value)],v.standardized_value);
  }
  for (const p of approved.persona_projection) assert.equal(p.decision_status,'confirmed');
  return {approved,pack};
}

export function normalize(raw: string, r: any, pack: any) {
  const text=raw.trim();
  const n=text==='' ? NaN : Number(text);
  let valid=Number.isInteger(n), value:any=null, note='';
  if (r.transform==='always_skip') return {value:null,status:'excluded',missing:null,note:'Excluded by approved rule'};
  if (r.valid_codes) valid &&= r.valid_codes.includes(n);
  if (r.valid_range) {
    // Q261's workbook range is an observed sample range, not a universal validity rule.
    valid &&= r.source_field==='Q261' ? n>0 && n<=2018 : n>=r.valid_range[0] && n<=r.valid_range[1];
    if(valid && r.source_field==='Q261' && (n<1948 || n>2000)) note='outside_previously_observed_range';
  }
  if (valid) {
    if (r.transform.startsWith('categorical_value_map')) value=pack.categorical_value_maps[r.source_field][String(n)];
    else if (r.transform==='derived_top_code_indicator') value=n===7;
    else value=n;
    assert.notEqual(value,undefined,`Missing map ${r.rule_id}:${n}`);
  }
  const knownMissing = r.source_field==='Q284' ? [-3,-2] : [-1,-2,-3,-4,-5];
  return {value,status:!valid?'unresolved':r.transform==='derived_top_code_indicator'?'deterministically_derived':r.transform==='copy_valid_integer'?'unchanged':'normalized',missing:!valid && knownMissing.includes(n)?n:null,note:!valid ? (text===''?'empty_source_cell':knownMissing.includes(n)?'source_missing_code':'unrecognized_value_preserved_no_imputation') : note};
}

export function projectionPath(p:any, field:any, values:any) {
  if(field.normalized_value===null) return null;
  if(p.projection_status==='project') return p.persona_path;
  if(p.projection_status!=='conditional_project') return null;
  if(p.source_field==='Q281' && field.normalized_value==='no_occupational_group') return null;
  const job=values.employment_status_at_survey?.normalized_value;
  const suffix=p.source_field==='Q281'?'occupational_group':'sector';
  if(['full_time','part_time','self_employed'].includes(job)) return `fast_state.employment.current_job.${suffix}`;
  if(['retired','homemaker','student','unemployed'].includes(job)) return `slow_attributes.work_history.last_known_job.${suffix}`;
  return null;
}

export function convert(row:Record<string,string>, meta:any, bundle:any) {
  const {pack,approved}=bundle;
  const identity={source_dataset:pack.source_dataset,source_version:pack.source_version,source_record_id:meta.recordId,cross_source_joined:false};
  const prefix=`${pack.source_dataset}:${meta.recordId}:${meta.hash.slice(0,12)}`;
  const snapshot:any={contract_version:'source_snapshot_v0.1',snapshot_id:`snap:${prefix}`,source_identity:identity,source_artifact:{artifact:meta.artifact,source_hash:meta.hash},as_of:asOf,fields:{},restrictions:{source_internal_only:true,contains_target_behavior:false,raw_visibility:'restricted',not_for_cross_source_matching:true},extensions:{source_record_ordinal:meta.ordinal,rule_workbook_sha256:approved.sha256,excluded_source_fields:['Q263'],human_sample_review:'pending'}};
  const evidence:any[]=[];
  const review:any[]=[];
  const ruleVersion=pack.rule_pack_version;
  for(const r of pack.field_rules) {
    if(r.transform==='always_skip') continue;
    assert.ok(Object.hasOwn(row,r.source_field),`Missing source column ${r.source_field}`);
    const a=approved.field_rules.find((x:any)=>x.rule_id===r.rule_id);
    const norm=normalize(row[r.source_field],r,pack);
    const kind=r.observation_method==='interviewer_observed'?'direct_observation':r.observation_method;
    const time={observation_time:'2018',time_precision:'year',reference_period:r.source_field==='Q286'?'2017':'2018'};
    const flags=['time_lag', ...(kind==='self_report'?['self_report']:[]), ...(r.source_field==='Q274' && row.Q274==='7'?['top_coding']:[])];
    const field={source_field:r.source_field,raw_value:row[r.source_field],raw_missing_code:norm.missing,normalized_value:norm.value,normalization_status:norm.status,mapping_rule_id:r.rule_id,mapping_rule_version:ruleVersion,observation:{assertion_kind:kind,...time},quality_flags:flags,notes:`${a.transformation_rule} ${norm.note}`};
    snapshot.fields[r.standardized_field]=field;
    evidence.push({contract_version:'evidence_record_v0.1',evidence_id:`ev:${prefix}:${r.rule_id}`,provenance:{...identity,cross_source_joined:undefined,snapshot_id:snapshot.snapshot_id,source_hash:meta.hash,source_locator:{artifact:meta.artifact,location_type:'field',location_value:`parsed_record=${meta.ordinal};${r.source_field}`,context_note:`S007=${meta.recordId}; ordinal excludes CSV header`}},observation:{assertion_kind:kind,observed_value:row[r.source_field],time,sensitivity:'restricted_research'},normalization:{status:norm.status,normalized_value:norm.value,mapping_rule_id:r.rule_id,mapping_rule_version:ruleVersion,raw_missing_code:norm.missing,transformation_note:field.notes},projection:{target:'survey_snapshot_only',persona_path:null,projection_status:'not_applicable',prohibited_inference:[a.prohibited_inference]},quality:{quality_flags:flags,review_status:'draft',review_note:'Mapping rules confirmed; individual sample review pending'},governance:{license_scope:'research_only',contains_direct_identifiers:false,reidentification_risk:'unknown',handling_note:'Local restricted research. Do not publicly redistribute person-level derivatives.'},extensions:{standardized_field:r.standardized_field,retained_raw_name:r.retain_raw_as,missing_or_validation_note:norm.note}});
  }
  const persona:any={contract_version:'persona_initial_state_v0.1',persona_id:`persona:${prefix}`,source_identity:{...identity,source_snapshot_id:snapshot.snapshot_id},as_of:asOf,projection_status:'draft_for_review',stable_attributes:{},slow_attributes:{},fast_state:{},long_term_normative_state:{},unavailable_attributes:[],restrictions:{prohibited_uses:['cross_source_identity_join','unobserved_attribute_completion','behavior_prediction_claim_without_validation','overwrite_initial_state'],contains_target_behavior:false,source_scope_note:'WVS China 2018 source-internal initial snapshot; not a current real-person profile'},extensions:{human_sample_review:'pending',projection_rule_details:{},selection_purpose:'mechanical_review_only',missing_fields:[]}};
  for(const p of approved.persona_projection) {
    if(p.projection_status==='excluded') continue;
    const field=snapshot.fields[p.source_standardized_field];
    const ev=evidence.find(x=>x.normalization.mapping_rule_id===p.source_rule_id);
    const target=projectionPath(p,field,snapshot.fields);
    if(target) {
      const [section,...parts]=target.split('.');
      let value=field.normalized_value;
      const ids=[ev.evidence_id];
      if(p.source_rule_id==='MAP_Q274_COUNT') {
        value={exact_count:value===7?null:value,minimum_count:value};
        ids.push(evidence.find(x=>x.normalization.mapping_rule_id==='MAP_Q274_TOPCODED').evidence_id);
      }
      if(p.projection_status==='conditional_project') ids.push(evidence.find(x=>x.normalization.mapping_rule_id==='MAP_Q279').evidence_id);
      persona[section][parts.join('.')]={value,evidence_ids:ids,observation_status:p.source_rule_id==='MAP_Q274_COUNT'?'deterministic_derivation':field.observation.assertion_kind==='direct_observation'?'direct':field.observation.assertion_kind,update_policy:p.update_mode,notes:p.notes};
      persona.extensions.projection_rule_details[target]=p;
      ev.projection={...ev.projection,target:'persona_initial_state',persona_path:target,projection_status:'draft_for_review'};
    } else {
      persona.extensions.missing_fields.push({source_rule_id:p.source_rule_id,reason:field.normalized_value===null?'missing_or_invalid_source_value':p.projection_status==='conditional_project'?'job_context_not_projectable':p.projection_status});
    }
    const label=approved.value_map.find((x:any)=>x.source_field===p.source_field && String(x.raw_value)===field.raw_value)?.source_label_zh;
    review.push({record_id:meta.recordId,source_field:p.source_field,source_label:approved.field_rules.find((x:any)=>x.rule_id===p.source_rule_id).source_label_zh,raw_value:field.raw_value,raw_label:label??(field.normalized_value===null?(field.raw_missing_code!==null?`缺失代码 ${field.raw_missing_code}`:'未识别，保留原值'):field.raw_value),normalized_value:field.normalized_value,persona_path:target,persona_value:target?persona[target.split('.')[0]][target.split('.').slice(1).join('.')].value:null,decision:target?'进入初始状态':field.normalized_value===null?'缺失/无效，不填补':'仅保留快照或投影元数据',mapping_rule_id:p.source_rule_id,evidence_id:ev.evidence_id,observation_year:2018,reference_year:p.source_field==='Q286'?2017:2018,notes:`${p.projection_condition} ${field.notes}`});
  }
  // Explicit absence describes unavailable modules, not invented zero scores.
  persona.unavailable_attributes.push({persona_path:'long_term_normative_state',reason:'not_collected'});
  return JSON.parse(JSON.stringify({snapshot,evidence,persona,review}));
}

export async function validator(root:string) {
  const ajv=new Ajv2020({allErrors:true,strict:false});
  const checks:any={};
  for(const name of ['source_snapshot','evidence_record','persona_initial_state','processing_manifest']) checks[name]=ajv.compile(await readJSON(path.join(root,`contracts/${name}_v0.1.json`)));
  const schema=(name:string,value:any)=>assert.ok(checks[name](value),`${name}: ${JSON.stringify(checks[name].errors)}`);
  return {schema,check(result:any,bundle:any,meta:any,row:any) {
    schema('source_snapshot',result.snapshot); schema('persona_initial_state',result.persona);
    result.evidence.forEach((e:any)=>schema('evidence_record',e));
    assert.equal(new Set(result.evidence.map((e:any)=>e.evidence_id)).size,result.evidence.length);
    const byId=new Map(result.evidence.map((e:any)=>[e.evidence_id,e]));
    for(const section of sections) for(const attr of Object.values(result.persona[section]) as any[]) for(const id of attr.evidence_ids) {
      const ev:any=byId.get(id); assert.ok(ev,'Dangling evidence');
      assert.equal(ev.provenance.source_record_id,result.persona.source_identity.source_record_id,'Cross-person evidence');
      assert.equal(ev.provenance.source_dataset,result.persona.source_identity.source_dataset,'Cross-source evidence');
      assert.equal(ev.provenance.snapshot_id,result.snapshot.snapshot_id,'Wrong snapshot');
    }
    assert.ok(!Object.values(result.snapshot.fields).some((f:any)=>f.source_field==='Q263'));
    // Regenerate from whitelisted source fields to reject edited values, paths, extra traits and leakage.
    assert.deepEqual(result,convert(row,meta,bundle),'Output differs from approved source projection');
  }};
}
