import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {STEP,readJSON,fileHash,loadSource,digest,requireThat} from './source';
import {project} from './projector';
import {run,OfflineBackend,LLMBackend,EventLog,validateRun} from './runtime';

const backendArg=process.argv.find(x=>x.startsWith('--backend='))?.split('=')[1]??'offline';
requireThat(['offline','llm'].includes(backendArg),'backend must be offline or llm');
const planPath=path.join(STEP,'03_experiment/configs/coordination_v1.json');
const mapPath=path.join(STEP,'00_persona/rules/coordination_persona_mapping_v1.json');
const plan=readJSON(planPath),mapping=readJSON(mapPath);
const runId=new Date().toISOString().replace(/[:.]/g,'-')+'_'+backendArg+'_'+randomUUID().slice(0,8);
const dir=path.join(STEP,'03_experiment/runs',runId);fs.mkdirSync(dir,{recursive:true});
const write=(name:string,data:any)=>fs.writeFileSync(path.join(dir,name),JSON.stringify(data,null,2)+'\n',{flag:'wx'});
const log=new EventLog(e=>fs.appendFileSync(path.join(dir,'events.jsonl'),JSON.stringify(e)+'\n'));
const manifest:any={schema_version:'run_manifest_v1',run_id:runId,experiment_id:plan.experiment_id,status:'running',
  backend:backendArg,started_at:new Date().toISOString(),access:'local_restricted_research',learning_enabled:false,
  runtime_version:Bun.version,model:backendArg==='offline'?null:(process.env.VS_LLM_MODEL??null),specs:{},
  endpoint:backendArg==='offline'?null:'configured_via_environment_not_logged',seed:plan.backend.seed,
  configuration:{plan_sha256:await fileHash(planPath),mapping_sha256:await fileHash(mapPath)},code:{},artifacts:{},
  not_implemented:['Concordia adapter','M consolidation','multi autonomous agents','synthetic population','human fidelity evaluation','profession/Skill authorization'],
  scope:'single_agent_architecture_validation_only'};
for(const f of ['source.ts','projector.ts','runtime.ts','run.ts','verify_run.ts','runtime.test.ts']) manifest.code[f]=await fileHash(path.join(import.meta.dir,f));
for(const f of ['Architecture_Spec.md','Data_Contract.md','Experiment_Spec.md'])manifest.specs[f]=await fileHash(path.join(STEP,'specs',f));
try {
  write('experiment_role_plan.json',plan);write('mapping_rules.json',mapping);
  const source=await loadSource(plan);manifest.source_inputs=source.hashes;
  const projected=project(source,plan,mapping);
  const replay=project({...source,evidence:[...source.evidence].reverse()},plan,mapping);
  requireThat(projected.initialization_hash===replay.initialization_hash,'initialization nondeterministic');
  write('candidate_profile.json',{access:'audit_only_not_decision_input',identity:projected.state.source_identity,fields:projected.candidates,flags:source.flags});
  write('reserved_targets.sealed.json',{access:'offline_audit_only_never_runtime',scored:false,evidence:projected.sealed});
  write('route_decisions.json',projected.routes);write('agent_state.initial.json',projected.state);
  manifest.initialization_hash=projected.initialization_hash;
  const backend=backendArg==='offline'?new OfflineBackend():new LLMBackend(plan.backend.llm);
  const result=await run(projected.state,plan,backend,log);
  write('agent_state.final.json',result.state);write('world.final.json',result.world);write('episodic_memory.json',result.episodes);
  const validation=validateRun(result,projected.state,plan);
  if(backendArg==='offline') requireThat(Object.values(validation.checks.coverage).every(Boolean),'offline boundary coverage missing');
  const values=Object.values(projected.state.P).flatMap((layer:any)=>Object.values(layer)) as any[];
  write('architecture_validation.json',{...validation,deterministic_initialization:true,initialization_hash:projected.initialization_hash,
    provenance_complete:values.filter(v=>v.value!==null).every(v=>v.provenance?.evidence_id),
    candidate_fields:projected.candidates.length,reserved_fields:projected.sealed.length,persona_values:values.filter(v=>v.value!==null).length,
    persona_unknown:values.filter(v=>v.value===null).length,per_layer:Object.fromEntries(Object.entries(projected.state.P).map(([k,l]:[string,any])=>[k,{total:Object.keys(l).length,known:Object.values(l).filter((v:any)=>v.value!==null).length}])),
    coverage_limit:'Only selected S12 executed. Other 11 people were not initialized; top-code/negative-code and mutation tests are separate fixtures, not fabricated respondent evidence.'});
  manifest.status='completed';manifest.rounds=result.world.history.length;manifest.score=result.world.score;
  manifest.final_event_hash=result.events.at(-1)?.hash;
}catch(error){manifest.status='failed';manifest.failure=error instanceof Error?error.message:'unknown_error';
  if(log.events.at(-1)?.type!=='run_failed')log.add('run_failed',{message:manifest.failure});
  process.exitCode=1;
}finally{
  manifest.finished_at=new Date().toISOString();
  for(const name of fs.readdirSync(dir))manifest.artifacts[name]={sha256:await fileHash(path.join(dir,name)),bytes:fs.statSync(path.join(dir,name)).size};
  write('run_manifest.json',manifest);
  console.log(JSON.stringify({status:manifest.status,run_directory:dir,rounds:manifest.rounds,error:manifest.failure??null}));
}
