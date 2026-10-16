import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {STEP,readJSON,fileHash,digest,requireThat} from './source';
import {prepareSource} from './persona_source';
import {project,personaView} from './projector';

export function parseArgs(args:string[]) {
  const options:any={};
  for(let i=0;i<args.length;i++){
    const key=args[i];
    if(key==='--all'){requireThat(!options.all,'duplicate --all');options.all=true;}
    else if(['--source','--selection','--plan','--mapping'].includes(key)){
      requireThat(!options[key.slice(2)]&&args[i+1]&&!args[i+1].startsWith('--'),'missing or duplicate '+key);
      options[key.slice(2)]=path.resolve(args[++i]);
    }else throw new Error('Unknown argument: '+key);
  }
  requireThat(Boolean(options.all)!==Boolean(options.selection),'Specify exactly one of --all or --selection <manifest>');
  return options;
}

export function individualPlan(base:any,dataset:string,recordId:string) {
  requireThat(/^[A-Za-z0-9_-]+$/.test(dataset)&&/^[A-Za-z0-9_-]+$/.test(recordId),'unsafe source identity');
  const p=structuredClone(base),id=dataset+'__'+recordId;
  requireThat(Array.isArray(p.scenario.permissions[base.agent_id]),'base permission missing');
  p.scenario.permissions={[id]:[...p.scenario.permissions[base.agent_id]]};
  delete p.sample_slot;p.agent_id=id;
  p.initialization_scope={mode:'initialization_only',base_role_plan_sha256:digest(base),source_record_id:recordId,
    authorization:'Explicit CLI cohort selection under the existing mapping and input restrictions; no simulation or LLM.'};
  return p;
}

