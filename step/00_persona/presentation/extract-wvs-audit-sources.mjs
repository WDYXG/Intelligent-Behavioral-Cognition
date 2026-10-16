import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {getDocument} from 'pdfjs-dist/legacy/build/pdf.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.join(root,'working/WVS7_full_audit_2026-09-14');
await fs.mkdir(out,{recursive:true});
const sources=[];
for(const file of ['WVS7_China_Questionnaire_Mandarin_2018.pdf','WVS7_Codebook_Variables_Report_v6.0.pdf']){
  const relative=`raw/SRC_WVS7_CHN_2018/docs/${file}`;
  const bytes=await fs.readFile(path.join(root,relative));
  const doc=await getDocument({data:new Uint8Array(bytes),useSystemFonts:true}).promise;
  const pages=[];
  for(let p=1;p<=doc.numPages;p++){
    const page=await doc.getPage(p);const content=await page.getTextContent();
    const lines=[];let last=null;
    for(const item of content.items){if(!('str'in item))continue;const y=item.transform[5];if(last===null||Math.abs(last-y)>2)lines.push([]);lines.at(-1).push(item.str);last=y;}
    pages.push({pdf_page:p,text:lines.map(l=>l.join(' ')).join('\n')});
  }
  const name=file.replace('.pdf','.pages.json');
  await fs.writeFile(path.join(out,name),JSON.stringify(pages,null,2)+'\n',{flag:'wx'});
  sources.push({source:relative,sha256:createHash('sha256').update(bytes).digest('hex'),pages:pages.length,extracted:name,warning:'Machine extracted text; verify PDF layout before semantic decisions.'});
  console.log(`${file}: ${pages.length} pages`);
}
await fs.writeFile(path.join(out,'document_sources.json'),JSON.stringify(sources,null,2)+'\n',{flag:'wx'});
