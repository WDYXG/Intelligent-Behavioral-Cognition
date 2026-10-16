import {clone,freeze,EventLog} from './runtime';
import {digest,requireThat} from './source';
import {makeMessages,type ExperimentBackend} from './experiment_backend';

export function rng(seed:number){let x=seed>>>0;return ()=>{x=(Math.imul(1664525,x)+1013904223)>>>0;return x/4294967296;};}
function shuffle<T>(items:T[],seed:number){const a=[...items],r=rng(seed);for(let i=a.length-1;i>0;i--){const j=Math.floor(r()*(i+1));[a[i],a[j]]=[a[j],a[i]];}return a;}
export function schedule(pop:any[],network:any,seed:number,round:number){
  const ids=pop.map(p=>p.alias),groups=new Map(pop.map(p=>[p.alias,p.group]));
  let edges:string[][]=[];
  if(network.kind==='complete'){
    const order=shuffle(ids,seed+round);for(let i=0;i<order.length;i++)for(let j=i+1;j<order.length;j++)edges.push([order[i],order[j]]);
  }else if(network.kind==='ring'){
    const offset=(seed+round-1)%ids.length;const order=[...ids.slice(offset),...ids.slice(0,offset)];
    for(let i=0;i<order.length;i++)edges.push([order[i],order[(i+1)%order.length]]);
  }else edges=shuffle(network.edges??[],seed+round);
  const used=new Set<string>(),pairs:any[]=[];
  for(const edge of edges){
    requireThat(Array.isArray(edge)&&edge.length===2&&edge[0]!==edge[1]&&edge.every(x=>ids.includes(x)),'invalid interaction edge');
    const [a,b]=edge;if(network.within_group&&groups.get(a)!==groups.get(b))continue;
    if(!used.has(a)&&!used.has(b)){used.add(a);used.add(b);pairs.push({interaction_id:`r${round}_i${pairs.length+1}`,actors:[a,b]});}
  }
  requireThat(pairs.length>0,'network has no schedulable interactions');
  return {round,pairs,idle:ids.filter(id=>!used.has(id))};
}

export function initialStates(pop:any[]){return Object.fromEntries(pop.map(p=>[p.alias,{agent_id:p.alias,P:freeze(clone(p.initial.P)),M:freeze(clone(p.initial.M0)),X:clone(p.initial.X0)}]));}
export function observation(agent:string,id:string,round:number,version:number,content:any){return {observation_id:id,recipient:agent,visibility:[agent],simulation_time:{round},world_version:version,content};}
export function updatedX(before:any,obs:any){return {...before,version:before.version+1,observation_ids:[...before.observation_ids,obs.observation_id],
  current_round:obs.simulation_time.round,last_observation:clone(obs),visible_score:obs.content.score??before.visible_score,
  ...(obs.content.kind==='interaction_feedback'?{last_partner_action:obs.content.partner_action,last_interaction:clone(obs.content)}:{})};}
export function deliverTo(states:any,episodes:any,obs:any,log:EventLog){
  const state=states[obs.recipient];
  if(!state||!obs.visibility.includes(obs.recipient)){log.add('observation_blocked',{observation_id:obs.observation_id,reason:'recipient_visibility'});return false;}
  requireThat(!state.X.observation_ids.includes(obs.observation_id),'duplicate observation');
  log.add('observation_delivered',obs);episodes[obs.recipient].push(clone(obs));
  const before=clone(state.X);state.X=updatedX(before,obs);
  log.add('state_transition',{agent_id:obs.recipient,writer:'StateUpdater',rule:'delivered_observation_v1',cause_refs:[obs.observation_id],before,after:state.X});return true;
}
export function contextFor(p:any,state:any,episodes:any[],c:any,condition:any,seed:number,round:number,attempt:number){
  const emptyP=Object.fromEntries(Array.from({length:8},(_,i)=>['L'+(i+1),{}]));
  return freeze({agent_id:p.alias,round,attempt,decision_seed:parseInt(digest({seed,round,alias:p.alias,attempt}).slice(0,8),16),
    P:condition.persona_enabled?clone(p.view):emptyP,M:condition.persona_enabled?clone(state.M):{entries:[],status:'personal_initialization_disabled'},
    X:clone(state.X),Observation:clone(state.X.last_observation),episodes:c.observation.memory_window?clone(episodes.slice(-c.observation.memory_window)):[],
    capability:condition.professional_enabled?{module:'public_rules_reference_v1',version:'1.0.0',scope:'engineering_reference_not_validated_professional_layer',
      source:'public_scenario_rules',norm:c.scenario.norm,payoffs:{match:c.scenario.match_reward,mismatch:c.scenario.mismatch_reward}}:null});
}

