import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import JSZip from 'jszip';
import {parse} from 'csv-parse';
import Ajv from 'ajv/dist/2020.js';
import {loadAudit, interpretAuditedRow, adapterVersion, auditPath} from './wvs-audit.ts';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const hash=(x:Buffer|string)=>createHash('sha256').update(x).digest('hex');
const rel='raw/SRC_WVS7_CHN_2018/data/F00011356-WVS_Cross-National_Wave_7_csv_v6_0.zip';
const bytes=await fs.readFile(path.join(root,rel));
const sourceHash=hash(bytes);
assert.equal(sourceHash,'234ddf03976f50f5d03f9e163bf346626a7bbd9afeae2028622d513617cb223d');
const audit=await loadAudit(root);
const schema=JSON.parse(await fs.readFile(path.join(root,'contracts/evidence_record_v0.1.json'),'utf8'));
const validate=new Ajv({strict:true,allowUnionTypes:true}).compile(schema);
const zip=await JSZip.loadAsync(bytes,{checkCRC32:true});
const member='WVS_Cross-National_Wave_7_csv_v6_0.csv';
const entry=zip.file(member); assert.ok(entry);
const rows:{row:Record<string,string>,ordinal:number}[]=[];
let scanned=0;
const parser=parse({bom:true,record_delimiter:['\r\n','\n','\r'],skip_empty_lines:true,columns:(headers:string[])=>{
  assert.equal(headers.length,613); assert.equal(new Set(headers).size,613);
  for(const field of [...audit.index.keys(),'S007','version','A_YEAR','B_COUNTRY','B_COUNTRY_ALPHA']) assert.ok(headers.includes(field));
  return headers;
}});
entry.nodeStream().on('error',e=>parser.destroy(e)).pipe(parser);
for await(const row of parser){
  scanned++;
  assert.equal(row.B_COUNTRY==='156',row.B_COUNTRY_ALPHA==='CHN');
  if(row.B_COUNTRY==='156' && row.A_YEAR==='2018') rows.push({row,ordinal:scanned});
}
assert.equal(scanned,97220); assert.equal(rows.length,3036);
assert.equal(new Set(rows.map(x=>x.row.S007)).size,3036);

function flags(row:Record<string,string>){
  const result:string[]=[];
  const child=Array.from({length:11},(_,i)=>row[`Q${i+7}`]);
  if(child.every(x=>x==='1'||x==='2')){
    const n=child.filter(x=>x==='1').length;
    if(n<5)result.push('child_qualities_under_five_not_corrected');
    if(n>5)result.push('child_qualities_over_five_not_corrected');
  } else result.push('child_qualities_incomplete');
  for(const [a,b] of [['Q152','Q153'],['Q154','Q155'],['Q156','Q157']]){
    if(Number(row[a])>0 && row[a]===row[b])result.push(`${a}_${b}_same_choice`);
    if(Number(row[a])<0 && Number(row[b])>0)result.push(`${a}_${b}_second_without_valid_first`);
  }
  for(const a of ['Q276','Q280'])if((row[a]==='-3')!==(row.Q282==='-3'))result.push(`${a}_Q282_inapplicable_flags_differ`);
  if(['1','2'].includes(row.Q273)&&row.Q282==='-3')result.push('partner_status_Q282_inapplicable_difference');
  if(['1','2','3'].includes(row.Q280)&&row.Q282==='0')result.push('partner_employed_occupation_zero');
  if(Number(row.Q261)>0&&Number(row.Q262)>0&&Number(row.Q261)+Number(row.Q262)!==2018)result.push('birth_year_plus_age_differs_from_2018_not_necessarily_error');
  return result;
}

