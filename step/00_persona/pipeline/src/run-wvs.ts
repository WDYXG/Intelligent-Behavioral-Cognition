import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import JSZip from 'jszip';
import {parse} from 'csv-parse';
import {convert,hash,loadBundle,validator,writeJSON} from './wvs.ts';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const args=process.argv.slice(2);
assert.ok(args.every((a,i)=>['--seed','--size'].includes(a) || (i>0 && ['--seed','--size'].includes(args[i-1]))),'Only --seed and --size are supported; full batch is not enabled');
const arg=(name:string, fallback:string)=>args.includes(name)?args[args.indexOf(name)+1]:fallback;
const seed=arg('--seed','20260906');
const size=Number(arg('--size','5'));
assert.ok(seed && Number.isInteger(size) && size>=1 && size<=10,'Review size must be 1..10');
const bundle=await loadBundle(root);
const check=await validator(root);
const rel='raw/SRC_WVS7_CHN_2018/data/F00011356-WVS_Cross-National_Wave_7_csv_v6_0.zip';
const bytes=await fs.readFile(path.join(root,rel));
const inputHash=hash(bytes);
assert.equal(inputHash,'234ddf03976f50f5d03f9e163bf346626a7bbd9afeae2028622d513617cb223d');
const zip=await JSZip.loadAsync(bytes,{checkCRC32:true});
const member='WVS_Cross-National_Wave_7_csv_v6_0.csv';
const entry=zip.file(member); assert.ok(entry,'Expected CSV entry missing');
let headers:string[]=[];
const selected:any[]=[];
const seen=new Set<string>();
let scanned=0,eligible=0;
const parser=parse({bom:true,record_delimiter:['\r\n','\n','\r'],columns:(h:string[])=>{headers=h; assert.equal(new Set(h).size,h.length); for(const k of ['S007','version','B_COUNTRY','B_COUNTRY_ALPHA','A_YEAR',...bundle.pack.field_rules.map((r:any)=>r.source_field)]) assert.ok(h.includes(k),`Missing CSV column ${k}`); return h;},skip_empty_lines:true});
const stream=entry.nodeStream();
stream.on('error',error=>parser.destroy(error));
stream.pipe(parser);
for await(const record of parser) {
  scanned++;
  const country=record.B_COUNTRY==='156', alpha=record.B_COUNTRY_ALPHA==='CHN';
  assert.equal(country,alpha,'Country code and alpha code disagree');
  if(!country || record.A_YEAR!=='2018') continue;
  assert.equal(record.version,'6-0-0 (2024-04-30)','Unexpected source version');
  eligible++;
  const id=record.S007;
  assert.match(id,/^\d+$/,'S007 must be a numeric record identifier');
  assert.ok(!seen.has(id),'Duplicate S007 within selected country/year'); seen.add(id);
  const rank=hash(`${seed}:${bundle.pack.source_dataset}:${id}`);
  if(selected.length<size || rank<selected[selected.length-1].rank) {
    const row=Object.fromEntries(bundle.pack.field_rules.filter((r:any)=>r.transform!=='always_skip').map((r:any)=>[r.source_field,record[r.source_field]]));
    selected.push({rank,row,meta:{recordId:id,ordinal:scanned,hash:inputHash,artifact:`${rel}!/${member}`}});
    selected.sort((a,b)=>a.rank.localeCompare(b.rank));
    if(selected.length>size)selected.pop();
  }
}
assert.equal(selected.length,size,'Too few matching records');
const results=selected.map(s=>{const r=convert(s.row,s.meta,bundle);check.check(r,bundle,s.meta,s.row);return r;});
assert.equal(hash(await fs.readFile(path.join(root,rel))),inputHash,'Raw file changed during run');
const runId=`WVS_review_${new Date().toISOString().replace(/[:.]/g,'-')}`;
const output=path.join(root,'derived/WVS7_CHN_2018',runId);
await fs.mkdir(output,{recursive:true});
const outputs:any[]=[];
async function outputFile(name:string,value:any,kind:string,jsonl=false) {
  const text=jsonl?value.map((x:any)=>JSON.stringify(x)).join('\n')+'\n':JSON.stringify(value,null,2)+'\n';
  await fs.writeFile(path.join(output,name),text,{flag:'wx'});
  outputs.push({relative_path:path.relative(root,path.join(output,name)).replaceAll('\\','/'),sha256:hash(text),kind});
}
await outputFile('snapshots.jsonl',results.map(x=>x.snapshot),'snapshot',true);
await outputFile('evidence.jsonl',results.flatMap(x=>x.evidence),'evidence_records',true);
await outputFile('personas.jsonl',results.map(x=>x.persona),'persona_initial_states',true);
await outputFile('review_rows.json',results.flatMap(x=>x.review),'quality_report');
const unresolved=results.flatMap(x=>x.review).filter(x=>x.normalized_value===null);
const quality={run_id:runId,status:'machine_checked_human_review_pending',scanned_records:scanned,eligible_records:eligible,converted_records:size,excluded_field:'Q263',rules_total:18,active_rules:17,evidence_records:results.reduce((n,r)=>n+r.evidence.length,0),unresolved_fields:unresolved.map(x=>({record_id:x.record_id,rule:x.mapping_rule_id,raw_value:x.raw_value})),selection:{method:'lowest_SHA256_seed_dataset_S007',seed,size,selected_source_record_ids:selected.map(s=>s.meta.recordId),purpose:'reproducible mechanical spot check; not weighted or population representative'},schema_validation:true,source_reference_validation:true,no_API_calls:true,scientific_behavior_validation:false,limitations:['Only 17 active approved background rules are processed.','2018 initial state, not present-day profile.','No target population or survey weighting has been applied.','No cross-source joins, missing-value imputation or normative trait generation.','Full-batch conversion and Concordia execution remain disabled.']};
await outputFile('quality_report.json',quality,'quality_report');
const inputs:any[]=[];
for(const [file,role] of [[rel,'raw_data'],[bundle.approved.approved_workbook,'other'],['working/WVS7_CHN_2018_field_audit_v0.1.xlsx','other'],['rules/WVS7_approved_tables_v0.1.json','other'],['rules/WVS7_CHN_2018_rules_v0.1.json','other'],['pipeline/src/wvs.ts','other'],['pipeline/src/run-wvs.ts','other'],['pipeline/package.json','other'],['pipeline/bun.lock','other'],...['source_snapshot','evidence_record','persona_initial_state','processing_manifest'].map(s=>[`contracts/${s}_v0.1.json`,'other'])]) inputs.push({relative_path:file,sha256:hash(await fs.readFile(path.join(root,file))),role});
const manifest={contract_version:'processing_manifest_v0.1',run_id:runId,source_dataset:bundle.pack.source_dataset,source_version:bundle.pack.source_version,input_artifacts:inputs,rule_bundle:{adapter_version:'wvs_review_adapter_v0.1.0',field_audit_version:'WVS7_CHN_2018_field_audit_v0.1',mapping_rule_versions:[bundle.pack.rule_pack_version],persona_projection_rule_versions:['WVS7_approved_tables_v0.1']},output_artifacts:outputs,record_counts:{input_records:size,snapshot_records:results.length,evidence_records:quality.evidence_records,persona_records:results.length,excluded_records:0,failed_records:0},boundary_attestations:{raw_bytes_preserved:true,cross_source_identity_joined:false,unobserved_attributes_imputed:false,target_behavior_in_persona:false}};
check.schema('processing_manifest',manifest);
await writeJSON(path.join(output,'manifest.json'),manifest);
await fs.writeFile(path.join(output,'README.md'),`# WVS 中国 2018 小样本处理\n\n本次转换 ${size} 条来源内记录。扫描 ${scanned} 条跨国记录后，找到 ${eligible} 条符合国家代码156、CHN、2018三条件的记录；只转换固定种子 ${seed} 对应的 ${size} 条。\n\n## 阅读顺序\n\n1. WVS_样本审查.xlsx（生成后）：查看原值、中文标签、标准值与 Persona 去向。\n2. review_rows.json：同一份逐字段清单的机器格式。\n3. snapshots.jsonl：17个有效规则对应的不可改写调查快照；Q263不输出。\n4. evidence.jsonl：证据编号、源CSV记录序号、字段、转换版本与缺失码。\n5. personas.jsonl：尚待人工抽查的Persona初始状态。\n6. quality_report.json、manifest.json：选择方法、数量、限制和文件SHA256。\n\n## 审查重点\n\n中文表述是否符合已确认问卷；当前职业与上一职业是否分流正确；缺失有没有保持为空。5条样本未必包含封顶或特殊负编码，因此这些边界另由合成测试覆盖，不能假称是真人样本覆盖。\n\n## 限制\n\n这是处理过程的机械检查，不是人口代表性或行为预测验证。未经抽查不得把draft_for_review改为confirmed。本目录包含受访者级研究派生数据，只在本地受限使用，不公开上传。初始状态与调查快照均不可被模拟覆盖，后续世界运行应创建独立状态。\n\nmanifest的input_records指实际转换的记录数；全文件扫描数和筛选数另见quality_report。Q263是排除字段，不是排除受访者。\n`,{flag:'wx'});
console.log(JSON.stringify({output,scanned,eligible,converted:size,evidence:quality.evidence_records,unresolved:unresolved.length},null,2));