export class SharedGM {
  world:any;private ids:string[];
  constructor(private c:any,pop:any[]){this.ids=pop.map(p=>p.alias);this.world={version:0,round:1,scores:Object.fromEntries(this.ids.map(id=>[id,0])),interactions:[]};}
  inspect(req:any,pair:any,actor:string){
    if(req.actor_id!==actor||!pair.actors.includes(actor))return 'actor_not_authorized';
    if(req.interaction_id!==pair.interaction_id)return 'wrong_interaction';
    if(req.world_version!==this.world.version)return 'stale_world_version';
    if(req.action_type==='SET_PAYOFF')return 'permission_denied';
    if(!['A','B'].includes(req.action_type))return 'invalid_action';
    if(Object.keys(req.arguments??{}).length)return 'unexpected_arguments';
    return null;
  }
  commit(scheduled:any,requests:any[]){
    const before=clone(this.world),byActor=new Map(requests.map(r=>[r.actor_id,r]));
    requireThat(requests.length===scheduled.pairs.length*2&&byActor.size===requests.length,'incomplete/duplicate round requests');
    requireThat(new Set(requests.map(r=>r.request_id)).size===requests.length,'duplicate request_id');
    requireThat(scheduled.round===before.round,'wrong scheduled round');
    const interactions=[];const scores={...before.scores};
    for(const pair of scheduled.pairs){
      const [a,b]=pair.actors,ra:any=byActor.get(a),rb:any=byActor.get(b);
      requireThat(ra&&rb&&!this.inspect(ra,pair,a)&&!this.inspect(rb,pair,b),'invalid staged action');
      const reward=ra.action_type===rb.action_type?this.c.scenario.match_reward:this.c.scenario.mismatch_reward;
      scores[a]+=reward;scores[b]+=reward;
      interactions.push({...pair,round:before.round,choices:{[a]:ra.action_type,[b]:rb.action_type},reward,
        norm_violation:{[a]:ra.action_type==='B',[b]:rb.action_type==='B'},request_ids:[ra.request_id,rb.request_id]});
    }
    this.world={version:before.version+1,round:before.round+1,scores,interactions:[...before.interactions,...interactions]};
    return {before,after:clone(this.world),interactions};
  }
}

