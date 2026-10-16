import {digest,expandFields,requireThat} from './source';

export function project(source:any,plan:any,mapping:any) {
  requireThat(plan.status==='frozen_for_architecture_test' && !plan.learning_enabled,'role plan not frozen or learning enabled');
  requireThat(plan.time_alignment.mode==='counterfactual_initialization_from_2018_snapshot','unsupported time alignment');
  const audit=new Map(source.audit.map((a:any)=>[a.field,a])) as Map<string,any>;
  const fields=new Map(source.record.fields.map((f:any)=>[f.field,f])) as Map<string,any>;
  const groups=new Map<string,any>();
  for(const g of mapping.groups) for(const f of expandFields(g.fields)){requireThat(!groups.has(f),'duplicate mapping');groups.set(f,g);}
  const blocked=new Set(plan.target_closure.reserved_fields);
  requireThat(new Set(source.record.fields.map((f:any)=>f.field)).size===source.record.fields.length,'duplicate source fields');
  requireThat(new Set(source.evidence.map((e:any)=>e.extensions.source_field)).size===source.evidence.length,'duplicate evidence fields');
  // Closure is independently pinned to the historical target-reservation set, not inferred from answers.
  const historical=source.catalog.fields.filter((f:any)=>f.proposed_route==='VALIDATION_TARGET').map((f:any)=>f.field);
  requireThat(historical.every((f:string)=>blocked.has(f)),'target closure omits reserved field');
  requireThat(plan.decision_fields.every((f:string)=>groups.has(f)&&!blocked.has(f)),'decision allowlist invalid');
  const P:any=Object.fromEntries(Array.from({length:8},(_,i)=>['L'+(i+1),{}]));
  const routes:any[]=[], candidates:any[]=[], sealed:any[]=[];
  for(const e of [...source.evidence].sort((a,b)=>a.extensions.source_field.localeCompare(b.extensions.source_field,'en',{numeric:true}))) {
    const f=e.extensions.source_field, a=audit.get(f), r=fields.get(f), g=groups.get(f);
    requireThat(a&&r,'unknown field');
    requireThat(e.provenance.source_dataset===source.record.source_dataset && e.provenance.source_record_id===source.record.source_record_id,'cross-person evidence');
    requireThat(e.evidence_id===r.evidence_id,'evidence reference mismatch');
    requireThat(e.observation.time.observation_time===plan.time_alignment.observation_year,'source time mismatch');
    requireThat(e.extensions.audit_sha256===source.hashes.source_audit.sha256,'audit hash mismatch');
    const code=a.code_audit.find((c:any)=>String(c.code)===String(r.raw));
    requireThat(code && code.normalized_value===r.value && e.normalization.normalized_value===r.value,'code/value mismatch');
    requireThat((code.missing_reason??null)===(r.missing_reason??null) && (e.extensions.missing_reason??null)===(r.missing_reason??null),'missingness mismatch');
    requireThat(['source_category','source_missing'].includes(r.status),'unknown source status');
    requireThat((r.status==='source_missing')===(r.value===null),'source status/value mismatch');
    let reason='no_mapping', route='EVIDENCE_ONLY';
    if(a.current_disposition==='excluded_existing_decision'){route='EXCLUDED';reason='source_excluded';}
    else if(a.current_disposition!=='retained_with_source_limits') reason=a.current_disposition;
    else if(blocked.has(f)){route='VALIDATION_TARGET';reason='reserved_not_scored';}
    else if(g){route=r.value===null?'EVIDENCE_ONLY':'P_INIT';reason=r.value===null?'source_missing':'explicit_baseline_mapping';}
    const provenance={evidence_id:e.evidence_id,source_field:f,source:e.provenance,assertion_kind:e.observation.assertion_kind,
      observation_time:e.observation.time.observation_time,reference_period:e.observation.time.reference_period,
      time_precision:e.observation.time.time_precision,raw_code:r.raw,missing_reason:r.missing_reason,
      mapping_rule_id:'baseline_item:'+f,mapping_rule_version:mapping.version,audit_ref:f,
      audit_sha256:source.hashes.source_audit.sha256,limitations:{prohibited_inference:e.projection.prohibited_inference,
        source_review:a.source_review_status,effective_decisions:a.effective_decisions,effective_issue_handling:a.effective_issue_handling,
        time_note:e.extensions.reference_period_note,scope:g?.limit??'Source-specific baseline response; no enduring trait inference.'}};
    let value:any=r.value===null?null:{code:r.value,label:e.extensions.published_or_approved_label};
    let transform=mapping.special_transforms[f]??'source_item_identity';
    if(value!==null && transform==='children_lower_bound') value={exact_count:r.value===7?null:r.value,minimum_count:r.value,topcoded:r.value===7};
    if(value!==null && transform==='china_education' && r.value===6) value={code:6,label:'大学层次教育（不能区分大专与本科）'};
    if(value!==null && transform==='religion_limits' && [0,9].includes(r.value)) value={code:r.value,label:r.value===0?'该次调查未报告具体宗教归属':'其他宗教，具体类别未明确'};
    if(value!==null && transform==='employment_context') {
      const employment=fields.get('Q279');
      if(blocked.has('Q279') || !employment || ![1,2,3,4,5,6,7].includes(employment.value)){value=null;route='EVIDENCE_ONLY';reason='employment_context_unknown_or_blocked';}
      else value={...value,context:[1,2,3].includes(employment.value)?'current_at_survey':'last_job_at_survey'};
    }
    if(a.current_disposition!=='retained_with_source_limits') value=null;
    const stateValue={ontology_key:g?.ontology_key??null,value,value_status:value===null?'unknown':(transform==='source_item_identity'?'observed':'derived'),
      source_item:{field:f,title:a.title_zh,group:a.group},provenance,
      dependency_evidence_refs:transform==='employment_context'&&!blocked.has('Q279')?[fields.get('Q279').evidence_id]:[],
      time_scope:transform==='age14_reference'?'when_respondent_was_14':(f==='Q286'?'2017_retrospective':'source_reference_period_not_extrapolated'),
      unavailable_reason:value===null?reason:null};
    // Candidate audit retains all field positions; reserved payloads go only to the sealed file.
    candidates.push({field:f,layer:g?.layer??null,disposition:a.current_disposition,
      evidence_id:e.evidence_id,...(blocked.has(f)?{payload:'sealed'}:{source_record:r,audit:e.extensions}),projection_reason:reason});
    if(blocked.has(f)) sealed.push(e);
    if(g && route!=='EXCLUDED' && !blocked.has(f)) P[g.layer][f]=stateValue;
    routes.push({experiment_id:plan.experiment_id,evidence_id:e.evidence_id,source_field:f,route,reason,
      decision_status:'final_for_this_experiment',ontology_key:g?.ontology_key??null,output_path:route==='P_INIT'?`P.${g.layer}.${f}`:null,
      mapping_rule_id:provenance.mapping_rule_id,mapping_rule_version:mapping.version,
      source_audit_sha256:source.hashes.source_audit.sha256,role_plan_sha256:digest(plan),mapping_sha256:digest(mapping),
      decision_visible:plan.decision_fields.includes(f)&&!!g&&!blocked.has(f),target_block_reason:blocked.has(f)?'reserved_payload_and_derivatives':null});
  }
  for(const [layer,keys] of Object.entries(mapping.unmeasured) as [string,string[]][]) for(const key of keys) P[layer][key]={ontology_key:key,value:null,value_status:'unknown',provenance:null,unavailable_reason:'not_measured_or_source_insufficient'};
  const M0={entries:[],status:'unknown_no_long_term_initialization',consolidated:false};
  const X0={version:0,observation_ids:[],last_observation:null,last_partner_action:null,visible_score:0,current_round:0};
  const state={schema_version:'agent_state_v1.0.0',agent_id:plan.agent_id,source_identity:{kind:'human_grounded',dataset:source.record.source_dataset,version:source.record.source_version,record_id:source.record.source_record_id},
    experiment_id:plan.experiment_id,P,M0,X0,state_version:0,consistency_flags:source.flags,
    initialization_manifest:{role_plan_sha256:digest(plan),mapping_sha256:digest(mapping),input_hashes:source.hashes,canonicalization:'sorted_object_keys_array_order_v1',time_alignment:plan.time_alignment}};
  return {state,routes,candidates,sealed,initialization_hash:digest(state)};
}

export function personaView(state:any,plan:any) {
  const P:any=Object.fromEntries(Array.from({length:8},(_,i)=>['L'+(i+1),{}]));
  for(const [layer,items] of Object.entries(state.P) as [string,any][]) for(const [key,v] of Object.entries(items) as [string,any][]) {
    if(plan.decision_fields.includes(key)) P[layer][key]={ontology_key:v.ontology_key,value:v.value,value_status:v.value_status,
      question:v.source_item.title,group:v.source_item.group,observation_time:v.provenance.observation_time,
      reference_period:v.provenance.reference_period,limitations:v.provenance.limitations,unavailable_reason:v.unavailable_reason};
    else if(v.provenance===null) P[layer][key]={ontology_key:v.ontology_key,value:null,value_status:'unknown'};
  }
  return P;
}
