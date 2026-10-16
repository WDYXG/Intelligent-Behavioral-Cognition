import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import JSZip from 'jszip';
import {parse} from 'csv-parse';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const source='raw/SRC_WVS7_CHN_2018/data/F00011356-WVS_Cross-National_Wave_7_csv_v6_0.zip';
const hash=(b:Uint8Array)=>createHash('sha256').update(b).digest('hex');
const bytes=await fs.readFile(path.join(root,source)),sha256=hash(bytes);
assert.equal(sha256,'234ddf03976f50f5d03f9e163bf346626a7bbd9afeae2028622d513617cb223d');
const zip=await JSZip.loadAsync(bytes,{checkCRC32:true});
const member='WVS_Cross-National_Wave_7_csv_v6_0.csv';const entry=zip.file(member);assert.ok(entry);
const parser=parse({bom:true,record_delimiter:['\r\n','\n','\r'],columns:true,skip_empty_lines:true});
const stream=entry.nodeStream();stream.on('error',e=>parser.destroy(e));stream.pipe(parser);
const groups=[['Q154','Q155'],['Q156','Q157']].map(([first,second])=>({first,second,pairs:new Map<string,number>(),counts:{first_missing_second_minus3:0,first_missing_second_not_minus3:0,first_valid_second_minus3:0,first_valid_second_no_answer:0,both_valid_distinct:0,both_valid_equal:0,other_pair:0}}));
let scanned=0,n=0;const ids=new Set<string>();const valid=(s:string)=>['1','2','3','4'].includes(s);
for await(const r of parser){
 scanned++;if(r.B_COUNTRY!=='156'||r.B_COUNTRY_ALPHA!=='CHN'||r.A_YEAR!=='2018')continue;
 assert.equal(r.version,'6-0-0 (2024-04-30)');assert.ok(!ids.has(r.S007));ids.add(r.S007);n++;
 for(const g of groups){
  const a=r[g.first],b=r[g.second],key=`${a},${b}`;g.pairs.set(key,(g.pairs.get(key)??0)+1);
  if(['-1','-2'].includes(a))g.counts[b==='-3'?'first_missing_second_minus3':'first_missing_second_not_minus3']++;
  else if(valid(a)&&b==='-3')g.counts.first_valid_second_minus3++;
  else if(valid(a)&&b==='-2')g.counts.first_valid_second_no_answer++;
  else if(valid(a)&&valid(b))g.counts[a===b?'both_valid_equal':'both_valid_distinct']++;
  else g.counts.other_pair++;
 }
}
assert.equal(scanned,97220);assert.equal(n,3036);assert.equal(hash(await fs.readFile(path.join(root,source))),sha256);
for(const g of groups)assert.equal(Object.values(g.counts).reduce((a,b)=>a+b,0),n);
const result={scope:'Q154_Q155_and_Q156_Q157_pair_checks_only',source,member,sha256,scanned,selected:n,filter:{B_COUNTRY:'156',B_COUNTRY_ALPHA:'CHN',A_YEAR:'2018'},groups:groups.map(g=>({first:g.first,second:g.second,counts:g.counts,pair_frequencies:[...g.pairs].map(([key,count])=>({first_code:Number(key.split(',')[0]),second_code:Number(key.split(',')[1]),count}))})),Q157_minus3_specific_reason:'unresolved_preserve_Not_applicable_Not_first_choice',checks:{unique_source_ids:true,counts_reconciled:true,raw_hash_unchanged:true},source_records_modified:false,full_question_audit_complete:false};
await fs.writeFile(path.join(root,'working/WVS7_full_audit_2026-09-14/Q154_Q157_pair_checks.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({selected:n,groups:result.groups.map(({pair_frequencies,...r})=>r),checks:result.checks}));
