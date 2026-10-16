import fs from 'node:fs';
import fsp from 'node:fs/promises';
import readline from 'node:readline';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const out=path.resolve(process.argv[2]);
const manifest=JSON.parse(await fsp.readFile(path.join(out,'manifest.json'),'utf8'));
const counts:Record<string,number>={};
const people=new Set<string>();
for(const artifact of manifest.outputs){
  const digest=createHash('sha256');
  const stream=fs.createReadStream(path.join(out,artifact.file));
  for await(const chunk of stream)digest.update(chunk);
  assert.equal(digest.digest('hex'),artifact.sha256);
  let n=0;
  for await(const line of readline.createInterface({input:fs.createReadStream(path.join(out,artifact.file)),crlfDelay:Infinity})){
    const record=JSON.parse(line); n++;
    if(artifact.file==='standardized_records.jsonl'){
      assert.ok(!people.has(record.source_record_id));people.add(record.source_record_id);
      assert.equal(record.fields.length,290);
      assert.equal(new Set(record.fields.map((f:any)=>f.field)).size,290);
      assert.ok(!record.fields.some((f:any)=>f.field==='Q263'));
    }
    if(artifact.file==='evidence.jsonl'){
      assert.ok(people.has(record.provenance.source_record_id));
      assert.equal(record.projection.persona_path,null);
      assert.notEqual(record.extensions.source_field,'Q263');
    }
  }
  counts[artifact.file]=n;
}
assert.equal(counts['standardized_records.jsonl'],3036);
assert.equal(counts['evidence.jsonl'],880440);
assert.equal(counts['consistency_flags.jsonl'],3036);
const report={status:'passed',counts,output_hashes_match:true,unique_respondents:people.size,excluded_field_absent:true,no_persona_projection:true};
await fsp.writeFile(path.join(out,'readback_verification.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report));
