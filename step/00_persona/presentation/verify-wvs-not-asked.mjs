import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dir=path.join(root,'working/WVS7_full_audit_2026-09-14');
const read=async n=>JSON.parse(await fs.readFile(path.join(dir,n),'utf8'));
const inventory=await read('field_inventory.json');
const selected=inventory.filter(f=>f.kind==='primary_question'&&f.negative===3036);
assert.equal(selected.length,44);
const pages=await read('WVS7_Codebook_Variables_Report_v6.0.pages.json');
const lines=pages.flatMap(p=>p.text.split('\n').map(t=>({page:p.pdf_page,text:t.trim()})));
const fields=new Set(inventory.map(f=>f.field));
const isHeading=line=>fields.has(line.split(/\s+/)[0])||/^Q\d+[A-Z_0-9]*\s/.test(line);
const rows=selected.map(f=>{
 assert.deepEqual(f.negative_codes,{'-4':3036});assert.equal(f.distinct,1);
 const start=lines.findIndex(l=>l.text.startsWith(f.field+' '));assert.ok(start>=0);
 let end=start+1;while(end<lines.length&&!isHeading(lines[end].text))end++;
 let evidence=lines.slice(start,end).find(l=>/^-4-?\.-?\s*Not asked\s*$/.test(l.text));
 let route='question_entry';let annexHeading=null;
 if(!evidence){
   const page=f.field==='Q223'?360:['Q266','Q267','Q268'].includes(f.field)?379:null;
   assert.ok(page,`Unreviewed exception ${f.field}`);
   const source=pages.find(p=>p.pdf_page===page);assert.ok(source);
   annexHeading=source.text.split('\n').find(t=>f.field==='Q223'?t.includes('Political parties list (Q223)'):t.includes('Country of birth (Q266, Q267, Q268)'))?.trim();
   assert.ok(annexHeading,'Annex scope mismatch');
   const text=source.text.split('\n').map(s=>s.trim()).find(s=>/^-4\s+Not asked$/.test(s));assert.ok(text);
   evidence={page,text};route='explicitly_referenced_annex';
 }
 return {field:f.field,title:lines[start].text.replace(new RegExp('^'+f.field+'\\s+'),''),raw_code:-4,count:3036,question_pdf_page:lines[start].page,definition_pdf_page:evidence.page,definition_quote:evidence.text,evidence_route:route,annex_heading:annexHeading,code_status:'verified_not_asked',normalized_value:null,missing_reason:'not_asked',persona_projection:false,full_question_audit_complete:false};
});
const sourcePath='raw/SRC_WVS7_CHN_2018/docs/WVS7_Codebook_Variables_Report_v6.0.pdf';
const sha256=createHash('sha256').update(await fs.readFile(path.join(root,sourcePath))).digest('hex');
const out={scope:'44_constant_negative_primary_fields_code_minus4_only',source:sourcePath,source_sha256:sha256,verified_on:'2026-09-14',policy:'WVS7_CHN_2018_NOT_ASKED_POLICY',count:rows.length,exception_count:0,annex_count:rows.filter(r=>r.evidence_route!=='question_entry').length,rows,limitations:['核实的是此版本数据的编码标记，不额外断言实际访谈经过。','不代表整题语义审计或全部WVS审计完成。','没有重写原始数据、生成Persona或接入转换程序。']};
await fs.writeFile(path.join(dir,'not_asked_code_audit.json'),JSON.stringify(out,null,2)+'\n',{flag:'wx'});
const md=['# 44题未提问编码核验','', '日期：2026-09-14。状态：44/44题的原码-4定义核验完成；不是整题或整份问卷审计全部完成。','', '每题中国2018样本3036条均为-4。40题在题目条目内有明确Not asked定义；4题通过条目所指附录确认。没有发现需要改变已确认处理原则的例外。','', '按已确认原则：保留原码；标准值为空，原因not_asked；不作个人态度证据，不投影或补全Persona。此处只保存审计依据，尚未修改转换程序。','', '## 逐题依据','', '| 题号 | 代码本标题（英文，部分标题跨行） | 题目PDF页 | -4定义PDF页 | 原文依据 |','|---|---|---:|---:|---|',...rows.map(r=>`| ${r.field} | ${r.title.replaceAll('|','/')} | ${r.question_pdf_page} | ${r.definition_pdf_page} | ${r.definition_quote} |`),'','## 附录与边界','', '- Q223条目PDF72指向附录；PDF360明确标题Political parties list (Q223)，并列出-4 Not asked。','- Q266、Q267、Q268条目PDF85指向附录；PDF379明确该三题共用Country of birth编码，列出-4 Not asked。','- 页码为PDF文件页码；来源为本地v6.0代码本，SHA256记录在同名JSON。','- 核实的是该版本数据将这些回答标记为未提问；不能据此推断受访者态度，也不额外断言实际访谈过程。','- 原Excel盘点快照未刷新；本文件和not_asked_code_audit.json补充其后续审计结果。',''];
await fs.writeFile(path.join(dir,'未提问字段核验.md'),md.join('\n'),{flag:'wx'});
console.log(JSON.stringify({verified:rows.length,direct:rows.length-out.annex_count,annex:out.annex_count,exceptions:out.exception_count,source_sha256:sha256}));
