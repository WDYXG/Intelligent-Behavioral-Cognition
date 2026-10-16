import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import JSZip from 'jszip';
import {parse} from 'csv-parse';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const output=path.join(root,'working/WVS7_full_audit_2026-09-14');
const source='raw/SRC_WVS7_CHN_2018/data/F00011356-WVS_Cross-National_Wave_7_csv_v6_0.zip';
const digest=(b:Uint8Array)=>createHash('sha256').update(b).digest('hex');
const bytes=await fs.readFile(path.join(root,source));
const sha256=digest(bytes);
assert.equal(sha256,'234ddf03976f50f5d03f9e163bf346626a7bbd9afeae2028622d513617cb223d');
const zip=await JSZip.loadAsync(bytes,{checkCRC32:true});
const member='WVS_Cross-National_Wave_7_csv_v6_0.csv';
const entry=zip.file(member);assert.ok(entry);
type Stat={field:string;kind:string;empty:number;numeric:number;text:number;negative:number;zero:number;positive:number;min:number|null;max:number|null;counts:Map<string,number>};
let columns:string[]=[];let stats:Stat[]=[];let scanned=0,selected=0;
const ids=new Set<string>();
const parser=parse({bom:true,record_delimiter:['\r\n','\n','\r'],skip_empty_lines:true,columns:(h:string[])=>{
  columns=h;assert.equal(new Set(h).size,h.length);
  stats=h.map(field=>({field,kind:/^Q\d+$/.test(field)?'primary_question':/^Q\d+/.test(field)?'question_variant':'technical_or_derived',empty:0,numeric:0,text:0,negative:0,zero:0,positive:0,min:null,max:null,counts:new Map()}));return h;
}});
const stream=entry.nodeStream();stream.on('error',e=>parser.destroy(e));stream.pipe(parser);
for await(const r of parser){
  scanned++;
  assert.equal(r.B_COUNTRY==='156',r.B_COUNTRY_ALPHA==='CHN');
  if(r.B_COUNTRY!=='156'||r.B_COUNTRY_ALPHA!=='CHN'||r.A_YEAR!=='2018')continue;
  assert.equal(r.version,'6-0-0 (2024-04-30)');assert.ok(!ids.has(r.S007));ids.add(r.S007);selected++;
  for(const s of stats){
    const v=r[s.field];
    if(v.trim()===''){s.empty++;continue;}
    s.counts.set(v,(s.counts.get(v)??0)+1);
    if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(v)){s.text++;continue;}
    const n=Number(v);assert.ok(Number.isFinite(n));s.numeric++;
    if(n<0)s.negative++;else if(n===0)s.zero++;else s.positive++;
    s.min=s.min===null?n:Math.min(s.min,n);s.max=s.max===null?n:Math.max(s.max,n);
  }
}
assert.equal(scanned,97220);assert.equal(selected,3036);assert.equal(columns.length,613);
const fields=stats.map(({counts,...s})=>{
  assert.equal(s.empty+s.numeric+s.text,selected);
  assert.equal(s.negative+s.zero+s.positive,s.numeric);
  return {...s,n:selected,distinct:counts.size,negative_codes:Object.fromEntries([...counts].filter(([v])=>Number(v)<0)),status:'technical_profile_complete_semantic_review_pending'};
});
// Export categorical question counts only, never respondent identifiers or free text.
const frequencies=stats.filter(s=>s.kind!=='technical_or_derived'&&s.text===0&&s.counts.size<=100).flatMap(s=>[...s.counts].sort((a,b)=>Number(a[0])-Number(b[0])).map(([code,count])=>({field:s.field,code,count,denominator:selected,share:count/selected,meaning:'pending_source_review'})));
const primary=fields.filter(f=>f.kind==='primary_question');
const manifest={source,member,sha256,source_version:'6-0-0 (2024-04-30)',filter:{B_COUNTRY:'156',B_COUNTRY_ALPHA:'CHN',A_YEAR:'2018'},scanned,selected,columns:columns.length,primary_questions:primary.length,question_variants:fields.filter(f=>f.kind==='question_variant').length,technical_or_derived:fields.filter(f=>f.kind==='technical_or_derived').length,missing_Q1_to_Q290:Array.from({length:290},(_,i)=>`Q${i+1}`).filter(q=>!columns.includes(q)),constant_primary_questions:primary.filter(f=>f.distinct===1).map(f=>f.field),all_negative_primary_questions:primary.filter(f=>f.negative===selected).map(f=>f.field),weight_named_columns:columns.filter(c=>/weight|wght|^S01[78]$|^PWGHT$/i.test(c)),frequency_rows:frequencies.length,checks:{unique_S007:ids.size===selected,all_fields_accounted:true,all_counts_reconciled:true,raw_hash_unchanged:digest(await fs.readFile(path.join(root,source)))===sha256},boundaries:{semantic_audit_complete:false,new_persona_fields_approved:false,personas_generated:0,negative_codes_automatically_missing:false,weighted_estimates:false},script_sha256:digest(await fs.readFile(fileURLToPath(import.meta.url)))};
assert.ok(manifest.checks.raw_hash_unchanged);
await fs.mkdir(output,{recursive:true});
for(const [name,value]of Object.entries({'field_inventory.json':fields,'question_frequencies.json':frequencies,'inventory_manifest.json':manifest}))await fs.writeFile(path.join(output,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(manifest,null,2));
