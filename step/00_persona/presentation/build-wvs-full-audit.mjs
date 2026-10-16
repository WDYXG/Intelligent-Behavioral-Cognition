import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {Workbook,SpreadsheetFile,FileBlob} from '@oai/artifact-tool';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const dir=path.join(root,'working/WVS7_full_audit_2026-09-14');
const read=async name=>JSON.parse(await fs.readFile(path.join(dir,name),'utf8'));
const fields=await read('field_inventory.json'), freq=await read('question_frequencies.json'), manifest=await read('inventory_manifest.json');
const cn=await read('WVS7_China_Questionnaire_Mandarin_2018.pages.json');
const en=await read('WVS7_Codebook_Variables_Report_v6.0.pages.json');
const original=path.join(root,'working/WVS7_CHN_2018_field_audit_v0.1.xlsx');
const digest=async p=>createHash('sha256').update(await fs.readFile(p)).digest('hex');
const before=await digest(original);
const old=await SpreadsheetFile.importXlsx(await FileBlob.load(original));
const audit=old.worksheets.getItem('field_audit').getRange('A1:V18').values.slice(1);
const oldCodes=old.worksheets.getItem('observed_codes').getRange('A1:H211').values.slice(1);
const known=new Map(audit.map(r=>[r[1],r]));
const codes=new Map(oldCodes.map(r=>[`${r[1]}:${r[2]}`,r]));
let reconciled=0;
for(const row of oldCodes){const actual=freq.find(x=>x.field===row[1]&&Number(x.code)===Number(row[2]));assert.equal(actual?.count,row[4],`Existing count drift: ${row[1]}=${row[2]}`);reconciled++;}
const locators=fields.map(f=>{
  const escaped=f.field.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const pattern=new RegExp(`^\\s*${escaped}\\s+([^\\n]+)`,'m');
  const pages=en.filter(p=>p.pdf_page<=100&&pattern.test(p.text));
  const q=/^Q(\d+)$/.exec(f.field);
  const cp=q?cn.filter(p=>p.pdf_page>=3&&new RegExp(`Q\\s*${q[1]}(?![0-9_])(?:[.．、\\s]|$)`).test(p.text)):[];
  return {field:f.field,codebook_pages:pages.map(p=>p.pdf_page),codebook_heading:pages[0]?.text.match(pattern)?.[1]??'',questionnaire_candidate_pages:cp.map(p=>p.pdf_page),locator_status:'machine_index_not_semantic_approval'};
});
const locator=new Map(locators.map(l=>[l.field,l]));
await fs.writeFile(path.join(dir,'source_locators.json'),JSON.stringify(locators,null,2)+'\n');
const allNegative=fields.filter(f=>f.kind==='primary_question'&&f.negative===3036);
const issues=[
 ['SEM-01','Q33-Q41','中文选项3为“不知道”；国际Q33选项3为Neither agree nor disagree','中文问卷PDF4；代码本PDF16（Q33）','保留原码；本轮不映射中立态度；其余题逐一核对','待语义决策'],
 ['COV-01',`${allNegative.length}个主题号`,'全部记录都是负编码，不等于该维度已观测','字段盘点：负数N=3036；逐题回代码本核对','保留原码；不能生成这部分真人属性','待逐题核对负码含义'],
 ['SEM-02','Q284=-5','1015条为中国特殊有效选项，不是缺失','原确认表；代码本PDF91写China: None','继续保留“都不是”；不更改原规则','沿用已确认规则'],
 ['META-01','W_WEIGHT / S018 / PWGHT','CSV不存在wt_final；不同权重用途不能混用','CSV表头；代码本PDF8-9','本轮频数全部未加权；后续人口估计另定权重','已定位；统计用途待定'],
 ['COV-02','291 / 81 / 241','291个Q数字主字段，81个Q扩展字段，241个其他字段；非613道人格题','按CSV字段名分类，仅用于盘点','扩展题与技术/派生字段需单独核对，不重复投影','技术分类完成，语义待核'],
 ['SEM-03','Q7-Q17等矩阵题','仅按显式题号检索PDF会漏掉矩阵子项；候选页为空不代表未提问','中文问卷PDF3','人工查矩阵、共享题干与选项；不以正则匹配替代审计','待矩阵题核对'],
 ['DES-01','新态度/价值观字段','未来待预测行为和Persona输入可能重叠','研究设计尚未确定各题最终用途','先记录证据，不自动写入Persona或安排验证标签','待实验设计确认'],
 ['SCOPE-01','全字段语义审计','本表全量盘点已完成，但新增字段未逐题确认题意、跳题与选项','本表状态列明确标注','不能将本次结果称为全部审计通过','未完成；需后续语义审计']
];
const wb=Workbook.create(),tabs=[];
function table(name,headers,rows,widths){
 const s=wb.worksheets.add(name);s.showGridLines=false;const all=[headers,...rows];
 const r=s.getRangeByIndexes(0,0,all.length,headers.length);r.values=all;r.format.font={name:'Microsoft YaHei',size:11,color:'#202020'};r.format.fill='#FFFFFF';r.format.wrapText=true;r.format.verticalAlignment='center';r.format.rowHeight=name==='问题与下一步'?90:48;
 widths.forEach((w,i)=>s.getRangeByIndexes(0,i,all.length,1).format.columnWidthPx=w);
 const h=s.getRangeByIndexes(0,0,1,headers.length);h.format.fill='#EEEEEE';h.format.font={name:'Microsoft YaHei',size:11,bold:true,color:'#111111'};h.format.rowHeight=40;
 s.freezePanes.freezeRows(1);s.tables.add(`A1:${String.fromCharCode(64+headers.length)}${all.length}`,true,`Audit${tabs.length}`).showFilterButton=true;
 tabs.push({name,cols:headers.length,rows:all.length});return s;
}
table('审计范围',['项目','结果与边界'],[
 ['数据范围','WVS Wave7 v6.0.0，中国2018；3036条记录，613个字段。不是当代中国人口预测。'],
 ['本次完成','全部613字段计数、编码频数、原有17题210个编码频数复核、PDF来源候选页索引。'],
 ['尚未完成','新增题目逐题语义、量尺、跳题、缺失解释及Persona用途审批；不得把盘点完成写成审计全部通过。'],
 ['主字段与扩展',`${manifest.primary_questions}个纯Q数字字段（Q1-Q290加Q293）；${manifest.question_variants}个扩展字段；${manifest.technical_or_derived}个其他字段。分类依据名称，不是语义判断。`],
 ['全负编码题',`${allNegative.length}个主字段全部为负编码，仍需结合代码本判断未问/缺失等；不得补造其值。`],
 ['原规则复核',`${reconciled}个既有编码频数与新扫描完全一致；Q263继续排除；原审计与映射工作簿均不改写。`],
 ['统计口径','空字符串、负数、零、正数、非数值分别统计。负数不是统一缺失；频数比例分母固定3036，未加权。'],
 ['保密范围','仅本地受限研究。没有导出受访者身份列表、自由文本或拼接其他来源；未生成新Persona。'],
 ['来源定位','字段盘点列出中文问卷候选页和国际代码本标题；自动定位并非语义一致证明。完整来源与SHA256见同目录JSON。'],
 ['新字段状态','未人工审批。即使实际数据有数值，也不自动认定有效、不自动加入Persona。']
],[165,880]);
table('全字段盘点',['字段','名称（原审计/代码本）','字段类别','审计状态','总N','空N','负数N','零N','正数N','文本N','不同值数','数值最小','数值最大','负码与频数','中文PDF候选页','代码本PDF页','处理备注'],fields.map(f=>{
 const k=known.get(f.field),l=locator.get(f.field);
 return [f.field,k?.[2]||l.codebook_heading||'待核对',f.kind,k?'既有17题审计；频数已复核':'技术盘点完成；语义待审计',f.n,f.empty,f.negative,f.zero,f.positive,f.text,f.distinct,f.min,f.max,JSON.stringify(f.negative_codes),l.questionnaire_candidate_pages.join(',')||'未自动定位',l.codebook_pages.join(',')||'未自动定位',k?.[21]||'原码保留；不新增Persona映射'];
}),[110,330,190,220,75,75,85,75,85,85,100,115,115,260,140,140,420]);
const freqSheet=table('题目编码频数',['题号','原码','人数','分母N','占全体比例','既有中文标签','编码解释状态'],freq.map(f=>{const old=codes.get(`${f.field}:${f.code}`);return[f.field,Number(f.code),f.count,f.denominator,f.share,old?.[3]??'待查问卷/代码本',old?`沿用原审计：${old[7]}`:'未判定有效/缺失，不自动转换'];}),[115,90,100,100,125,330,330]);
freqSheet.getRange(`E2:E${freq.length+1}`).setNumberFormat('0.00%');
table('问题与下一步',['问题编号','涉及字段','发现','依据','当前处理','状态'],issues,[120,200,390,300,390,250]);
await wb.recalculate();
for(const t of tabs){
 const end=String.fromCharCode(64+Math.min(t.cols,6));const range=`A1:${end}${Math.min(t.rows,5)}`;
 const img=await wb.render({sheetName:t.name,range,scale:1,format:'png'});
 await fs.writeFile(path.join(dir,`${t.name}_preview.png`),new Uint8Array(await img.arrayBuffer()));
}
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},maxChars:1500});
const target=path.join(dir,'WVS7_CHN_2018_全字段审计盘点_v0.1.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(target);
assert.equal(await digest(original),before);
const result={file:target,sha256:await digest(target),existing_audit_sha256:before,existing_code_counts_reconciled:reconciled,fields:fields.length,frequency_rows:freq.length,all_negative_primary_questions:allNegative.length,sheets:tabs,formula_check:errors.ndjson,semantic_audit_complete:false};
await fs.writeFile(path.join(dir,'workbook_verification.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