export async function executeRun(pop:any[],c:any,condition:any,seed:number,backend:ExperimentBackend,log=new EventLog()){
  const states=initialStates(pop),episodes:any=Object.fromEntries(pop.map(p=>[p.alias,[]])),gm=new SharedGM(c,pop);
  const original=digest(pop.map(p=>[p.initial.P,p.initial.M0]));let pending:any[]=[];
  log.add('run_started',{seed,condition,backend:backend.kind,agents:pop.map(p=>p.alias),initial_world:gm.world});
  try{
    for(let round=1;round<=c.rounds;round++){
      const scheduled=schedule(pop,c.network,seed,round);log.add('round_scheduled',scheduled);pending=[];
      for(const p of pop){
        const pair=scheduled.pairs.find((x:any)=>x.actors.includes(p.alias));
        deliverTo(states,episodes,observation(p.alias,`${round}:${p.alias}:open`,round,gm.world.version,
          {kind:'round_open',round,interaction:pair??null,score:gm.world.scores[p.alias],norm:c.scenario.norm,
            action_space:['A','B','SET_PAYOFF'],payoffs:{match:c.scenario.match_reward,mismatch:c.scenario.mismatch_reward}}),log);
      }
      // Barrier: collect ALL decisions before any accepted action is committed or revealed.
      for(const pair of scheduled.pairs)for(const actor of pair.actors){
        const p=pop.find(x=>x.alias===actor);let accepted=false;
        for(let attempt=1;attempt<=c.max_attempts&&!accepted;attempt++){
          const context=contextFor(p,states[actor],episodes[actor],c,condition,seed,round,attempt),messages=makeMessages(context);
          log.add('decision_input',{agent_id:actor,interaction_id:pair.interaction_id,round,attempt,context,messages});
          const answer=await backend.decide(context,messages);log.add('decision_output',{agent_id:actor,interaction_id:pair.interaction_id,round,attempt,...answer});
          let parsed:any;try{parsed=JSON.parse(answer.raw);}catch{throw new Error('decision_output_invalid_json');}
          requireThat(parsed&&Object.keys(parsed).length===1&&typeof parsed.action==='string','decision_output_invalid_schema');
          const req={request_id:`${round}:${actor}:${attempt}`,actor_id:actor,interaction_id:pair.interaction_id,round,attempt,
            action_type:parsed.action,arguments:{},basis_state_version:states[actor].X.version,world_version:gm.world.version};
          log.add('action_request',req);const rejection=gm.inspect(req,pair,actor);
          if(rejection){
            log.add('action_result',{request_id:req.request_id,agent_id:actor,interaction_id:pair.interaction_id,round,accepted:false,rejection_reason:rejection,
              executed_action:null,world_version_before:gm.world.version,world_version_after:gm.world.version});
            deliverTo(states,episodes,observation(actor,req.request_id+':rejected',round,gm.world.version,
              {kind:'rejection',request_id:req.request_id,reason:rejection,score:gm.world.scores[actor]}),log);
          }else {pending.push(req);accepted=true;}
        }
        requireThat(accepted,'round_attempt_budget_exhausted');
      }
      log.add('collection_barrier',{round,request_ids:pending.map(r=>r.request_id)});
      const transaction=gm.commit(scheduled,pending);log.add('world_transition',transaction);
      for(const req of pending){
        const interaction=transaction.interactions.find(i=>i.interaction_id===req.interaction_id)!;
        log.add('action_result',{request_id:req.request_id,agent_id:req.actor_id,interaction_id:req.interaction_id,round,accepted:true,rejection_reason:null,
          executed_action:req.action_type,reward:interaction.reward,world_version_before:transaction.before.version,world_version_after:transaction.after.version});
      }
      pending=[];
      for(const p of pop){
        const interaction=transaction.interactions.find(i=>i.actors.includes(p.alias));
        const partner=interaction?.actors.find((id:string)=>id!==p.alias);
        const content:any=interaction?{kind:'interaction_feedback',interaction_id:interaction.interaction_id,own_action:interaction.choices[p.alias],
          partner_id:partner,partner_action:interaction.choices[partner],reward:interaction.reward,norm_violation:interaction.norm_violation[p.alias],
          feedback:interaction.norm_violation[p.alias]?'Public rule reminder: A was expected; B was allowed.':'Public rule acknowledges A.'}:{kind:'idle_feedback'};
        content.score=gm.world.scores[p.alias];
        if(c.observation.scope==='public_completed_round')content.public_results=clone(transaction.interactions);
        deliverTo(states,episodes,observation(p.alias,`${round}:${p.alias}:feedback`,round,gm.world.version,content),log);
      }
      requireThat(digest(pop.map(p=>[states[p.alias].P,states[p.alias].M]))===original,'P_M_mutation');
    }
    log.add('run_completed',{world:gm.world,state_hashes:Object.fromEntries(pop.map(p=>[p.alias,digest(states[p.alias])]))});
    return {states,episodes,world:clone(gm.world),events:log.events};
  }catch(error){
    for(const req of pending)log.add('action_result',{request_id:req.request_id,agent_id:req.actor_id,interaction_id:req.interaction_id,round:req.round,
      accepted:false,rejection_reason:'round_aborted_before_commit',executed_action:null,world_version_before:gm.world.version,world_version_after:gm.world.version});
    log.add('run_failed',{message:error instanceof Error?error.message:'unknown_error',world:gm.world,states});throw error;
  }
}