function convert(input:typeof rows[number]){
  const interpreted=interpretAuditedRow(input.row,audit);
  const evidence:any[]=[];
  const compact:any[]=[];
  for(const f of interpreted.fields){
    assert.ok(!['unrecognized_code','empty','field_absent'].includes(f.interpretation_status),`${f.source_field}: unexpected source code`);
    if(f.interpretation_status==='excluded')continue;
    const eid=`ev:WVS7_CHN_2018:${input.row.S007}:${f.source_field}:audit03`;
    compact.push({field:f.source_field,raw:f.raw_value,value:f.source_category_code,status:f.interpretation_status,missing_reason:f.missing_reason,evidence_id:eid});
    const ev={
      contract_version:'evidence_record_v0.1',evidence_id:eid,
      provenance:{source_dataset:'WVS7_CHN_2018',source_version:input.row.version,source_record_id:input.row.S007,source_hash:sourceHash,source_locator:{artifact:`${rel}!/${member}`,location_type:'field',location_value:`parsed_record=${input.ordinal};${f.source_field}`}},
      observation:{assertion_kind:['Q260','Q290'].includes(f.source_field)?'direct_observation':'self_report',observed_value:f.raw_value,time:{observation_time:'2018',time_precision:'year',reference_period:f.source_field==='Q286'?'2017':null},sensitivity:'restricted_research'},
      normalization:{status:'normalized',normalized_value:f.source_category_code,mapping_rule_id:`AUDIT_${f.source_field}`,mapping_rule_version:adapterVersion,raw_missing_code:f.missing_reason===null?null:f.raw_value,transformation_note:'Source category code only, not a score; local wording and exceptions in audit_dictionary.json.'},
      projection:{target:'survey_snapshot_only',persona_path:null,projection_status:'not_applicable',prohibited_inference:['No automatic Persona projection, imputation, cross-source identity join or behavioral inference.']},
      quality:{quality_flags:['time_lag'],review_status:'draft',review_note:'Machine validated against approved audit; not individually human-validated.'},
      governance:{license_scope:'research_only',contains_direct_identifiers:false,reidentification_risk:'unknown',handling_note:'Local restricted research; do not publish respondent-level records.'},
      extensions:{source_field:f.source_field,published_or_approved_label:f.published_or_approved_label,missing_reason:f.missing_reason,specific_missing_reason:f.specific_missing_reason,audit_disposition:f.audit_disposition,audit_dictionary_ref:`audit_dictionary.json#${f.source_field}`,audit_sha256:audit.sha256,reference_period_note:'Unless explicitly specified, use original question context; not necessarily current state.'}
    };
    assert.ok(validate(ev),JSON.stringify(validate.errors));
    evidence.push(ev);
  }
  assert.equal(evidence.length,290);
  return {record:{source_record_id:input.row.S007,source_dataset:'WVS7_CHN_2018',source_version:input.row.version,fields:compact,excluded_fields:['Q263'],persona_created:false},evidence,flags:flags(input.row)};
}

