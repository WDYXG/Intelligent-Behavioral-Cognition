import fs from 'node:fs';
import {createInterface} from 'node:readline';
import {readJSON,fileHash,requireThat} from './source';

async function* rows(file:string) {
  for await(const line of createInterface({input:fs.createReadStream(file),crlfDelay:Infinity})){
    if(line.trim())yield JSON.parse(line);
  }
}

// A source adapter, not a population selector. The same iterator serves any ID set or all records.
export async function prepareSource(config:any,selection:any=null) {
  requireThat(config.adapter==='wvs_audited_v1','unsupported source adapter');
  requireThat(config.evidence_order==='contiguous_by_respondent','unsupported evidence order');
  const hashes:any={};
  for(const [name,v] of Object.entries(config.inputs) as [string,any][]){
    const hash=await fileHash(v.path);requireThat(hash===v.sha256,'source hash mismatch: '+name);
    hashes[name]={path:v.path,sha256:hash};
  }
  const records=new Map<string,any>(),flags=new Map<string,any>();
  for await(const row of rows(config.inputs.standardized_records.path)){
    requireThat(!records.has(row.source_record_id),'duplicate source identity');
    requireThat(row.source_dataset===config.dataset&&row.source_version===config.version,'source dataset/version mismatch');
    records.set(row.source_record_id,row);
  }
  requireThat(records.size===config.expected_records,'source population count mismatch');
  for await(const row of rows(config.inputs.consistency_flags.path)){
    requireThat(records.has(row.source_record_id)&&!flags.has(row.source_record_id),'invalid consistency flag identity');
    flags.set(row.source_record_id,row.flags);
  }
  requireThat(flags.size===records.size,'incomplete consistency flags');
  let selected:Map<string,any>|null=null;
  if(selection){
    requireThat(selection.source?.dataset===config.dataset&&selection.source?.version===config.version,'selection source mismatch');
    for(const key of ['standardized_records','evidence'])requireThat(selection.source.inputs[key].sha256===hashes[key].sha256,'selection snapshot mismatch');
    requireThat(Array.isArray(selection.samples)&&selection.samples.length>0,'empty selection');
    selected=new Map();
    for(const s of selection.samples){
      requireThat(records.has(s.source_record_id)&&!selected.has(s.source_record_id),'unknown or duplicate selected identity');
      selected.set(s.source_record_id,s);
    }
  }
  const audit=readJSON(config.inputs.source_audit.path),catalog=readJSON(config.inputs.routing_catalog.path);
  const wanted=selected??records;
  async function* sources(){
    let id:string|null=null,evidence:any[]=[];
    const visited=new Set<string>(),emitted=new Set<string>();
    function finish(){
      if(id===null)return null;
      requireThat(!visited.has(id),'non-contiguous evidence identity');visited.add(id);
      requireThat(records.has(id),'evidence identity outside source');
      if(!wanted.has(id))return null;
      requireThat(evidence.length===config.fields_per_record,'incomplete evidence group');
      requireThat(new Set(evidence.map(e=>e.evidence_id)).size===evidence.length,'duplicate evidence id');
      const record=records.get(id);requireThat(record.fields.length===config.fields_per_record,'incomplete standardized record');
      emitted.add(id);
      return {record,evidence,audit,catalog,flags:flags.get(id),hashes,sample:selected?.get(id)??null};
    }
    for await(const e of rows(config.inputs.evidence.path)){
      const next=e.provenance.source_record_id;
      if(next!==id){const source=finish();if(source)yield source;id=next;evidence=[];}
      if(wanted.has(next))evidence.push(e);
    }
    const last=finish();if(last)yield last;
    requireThat(emitted.size===wanted.size,'missing requested respondents');
    requireThat(visited.size===records.size,'incomplete source evidence coverage');
  }
  return {hashes,count:wanted.size,sources};
}
