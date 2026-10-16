import fs from 'node:fs';
import path from 'node:path';
import {readJSON,fileHash,digest,requireThat} from './source';
import {validateRun} from './runtime';

const dir=path.resolve(process.argv[2]??'');
const read=(name:string)=>readJSON(path.join(dir,name));
const manifest=read('run_manifest.json');
requireThat(manifest.status==='completed','run not completed');
for(const [name,meta] of Object.entries(manifest.artifacts) as [string,any][]){
  requireThat(path.basename(name)===name,'invalid artifact path');
  requireThat(await fileHash(path.join(dir,name))===meta.sha256,'artifact hash mismatch: '+name);
  requireThat(fs.statSync(path.join(dir,name)).size===meta.bytes,'artifact size mismatch: '+name);
}
const initial=read('agent_state.initial.json');
requireThat(digest(initial)===manifest.initialization_hash,'initial state hash mismatch');
const events=fs.readFileSync(path.join(dir,'events.jsonl'),'utf8').trim().split('\n').map(x=>JSON.parse(x));
requireThat(events.at(-1)?.hash===manifest.final_event_hash,'final event mismatch');
const state=read('agent_state.final.json'),world=read('world.final.json'),episodes=read('episodic_memory.json');
const validation=validateRun({state,world,episodes,events},initial,read('experiment_role_plan.json'));
requireThat(digest(events.at(-1).payload.world)===digest(world),'saved world differs from completed event');
const delivered=events.filter(e=>e.type==='observation_delivered').map(e=>e.payload);
requireThat(digest(delivered)===digest(episodes),'saved episodes differ from delivery log');
const transitions=events.filter(e=>e.type==='state_transition');
let previous=initial.X0;
for(let i=0;i<transitions.length;i++){
  const t=transitions[i].payload;
  requireThat(digest(t.before)===digest(previous),'X transition discontinuity');
  requireThat(t.cause_refs[0]===episodes[i]?.observation_id,'X transition has no delivered cause');
  requireThat(t.after.version===t.before.version+1,'X version discontinuity');
  previous=t.after;
}
requireThat(transitions.length===episodes.length&&digest(previous)===digest(state.X),'saved X differs from delivery transitions');
requireThat(episodes.length===state.X.observation_ids.length,'episode count mismatch');
requireThat(new Set(state.X.observation_ids).size===episodes.length,'duplicate observations');
console.log(JSON.stringify({status:validation.status,verified_artifacts:Object.keys(manifest.artifacts).length,
  rounds:world.history.length,score:world.score,observations:episodes.length,scope:'architecture_only'},null,2));