export async function main(args=process.argv.slice(2)) {
  if(args.length===1&&args[0]==='--help'){
    console.log('bun step/02_agent_world/runtime/initialize_personas.ts --selection <selection_manifest.json>\n'+
      'bun step/02_agent_world/runtime/initialize_personas.ts --all\n'+
      'Optional: --source <source_config.json> --plan <role_plan.json> --mapping <mapping.json>\n'+
      'Default source: audited WVS7_CHN_2018. Initializes every selected ID (including S12).\n'+
      'No simulation or LLM. New output: step/03_experiment/persona_initializations/<dataset>/<timestamp_id>/');return;
  }
  const opts=parseArgs(args);
  const sourcePath=opts.source??path.join(STEP,'03_experiment/configs/wvs_persona_source_v1.json');
  const planPath=opts.plan??path.join(STEP,'03_experiment/configs/coordination_v1.json');
  const mappingPath=opts.mapping??path.join(STEP,'00_persona/rules/coordination_persona_mapping_v1.json');
  const config=readJSON(sourcePath),base=readJSON(planPath),mapping=readJSON(mappingPath);
  requireThat(/^[A-Za-z0-9_-]+$/.test(config.dataset),'unsafe dataset path');
  const selection=opts.selection?readJSON(opts.selection):null;
  const dir=path.join(STEP,'03_experiment/persona_initializations',config.dataset,
    new Date().toISOString().replace(/[:.]/g,'-')+'_'+randomUUID().slice(0,8));
  fs.mkdirSync(dir,{recursive:true});
  const artifacts:any={};
  const write=async(rel:string,value:any)=>{
    const file=path.join(dir,rel);fs.writeFileSync(file,JSON.stringify(value,null,2)+'\n',{flag:'wx'});
    artifacts[rel]={sha256:await fileHash(file),bytes:fs.statSync(file).size};
  };
  const manifest:any={schema_version:'persona_initialization_manifest_v1',status:'running',started_at:new Date().toISOString(),
    mode:'initialization_only',cohort:opts.all?'all':'selection',dataset:config.dataset,simulation_executed:false,llm_called:false,
    learning_enabled:false,expected_count:null,subjects:[],artifacts,code:{},specs:{},configuration:{},
    access:'local_restricted_research',limitations:['Audit target files are separated, not encrypted.',
      'No runtime or behavioral validation. Failure stops this batch; rerun creates a new directory, no resume.']};
  try {
    for(const [key,file] of Object.entries({source:sourcePath,plan:planPath,mapping:mappingPath,selection:opts.selection})){
      if(file)manifest.configuration[key]={path:file,sha256:await fileHash(file as string)};
    }
    for(const file of ['initialize_personas.ts','persona_source.ts','source.ts','projector.ts'])manifest.code[file]=await fileHash(path.join(import.meta.dir,file));
    for(const file of ['Architecture_Spec.md','Data_Contract.md','Experiment_Spec.md'])manifest.specs[file]=await fileHash(path.join(STEP,'specs',file));
    await write('source_config.json',config);await write('base_role_plan.json',base);await write('mapping_rules.json',mapping);
    if(selection)await write('selection_manifest.input.json',selection);
    console.log('Verifying source hashes and preparing cohort (no conversion yet)...');
    const pool=await prepareSource(config,selection);manifest.source_inputs=pool.hashes;manifest.expected_count=pool.count;
    for await(const source of pool.sources()){
      const recordId=source.record.source_record_id;
      const item:any={source_record_id:recordId,selection_slot:source.sample?.slot_id??null,status:'running'};
      manifest.subjects.push(item);
      try {
        const plan=individualPlan(base,config.dataset,recordId),projected=project(source,plan,mapping);
        const replay=project({...source,evidence:[...source.evidence].reverse()},plan,mapping);
        requireThat(projected.initialization_hash===replay.initialization_hash,'initialization nondeterministic');
        requireThat(Object.keys(projected.state.P).length===8,'incomplete Persona layers');
        const values:any[]=Object.values(projected.state.P).flatMap((layer:any)=>Object.values(layer));
        requireThat(values.filter(v=>v.value!==null).every(v=>v.provenance?.evidence_id),'missing provenance');
        const prefix='agents/'+recordId+'/';fs.mkdirSync(path.join(dir,prefix),{recursive:true});
        await write(prefix+'experiment_role_plan.json',plan);
        await write(prefix+'candidate_profile.json',{access:'audit_only_not_decision_input',identity:projected.state.source_identity,fields:projected.candidates,flags:source.flags});
        await write(prefix+'reserved_targets.sealed.json',{access:'offline_audit_only_never_runtime',scored:false,evidence:projected.sealed});
        await write(prefix+'route_decisions.json',projected.routes);
        await write(prefix+'agent_state.initial.json',projected.state);
        await write(prefix+'decision_persona_view.json',{access:'authorized_persona_view_only_not_full_decision_context',P:personaView(projected.state,plan)});
        const report={status:'passed',scope:'initialization_only',deterministic_initialization:true,initialization_hash:projected.initialization_hash,
          candidate_fields:projected.candidates.length,reserved_fields:projected.sealed.length,
          known:values.filter(v=>v.value!==null).length,unknown:values.filter(v=>v.value===null).length,
          per_layer:Object.fromEntries(Object.entries(projected.state.P).map(([k,l]:[string,any])=>[k,{total:Object.keys(l).length,known:Object.values(l).filter((v:any)=>v.value!==null).length}]))};
        await write(prefix+'initialization_validation.json',report);
        Object.assign(item,{status:'completed',agent_id:plan.agent_id,state_path:prefix+'agent_state.initial.json',
          initialization_hash:projected.initialization_hash,known:report.known,unknown:report.unknown});
        console.log(`${manifest.subjects.length}/${pool.count}: ${recordId} initialized`);
      }catch(error){item.status='failed';item.error=error instanceof Error?error.message:'unknown failure';throw error;}
    }
    requireThat(manifest.subjects.length===pool.count,'output count mismatch');manifest.status='completed';
  }catch(error){manifest.status='failed';manifest.error=error instanceof Error?error.message:'unknown failure';process.exitCode=1;
  }finally{
    manifest.finished_at=new Date().toISOString();
    fs.writeFileSync(path.join(dir,'initialization_manifest.json'),JSON.stringify(manifest,null,2)+'\n',{flag:'wx'});
    console.log(JSON.stringify({status:manifest.status,output_directory:dir,expected:manifest.expected_count,
      completed:manifest.subjects.filter((s:any)=>s.status==='completed').length,error:manifest.error??null},null,2));
  }
}
if(import.meta.main)await main();
