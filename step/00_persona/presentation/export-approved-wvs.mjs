import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const rel='working/WVS7_CHN_2018_mapping_rules_v0.1.xlsx';
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(root,rel)));
const out={approved_workbook:rel,sha256:createHash('sha256').update(await fs.readFile(path.join(root,rel))).digest('hex')};
for(const [sheet,range] of [['field_rules','A3:P21'],['value_map','A3:H58'],['persona_projection','A3:N21']]) {
  const [head,...rows]=wb.worksheets.getItem(sheet).getRange(range).values;
  out[sheet]=rows.filter(r=>r[0]!=null && r[0]!=='').map(r=>Object.fromEntries(head.map((h,i)=>[h,r[i]??null])));
}
const target=path.join(root,'rules/WVS7_approved_tables_v0.1.json');
const text=JSON.stringify(out,null,2)+'\n';
try {
  const current=await fs.readFile(target,'utf8');
  if(current!==text) throw new Error('Existing export differs. Review and version the changed rules instead of overwriting.');
  console.log('Existing approved export matches every exported cell.');
} catch(error) {
  if(error.code!=='ENOENT') throw error;
  await fs.writeFile(target,text,{flag:'wx'});
  console.log(target);
}
