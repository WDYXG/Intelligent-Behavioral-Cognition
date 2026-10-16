import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createInterface} from 'node:readline';

export const STEP = path.resolve(import.meta.dir, '../..');
export const readJSON = (p:string) => JSON.parse(fs.readFileSync(p, 'utf8'));
export function canonical(x:any):string {
  if (x === null || typeof x !== 'object') return JSON.stringify(x);
  if (Array.isArray(x)) return '[' + x.map(canonical).join(',') + ']';
  return '{' + Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',') + '}';
}
export const digest = (x:any) => createHash('sha256').update(canonical(x)).digest('hex');
export async function fileHash(p:string) {
  const h=createHash('sha256'); for await(const chunk of fs.createReadStream(p)) h.update(chunk); return h.digest('hex');
}
export function requireThat(ok:any, message:string):asserts ok { if(!ok) throw new Error(message); }
export async function loadSource(plan:any) {
  const selectionPath=path.join(STEP,'02_agent_world/vertical_slice/selection_v1/selection_manifest.json');
  const selection=readJSON(selectionPath);
  const sample=selection.samples.find((x:any)=>x.slot_id===plan.sample_slot);
  requireThat(sample,'selected slot missing');
  const inputs=selection.source.inputs;
  const hashes:any={};
  // Read-only verification of every pinned input, including the complete Evidence byte stream.
  for(const [name,v] of Object.entries(inputs) as [string,any][]) {
    const h=await fileHash(v.path); requireThat(h===v.sha256,`input hash mismatch: ${name}`);
    hashes[name]={path:v.path,sha256:h};
  }
  hashes.selection={path:selectionPath,sha256:await fileHash(selectionPath)};
  let record:any=null; let count=0;
  for await(const line of createInterface({input:fs.createReadStream(inputs.standardized_records.path),crlfDelay:Infinity})) {
    const row=JSON.parse(line); if(row.source_record_id===sample.source_record_id){record=row;count++;}
  }
  requireThat(count===1,'source identity must occur exactly once');
  const evidence:any[]=[];
  for await(const line of createInterface({input:fs.createReadStream(inputs.evidence.path),crlfDelay:Infinity})) {
    if(!line.includes('"source_record_id":"'+sample.source_record_id+'"')) continue;
    const e=JSON.parse(line); if(e.provenance.source_record_id===sample.source_record_id) evidence.push(e);
  }
  requireThat(evidence.length===290 && record.fields.length===290,'incomplete selected evidence');
  requireThat(new Set(evidence.map(e=>e.evidence_id)).size===290,'duplicate evidence');
  const audit=readJSON(inputs.source_audit.path);
  const catalog=readJSON(inputs.routing_catalog.path);
  const flags=sample.profile.consistency_flags;
  return {record,evidence,audit,catalog,flags,hashes,sample};
}

export function expandFields(s:string):string[] {
  return s.split(',').flatMap(piece=>{const [a,b]=piece.split('-').map(Number);return Array.from({length:(b??a)-a+1},(_,i)=>'Q'+(a+i));});
}
