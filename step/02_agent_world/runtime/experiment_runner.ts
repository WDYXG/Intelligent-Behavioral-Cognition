import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {STEP,readJSON,fileHash,requireThat,digest} from './source';
import {EventLog} from './runtime';
import {validateConfig,loadPopulation,requirementCheck} from './experiment_config';
import {CostLedger,makeBackend} from './experiment_backend';
import {executeRun} from './experiment_engine';
import {replayCheck,extractRows,basicStatistics} from './experiment_evaluation';

export async function runExperiment(configPath:string){
  const config=readJSON(configPath);validateConfig(config);
  const experimentId=new Date().toISOString().replace(/[:.]/g,'-')+'_'+config.backend.kind+'_'+randomUUID().slice(0,8);
  const dir=path.join(STEP,'03_experiment/experiments',experimentId);fs.mkdirSync(dir,{recursive:true});
  const write=(base:string,name:string,value:any)=>fs.writeFileSync(path.join(base,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
  const inventory=async(base:string)=>Object.fromEntries(await Promise.all(fs.readdirSync(base,{withFileTypes:true}).filter(e=>e.isFile()).map(async e=>[e.name,{sha256:await fileHash(path.join(base,e.name)),bytes:fs.statSync(path.join(base,e.name)).size}])));
  const ledger=new CostLedger(config.budget),manifest:any={schema_version:'experiment_manifest_v1',experiment_id:config.experiment_id,
    suite_id:experimentId,status:'running',started_at:new Date().toISOString(),backend:config.backend.kind,purpose:config.purpose,
    code:{},specs:{},runs:[],artifacts:{},learning_enabled:false,runtime_version:Bun.version,
    formal_study_completed:false,not_implemented:['formal professional layer','human behavior evaluation','Concordia adapter','M consolidation','durable transaction recovery']};
  const allRows:any={interactions:[],requests:[],results:[],observations:[]};
  try{
    write(dir,'experiment_config.json',config);write(dir,'requirements_check.json',requirementCheck());
    manifest.config_sha256=await fileHash(configPath);
    for(const file of ['experiment_runner.ts','experiment_config.ts','experiment_engine.ts','experiment_backend.ts','experiment_evaluation.ts','runtime.ts','projector.ts','source.ts'])manifest.code[file]=await fileHash(path.join(import.meta.dir,file));
    for(const file of ['Architecture_Spec.md','Data_Contract.md','Experiment_Spec.md'])manifest.specs[file]=await fileHash(path.join(STEP,'specs',file));
    const loaded=await loadPopulation(config,path.dirname(configPath));
    manifest.population={initialization_manifest:loaded.manifestPath,sha256:loaded.manifestHash,count:loaded.population.length,
      members:loaded.population.map(p=>({alias:p.alias,group:p.group,...p.source_ref}))};
    write(dir,'population.loaded.json',loaded.population);
    const backend=makeBackend(config,ledger);
    for(const condition of config.conditions)for(let repetition=0;repetition<config.repetitions;repetition++){
      const seed=(config.seed+repetition)>>>0,runId=`${condition.id}_repeat_${repetition+1}`;
      const runDir=path.join(dir,runId);fs.mkdirSync(runDir);
      const log=new EventLog(e=>fs.appendFileSync(path.join(runDir,'events.jsonl'),JSON.stringify(e)+'\n'));
      const runManifest:any={schema_version:'multi_agent_run_manifest_v1',run_id:runId,experiment_id:config.experiment_id,
        condition,repetition:repetition+1,seed,status:'running',started_at:new Date().toISOString(),config_sha256:digest(config),
        population_sha256:await fileHash(path.join(dir,'population.loaded.json')),code:manifest.code,specs:manifest.specs,
        backend:config.backend,learning_enabled:false,time_alignment:loaded.population[0].plan.time_alignment,
        scheduling:'simultaneous_collection_then_atomic_in_memory_commit',external_input:'none_all_participants_autonomous',
        tools:[],budget_before:ledger.snapshot(),artifacts:{}};
      try{
        const result=await executeRun(loaded.population,config,condition,seed,backend,log);
        write(runDir,'world.final.json',result.world);write(runDir,'agents.final.json',result.states);write(runDir,'episodes.json',result.episodes);
        const {report}=await replayCheck(result.events,loaded.population,config,condition,seed);write(runDir,'architecture_validation.json',report);
        const rows=extractRows(result.events,runManifest);write(runDir,'basic_statistics.json',basicStatistics(rows));
        for(const k of Object.keys(allRows))allRows[k].push(...rows[k as keyof typeof rows]);
        runManifest.status='completed';runManifest.final_event_hash=result.events.at(-1)?.hash;
      }catch(error){
        runManifest.status='failed';runManifest.error=error instanceof Error?error.message:'unknown_failure';
        if(log.events.at(-1)?.type!=='run_failed')log.add('run_failed',{message:runManifest.error});throw error;
      }finally{
        runManifest.finished_at=new Date().toISOString();runManifest.budget_after=ledger.snapshot();runManifest.artifacts=await inventory(runDir);
        write(runDir,'run_manifest.json',runManifest);
        manifest.runs.push({run_id:runId,status:runManifest.status,manifest:runId+'/run_manifest.json'});
      }
      console.log(`${runId}: completed (${loaded.population.length} agents, ${config.rounds} rounds)`);
    }
    write(dir,'basic_statistics.json',basicStatistics(allRows));manifest.status='completed';
  }catch(error){manifest.status='failed';manifest.error=error instanceof Error?error.message:'unknown_failure';
  }finally{
    manifest.finished_at=new Date().toISOString();manifest.cost=ledger.snapshot();manifest.artifacts=await inventory(dir);
    for(const run of manifest.runs)manifest.artifacts[run.manifest]={sha256:await fileHash(path.join(dir,run.manifest)),bytes:fs.statSync(path.join(dir,run.manifest)).size};
    write(dir,'experiment_manifest.json',manifest);
    console.log(JSON.stringify({status:manifest.status,output_directory:dir,runs:manifest.runs.length,error:manifest.error??null},null,2));
  }
  return {dir,manifest};
}
if(import.meta.main){
  requireThat(process.argv.length===3,'Usage: bun step/02_agent_world/runtime/experiment_runner.ts <experiment-config.json>');
  const {manifest}=await runExperiment(path.resolve(process.argv[2]));if(manifest.status!=='completed')process.exitCode=1;
}