// Gate the full run on real-record schema, raw-value and deterministic checks.
for(const i of [0,Math.floor(rows.length/4),Math.floor(rows.length/2),Math.floor(rows.length*3/4),rows.length-1]){
  const before=JSON.stringify(rows[i]);
  const a=convert(rows[i]);assert.deepEqual(a,convert(rows[i]));
  assert.equal(JSON.stringify(rows[i]),before);
  for(const ev of a.evidence)assert.equal(ev.observation.observed_value,rows[i].row[ev.extensions.source_field]);
}
console.log('Real sample gate passed: 5 records, schema and deterministic conversion. Starting full conversion.');
const out=path.join(root,'derived/WVS7_CHN_2018',`WVS_full_${new Date().toISOString().replace(/[:.]/g,'-')}`);
await fs.mkdir(out,{recursive:true});
const json=async(name:string,data:any)=>fs.writeFile(path.join(out,name),JSON.stringify(data,null,2)+'\n',{flag:'wx'});
await fs.writeFile(path.join(out,'audit_dictionary.json'),await fs.readFile(path.join(root,auditPath)),{flag:'wx'});
const handles=await Promise.all(['standardized_records.jsonl','evidence.jsonl','consistency_flags.jsonl'].map(n=>fs.open(path.join(out,n),'wx')));
const digests=handles.map(()=>createHash('sha256'));
const frequencies=new Map<string,number>();const flagCounts:Record<string,number>={};const statusCounts:Record<string,number>={};
let evidenceCount=0;
try{
  for(let i=0;i<rows.length;i++){
    const result=convert(rows[i]);
    const texts=[JSON.stringify(result.record)+'\n',result.evidence.map(e=>JSON.stringify(e)).join('\n')+'\n',JSON.stringify({source_record_id:rows[i].row.S007,flags:result.flags,automatic_correction:false})+'\n'];
    for(let k=0;k<3;k++){await handles[k].writeFile(texts[k]);digests[k].update(texts[k]);}
    evidenceCount+=result.evidence.length;
    for(const f of result.record.fields){const key=`${f.field}:${Number(f.raw)}`;frequencies.set(key,(frequencies.get(key)??0)+1);statusCounts[f.status]=(statusCounts[f.status]??0)+1;}
    for(const flag of result.flags)flagCounts[flag]=(flagCounts[flag]??0)+1;
    if((i+1)%500===0)console.log(`Converted ${i+1}/3036`);
  }
}finally{for(const handle of handles)await handle.close();}
let checked=0;
for(const [field,spec] of audit.index){
  if(field==='Q263')continue;
  for(const c of spec.code_audit){assert.equal(frequencies.get(`${field}:${c.code}`)??0,c.count,`Frequency mismatch ${field}:${c.code}`);checked++;}
}
assert.equal(evidenceCount,3036*290);
assert.equal(flagCounts.child_qualities_incomplete??0,14);
assert.equal(flagCounts.child_qualities_under_five_not_corrected??0,171);
assert.equal(flagCounts.child_qualities_over_five_not_corrected??0,0);
assert.equal(flagCounts.Q276_Q282_inapplicable_flags_differ??0,8);
assert.equal(flagCounts.Q280_Q282_inapplicable_flags_differ??0,7);
assert.equal(hash(await fs.readFile(path.join(root,rel))),sourceHash);
const files=['standardized_records.jsonl','evidence.jsonl','consistency_flags.jsonl'];
const outputs=files.map((file,i)=>({file,sha256:digests[i].digest('hex')}));
const report={status:'completed_machine_verified',scanned_records:scanned,converted_records:rows.length,audited_primary_fields:291,excluded_field:'Q263',output_fields_per_record:290,evidence_records:evidenceCount,frequency_cells_checked:checked,status_counts:statusCounts,consistency_flag_counts:flagCounts,real_sample_gate:5,all_evidence_schema_validated:true,raw_hash_unchanged:true,persona_records:0,API_calls:0,imputation:false,cross_source_join:false,behavior_validated:false,limits:['Source code normalization only; not complete semantic harmonization or Persona construction.','44 unmeasured questions retained as source-missing evidence, not individual attitudes.','Consistency flags are descriptive, not proof of respondent error.','No population weighting or independent human behavioral validation.']};
await json('quality_report.json',report);
await json('manifest.json',{adapter_version:adapterVersion,source_sha256:sourceHash,audit_sha256:audit.sha256,outputs,source:rel,code_sha256:hash(await fs.readFile(fileURLToPath(import.meta.url)))});
await fs.writeFile(path.join(out,'README.md'),`# WVS中国2018全量来源标准化\n\n已转换3036名受访者。291题审计范围中，Q263按既有决定排除，实际每人290个字段记录，共${evidenceCount}条证据（包括缺失和本来源未测量状态）。不是290个人格特质。\n\n## 文件\n\n- standardized_records.jsonl：每行一名受访者；原码、类别码、缺失和证据ID。\n- evidence.jsonl：逐题证据，全部通过 evidence_record_v0.1 合同检查。\n- audit_dictionary.json：中文题意、发布标签、专项决定、暂缓与禁止推断说明；与证据配套使用，不能只读类别码。历史决定中的未实施状态是历史文字，不代表当前运行状态。\n- consistency_flags.jsonl：跨题差异，仅标记，不修正答案。\n- quality_report.json：人数、频数核对、缺失状态和限制。\n- manifest.json：来源、规则与输出校验值。\n\n## 边界\n\n本地受限研究数据，不公开上传。不同来源不拼接；缺失不补；未生成Persona，未调用API。类别数字不是心理评分，暂缓字段不解除限制。其余322个非主问题列未纳入转换。5条真实样例机械检查后才全量运行；这不是独立真人行为验证，也不证明代表性。\n`,{flag:'wx'});
console.log(JSON.stringify({out,...report},null,2));
