import path from 'node:path';
import {readJSON,fileHash,digest,requireThat} from './source';
import {personaView} from './projector';

export function validateConfig(c:any) {
  requireThat(c.schema_version==='experiment_runner_v1','unsupported experiment config');
  requireThat(c.purpose==='architecture_validation','formal study requires frozen operationalization, not this engineering kernel');
  requireThat(c.learning_enabled===false,'M consolidation unsupported');
  requireThat(c.scenario.kernel==='simultaneous_pair_coordination_v1','unsupported Scenario Kernel');
  requireThat(Number.isInteger(c.rounds)&&c.rounds>0&&Number.isInteger(c.repetitions)&&c.repetitions>0,'invalid run size');
  requireThat(Number.isSafeInteger(c.seed)&&c.seed>=0&&c.seed<=0xffffffff,'invalid seed');
  requireThat(['ring','complete','explicit'].includes(c.network.kind),'unsupported network');
  requireThat(['interaction_only','public_completed_round'].includes(c.observation.scope),'unsupported visibility');
  requireThat(Number.isInteger(c.observation.memory_window)&&c.observation.memory_window>=0,'invalid memory window');
  requireThat(c.mechanisms.x_update==='delivered_observation_v1'&&c.mechanisms.consolidator==='disabled','unsupported mechanism');
  requireThat(Number.isInteger(c.max_attempts)&&c.max_attempts>=1&&c.max_attempts<=3,'invalid attempt budget');
  requireThat(['offline','llm'].includes(c.backend.kind),'unsupported backend');
  requireThat(c.conditions.length>0&&new Set(c.conditions.map((x:any)=>x.id)).size===c.conditions.length,'duplicate/empty conditions');
  for(const x of c.conditions){
    requireThat(/^[A-Za-z0-9_-]+$/.test(x.id),'unsafe condition id');
    requireThat(typeof x.persona_enabled==='boolean'&&typeof x.professional_enabled==='boolean','missing condition switches');
    requireThat(!x.professional_enabled||c.mechanisms.professional_module==='public_rules_reference_v1','professional module not implemented');
  }
  for(const k of ['max_calls','max_tokens'])requireThat(Number.isSafeInteger(c.budget[k])&&c.budget[k]>0,'invalid budget '+k);
  requireThat(Number.isFinite(c.budget.max_usd)&&c.budget.max_usd>=0,'invalid USD budget');
  for(const k of ['match_reward','mismatch_reward'])requireThat(Number.isFinite(c.scenario[k]),'invalid payoff');
  requireThat(typeof c.scenario.norm==='string'&&c.scenario.norm.length>0,'missing public expectation');
}

export async function loadPopulation(config:any,configDir:string) {
  const manifestPath=path.resolve(configDir,config.population.initialization_manifest);
  const root=path.dirname(manifestPath),manifest=readJSON(manifestPath);
  requireThat(manifest.status==='completed','initialization batch incomplete');
  const members=config.population.members??manifest.subjects.map((s:any,i:number)=>({record_id:s.source_record_id,alias:'agent_'+String(i+1).padStart(2,'0'),group:'cohort'}));
  requireThat(members.length>=2,'need at least two autonomous agents');
  requireThat(new Set(members.map((x:any)=>x.record_id)).size===members.length,'duplicate real respondent');
  requireThat(new Set(members.map((x:any)=>x.alias)).size===members.length,'duplicate agent alias');
  const population=[];
  for(const member of members){
    requireThat(/^[A-Za-z0-9_-]+$/.test(member.alias),'invalid alias');
    const subject=manifest.subjects.find((s:any)=>s.source_record_id===member.record_id&&s.status==='completed');
    requireThat(subject,'requested population member unavailable');
    const verified=async(rel:string)=>{
      const file=path.resolve(root,rel),relative=path.relative(root,file);
      requireThat(!relative.startsWith('..')&&!path.isAbsolute(relative),'artifact escapes initialization root');
      requireThat(manifest.artifacts[rel]&&await fileHash(file)===manifest.artifacts[rel].sha256,'initialization artifact hash mismatch: '+rel);
      return readJSON(file);
    };
    const initial=await verified(subject.state_path),prefix=path.posix.dirname(subject.state_path.replaceAll('\\','/'));
    const plan=await verified(prefix+'/experiment_role_plan.json'),view=await verified(prefix+'/decision_persona_view.json');
    const routes=await verified(prefix+'/route_decisions.json');
    requireThat(digest(initial)===subject.initialization_hash,'initialization payload mismatch');
    requireThat(initial.schema_version==='agent_state_v1.0.0'&&initial.experiment_id===config.experiment_id,'initialization contract/experiment mismatch');
    requireThat(initial.source_identity.record_id===member.record_id&&initial.source_identity.kind==='human_grounded','source identity mismatch');
    requireThat(digest(plan)===initial.initialization_manifest.role_plan_sha256,'role plan mismatch');
    requireThat(plan.learning_enabled===false&&plan.human_target===null,'unsupported initial plan');
    requireThat(initial.M0.entries.length===0&&initial.X0.version===0&&initial.X0.observation_ids.length===0,'nonempty M0/X0 requires explicit authorization adapter');
    requireThat(digest(view.P)===digest(personaView(initial,plan)),'decision view differs from authorized view');
    for(const layer of Object.values(view.P) as any[])for(const [field,value] of Object.entries(layer) as [string,any][]){
      if(field.startsWith('Q')){
        requireThat(plan.decision_fields.includes(field)&&!plan.target_closure.reserved_fields.includes(field),'unauthorized decision field');
        requireThat(value.value===null||routes.some((r:any)=>r.source_field===field&&r.route==='P_INIT'&&r.decision_visible),'unrouted Persona value');
      }
    }
    population.push({alias:member.alias,group:member.group??'cohort',initial,view:view.P,plan,
      source_ref:{manifest_path:manifestPath,state_path:subject.state_path,initialization_hash:subject.initialization_hash,record_id:member.record_id}});
  }
  return {population,manifestPath,manifestHash:await fileHash(manifestPath)};
}

export function requirementCheck(){return {scope:'implementation_audit_not_experimental_result',items:[
  {spec:'RQ1-RQ4; sections 1,9',status:'not_implemented',gap:'Independent human behavior holdout, majority/no-Persona/statistical baselines, balanced accuracy/MCC, CIs and human retest are absent.'},
  {spec:'2x2; section 8',status:'engineering_switches_only',gap:'A/B/C/D input toggles keep World invariant. public_rules_reference_v1 is only an input plumbing probe, not a validated professional norm layer.'},
  {spec:'Scenario; sections 4,7',status:'engineering_extension',gap:'Multi-autonomous simultaneous pair scheduling extends the scripted Vertical Slice per user request; not a replacement for formal scenario operationalization.'},
  {spec:'Data/input boundaries; sections 5,6',status:'implemented',evidence:'Existing frozen states, role plans, routed decision views and initialization hashes are verified; no reprojection, targets, new M0 or personal X0.'},
  {spec:'Architecture Validation; section 9',status:'implemented_with_tests',evidence:'Isolation, simultaneous collection barrier, P/M freeze, per-recipient X/episodes, explicit failures, manifests and replay checks.'},
  {spec:'RQ5 and social norm evaluation',status:'deferred',gap:'Only offline trajectory extraction/basic counts; no post-hoc convention/internalization thresholds or policy conclusions.'},
  {spec:'Model/prompt/seed robustness; section 9',status:'runner_support_only',gap:'Seed/repetition/backend configuration exists; no formal robustness study or real API calls yet.'}
]};}
