import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const output=path.resolve(process.argv[2]);
const rows=JSON.parse(await fs.readFile(path.join(output,'review_rows.json'),'utf8'));
const quality=JSON.parse(await fs.readFile(path.join(output,'quality_report.json'),'utf8'));
const evidence=(await fs.readFile(path.join(output,'evidence.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
const approved=JSON.parse(await fs.readFile(path.resolve(output,'../../../rules/WVS7_approved_tables_v0.1.json'),'utf8'));
const wb=Workbook.create();
const value=x=>x===null?'未提供/不投影':typeof x==='object'?JSON.stringify(x):x;
const tabs=[];
function table(name,headers,data,widths) {
  const s=wb.worksheets.add(name);s.showGridLines=false;
  const all=[headers,...data];
  s.getRangeByIndexes(0,0,all.length,headers.length).values=all;
  const used=s.getRangeByIndexes(0,0,all.length,headers.length);
  used.format.font={name:'Microsoft YaHei',size:11,color:'#202020'};
  used.format.fill='#FFFFFF';used.format.wrapText=true;used.format.verticalAlignment='center';used.format.rowHeight=66;
  widths.forEach((w,i)=>s.getRangeByIndexes(0,i,all.length,1).format.columnWidthPx=w);
  const h=s.getRangeByIndexes(0,0,1,headers.length);h.format.fill='#EEEEEE';h.format.font={name:'Microsoft YaHei',size:11,bold:true,color:'#111111'};h.format.rowHeight=30;
  if(name!=='阅读说明') {s.freezePanes.freezeRows(1);s.tables.add(`A1:${String.fromCharCode(64+headers.length)}${all.length}`,true,`Review${tabs.length}`).showFilterButton=true;}
  tabs.push({name,s,cols:headers.length,rows:all.length});
  return s;
}
table('阅读说明',['项目','说明'],[
  ['本次目的','检查原始数据到 Persona 字段的转换是否符合已确认规则，不检验行为预测能力。'],
  ['数据与日期','WVS Wave 7 v6.0.0，中国2018年调查；不是2026年当前画像。'],
  ['处理范围',`${quality.eligible_records}条中国记录中固定种子选取${quality.converted_records}条，生成${quality.evidence_records}条证据。Q263完全排除。`],
  ['抽样方法',`SHA256(seed:dataset:S007)排序取前${quality.converted_records}条；seed=${quality.selection.seed}。不使用调查权重，不宣称样本代表性。`],
  ['怎么审查','先看“字段审查”的原值、中文标签和标准值，再看初始值与去向。看不懂的行可用题号直接讨论。'],
  ['如何追溯','“证据定位”提供同一行的规则编号和证据ID。源CSV记录序号不含表头，不等于物理文本行号。'],
  ['特殊规则','Q284=-5有效；子女数7表示7人及以上；Q281/Q284按Q279分流当前或上一工作。边界另有合成测试覆盖。'],
  ['缺失与推断','缺失不填补。不推断职业能力、政治态度、规范倾向。不合并其他数据集的人。'],
  ['审查状态','程序检查已完成；5条真人样本仍待你审查。程序检查通过不等于真人数据或行为预测已验证。'],
  ['文件性质','这是固定输出清单，不是可编辑后自动重算的映射工作表。修改语义需回到规则文件并重新生成。'],
  ['存储边界','仅限本地受限研究；不公开上传本表或受访者级派生文件。'],
  ['数量与哈希','同目录quality_report.json记录选择过程，manifest.json记录输入和核心输出哈希，review_artifacts.json记录本表哈希。']
],[180,930]);
table('字段审查',['记录编号','题号与中文字段','原值','中文选项/原值','标准值','Persona初始值','去向','投影条件'],rows.map(r=>[r.record_id,`${r.source_field}\n${r.source_label}`,r.raw_value,r.raw_label,value(r.normalized_value),r.persona_path?value(r.persona_value):r.normalized_value===null?'缺失/无效':'不投影',r.persona_path??r.decision,approved.persona_projection.find(p=>p.source_rule_id===r.mapping_rule_id).projection_condition]),[110,190,65,180,220,220,300,380]);
table('证据定位',['记录编号','规则编号','证据ID','来源位置','观察/参考年份'],rows.map(r=>{const e=evidence.find(e=>e.evidence_id===r.evidence_id);return[r.record_id,r.mapping_rule_id,r.evidence_id,`${e.provenance.source_locator.artifact}\n${e.provenance.source_locator.location_value}`,`${r.observation_year} / ${r.reference_year}`];}),[110,190,390,600,130]);
const inspections=[];
for(const t of tabs) {
  const range=`A1:${String.fromCharCode(64+t.cols)}${Math.min(t.rows,7)}`;
  inspections.push((await wb.inspect({kind:'table',range:`'${t.name}'!${range}`,tableMaxRows:7,tableMaxCols:t.cols,maxChars:3500})).ndjson);
  const img=await wb.render({sheetName:t.name,range,scale:1,format:'png'});
  await fs.writeFile(path.join(output,`${t.name}_preview.png`),new Uint8Array(await img.arrayBuffer()));
}
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},maxChars:2000});
await fs.writeFile(path.join(output,'workbook_inspection.ndjson'),inspections.join('\n')+'\n'+errors.ndjson);
const filename='WVS_样本审查.xlsx';
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(output,filename));
const sha256=createHash('sha256').update(await fs.readFile(path.join(output,filename))).digest('hex');
await fs.writeFile(path.join(output,'review_artifacts.json'),JSON.stringify({workbook:filename,sha256,source:'review_rows.json',row_count:rows.length,formula_errors:errors.ndjson,rendered_sheets:tabs.map(t=>t.name)},null,2));
console.log(JSON.stringify({file:path.join(output,filename),rows:rows.length,sheets:tabs.map(t=>t.name),formula_errors:errors.ndjson}));
