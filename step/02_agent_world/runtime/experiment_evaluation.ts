import fs from 'node:fs';
import path from 'node:path';
import {digest,readJSON,fileHash,requireThat} from './source';
import {executeRun} from './experiment_engine';

export async function replayCheck(events:any[],population:any[],config:any,condition:any,seed:number){
  let previous:any=null;
  for(let i=0;i<events.length;i++){
    const {hash,...body}=events[i];requireThat(body.seq===i+1&&body.previous_hash===previous&&digest(body)===hash,'corrupt event chain');previous=hash;
  }
  requireThat(events.at(-1)?.type==='run_completed','cannot validate incomplete run as success');
  const inputs=events.filter(e=>e.type==='decision_input'),outputs=events.filter(e=>e.type==='decision_output');let cursor=0;
  requireThat(inputs.length===outputs.length,'decision input/output mismatch');
  const backend={kind:events[0].payload.backend,async decide(context:any,messages:any[]){
    requireThat(cursor<inputs.length,'replay missing decision');
    const input=inputs[cursor].payload,output=outputs[cursor++].payload;
    requireThat(digest(context)===digest(input.context)&&digest(messages)===digest(input.messages),'replay context/visibility mismatch');
    requireThat(output.agent_id===context.agent_id&&output.round===context.round&&output.attempt===context.attempt,'decision attribution mismatch');
    return {raw:output.raw,metadata:output.metadata};
  }};
  const replay=await executeRun(population,config,condition,seed,backend);
  requireThat(cursor===inputs.length&&digest(replay.events)===digest(events),'replay trajectory mismatch');
  return {result:replay,report:{status:'passed',event_chain:true,deterministic_response_replay:true,decision_visibility:true,
    P_M_immutable:true,world_log_consistency:true,delivery_X_consistency:true,request_result_separation:true,
    note:'Recorded response replay, not independent model resampling or human validation.'}};
}

export function extractRows(events:any[],meta:any){
  const base={run:meta.run_id,condition:meta.condition.id,repetition:meta.repetition,seed:meta.seed};
  const interactions=events.filter(e=>e.type==='world_transition').flatMap(e=>e.payload.interactions.map((i:any)=>({...base,...i})));
  const requests=events.filter(e=>e.type==='action_request').map(e=>({...base,...e.payload}));
  const results=events.filter(e=>e.type==='action_result').map(e=>({...base,...e.payload}));
  const observations=events.filter(e=>e.type==='observation_delivered').map(e=>({...base,agent_id:e.payload.recipient,
    round:e.payload.simulation_time.round,interaction_id:e.payload.content.interaction_id??e.payload.content.interaction?.interaction_id??null,...e.payload}));
  return {interactions,requests,results,observations};
}
export function filterRows(rows:any[],query:any){return rows.filter(r=>Object.entries(query).every(([k,v])=>k==='agent_id'?(r.agent_id===v||r.actor_id===v||r.actors?.includes(v)):r[k]===v));}
export function basicStatistics(rows:any){
  const bucket=(items:any[],key:(x:any)=>string)=>{const out:any={};for(const x of items){const k=key(x);out[k]=(out[k]??0)+1;}return out;};
  return {scope:'descriptive_engineering_counts_only',requests:rows.requests.length,executed:rows.results.filter((r:any)=>r.accepted).length,
    rejected:rows.results.filter((r:any)=>!r.accepted).length,interactions:rows.interactions.length,observations:rows.observations.length,
    action_counts:bucket(rows.results.filter((r:any)=>r.accepted),r=>r.executed_action),
    by_agent:bucket(rows.results,r=>r.agent_id),by_round:bucket(rows.results,r=>String(r.round)),
    by_run:bucket(rows.results,r=>r.run),by_condition:bucket(rows.results,r=>r.condition),
    scientific_claims:[],unimplemented:['human fidelity metrics','norm formation/internalization measures','formal mechanism effect estimators']};
}

export async function readExperiment(dir:string){
  const experiment=readJSON(path.join(dir,'experiment_manifest.json'));
  requireThat(experiment.status==='completed','experiment incomplete');
  const checked=async(base:string,manifest:any,rel:string)=>{
    const full=path.resolve(base,rel),relative=path.relative(base,full);requireThat(!relative.startsWith('..')&&!path.isAbsolute(relative),'unsafe artifact reference');
    requireThat(manifest.artifacts[rel]&&await fileHash(full)===manifest.artifacts[rel].sha256,'artifact hash mismatch: '+rel);return full;
  };
  for(const rel of Object.keys(experiment.artifacts))await checked(dir,experiment,rel);
  const config=readJSON(path.join(dir,'experiment_config.json')),population=readJSON(path.join(dir,'population.loaded.json'));
  requireThat(experiment.runs.length===config.conditions.length*config.repetitions,'missing configured runs');
  requireThat(new Set(experiment.runs.map((r:any)=>r.run_id)).size===experiment.runs.length,'duplicate runs');
  const populationHash=await fileHash(path.join(dir,'population.loaded.json'));
  const rows:any={interactions:[],requests:[],results:[],observations:[]},validation=[];
  for(const ref of experiment.runs){
    const manifestPath=await checked(dir,experiment,ref.manifest),runDir=path.dirname(manifestPath),manifest=readJSON(manifestPath);
    requireThat(manifest.status==='completed','run incomplete');
    const configuredCondition=config.conditions.find((c:any)=>c.id===manifest.condition.id);
    requireThat(configuredCondition&&digest(configuredCondition)===digest(manifest.condition),'condition configuration mismatch');
    requireThat(Number.isInteger(manifest.repetition)&&manifest.repetition>=1&&manifest.repetition<=config.repetitions,'invalid repetition');
    requireThat(manifest.run_id===`${manifest.condition.id}_repeat_${manifest.repetition}`&&manifest.run_id===ref.run_id,'run identity mismatch');
    requireThat(manifest.seed===((config.seed+manifest.repetition-1)>>>0),'repetition seed mismatch');
    requireThat(manifest.config_sha256===digest(config)&&manifest.population_sha256===populationHash,'run inputs mismatch');
    for(const rel of Object.keys(manifest.artifacts))await checked(runDir,manifest,rel);
    const events=fs.readFileSync(path.join(runDir,'events.jsonl'),'utf8').trim().split('\n').map(x=>JSON.parse(x));
    const {result,report}=await replayCheck(events,population,config,manifest.condition,manifest.seed);
    requireThat(events.at(-1).hash===manifest.final_event_hash,'manifest terminal hash mismatch');
    requireThat(digest(result.world)===digest(readJSON(path.join(runDir,'world.final.json'))),'saved World mismatch');
    requireThat(digest(result.states)===digest(readJSON(path.join(runDir,'agents.final.json'))),'saved states mismatch');
    requireThat(digest(result.episodes)===digest(readJSON(path.join(runDir,'episodes.json'))),'saved episodes mismatch');
    const extracted=extractRows(events,manifest);for(const k of Object.keys(rows))rows[k].push(...extracted[k as keyof typeof extracted]);
    validation.push({run:manifest.run_id,...report});
  }
  return {rows,validation,statistics:basicStatistics(rows)};
}

if(import.meta.main){
  requireThat(process.argv[2],'Usage: bun .../experiment_evaluation.ts <experiment-directory>');
  const result=await readExperiment(path.resolve(process.argv[2]));
  console.log(JSON.stringify({verified_runs:result.validation.length,...result.statistics},null,2));
}
