import {test,expect} from 'bun:test';
import path from 'node:path';
import {readJSON,STEP,digest} from './source';
import {clone,EventLog} from './runtime';
import {validateConfig,loadPopulation} from './experiment_config';
import {CostLedger,makeBackend} from './experiment_backend';
import {executeRun,schedule,SharedGM,initialStates,observation,deliverTo} from './experiment_engine';
import {replayCheck,extractRows,filterRows,basicStatistics} from './experiment_evaluation';

const configPath=path.join(STEP,'03_experiment/configs/experiment_offline_v1.json');
const base=readJSON(configPath);
function fixtures(){return Array.from({length:4},(_,i)=>({alias:'p'+i,group:i<2?'g1':'g2',
  initial:{P:{L1:{private_marker:'private_person_'+i}},M0:{entries:[],consolidated:false},X0:{version:0,observation_ids:[],last_observation:null,visible_score:0}},
  view:{L1:{allowed_marker:'authorized_person_'+i}}}));}
function small(){const c=clone(base);c.rounds=3;c.repetitions=1;return c;}
async function offline(c=small(),cond=base.conditions[3]){const ledger=new CostLedger(c.budget);return executeRun(fixtures(),c,cond,21,makeBackend(c,ledger));}

test('existing twelve initializations load hash-verified without Projector or raw data',async()=>{
  const loaded=await loadPopulation(base,path.dirname(configPath));expect(loaded.population.length).toBe(12);
  expect(new Set(loaded.population.map(p=>p.alias)).size).toBe(12);
});
test('unsupported config/mechanisms and formal-study claims fail closed',()=>{
  validateConfig(base);
  for(const mutate of [(c:any)=>c.learning_enabled=true,(c:any)=>c.scenario.kernel='other',(c:any)=>c.purpose='formal',(c:any)=>c.mechanisms.x_update='guess']){
    const c=small();mutate(c);expect(()=>validateConfig(c)).toThrow();
  }
});
test('network supports ring, complete, explicit and group constraints',()=>{
  const pop=fixtures();expect(schedule(pop,{kind:'ring'},1,1).pairs.length).toBe(2);
  expect(schedule(pop,{kind:'complete',within_group:true},1,1).pairs.every((p:any)=>pop.find(x=>x.alias===p.actors[0])?.group===pop.find(x=>x.alias===p.actors[1])?.group)).toBe(true);
  expect(schedule(pop,{kind:'explicit',edges:[['p0','p2']]},1,1).idle).toEqual(['p1','p3']);
  expect(()=>schedule(pop,{kind:'explicit',edges:[['missing','p0']]},1,1)).toThrow();
});
test('autonomous simultaneous barrier, independent contexts, P/M frozen, visible feedback only',async()=>{
  const c=small(),result=await offline(c),pop=fixtures();
  expect(result.world.version).toBe(3);expect(result.world.interactions.length).toBe(6);
  for(const p of pop){expect(result.states[p.alias].P).toEqual(p.initial.P);expect(result.states[p.alias].M).toEqual(p.initial.M0);}
  for(const e of result.events.filter(e=>e.type==='decision_input')){
    const ctx=e.payload.context,id=ctx.agent_id;
    expect(ctx.P).toEqual(pop.find(p=>p.alias===id)!.view);
    expect(ctx.episodes.every((o:any)=>o.recipient===id)).toBe(true);
    expect(ctx.episodes.filter((o:any)=>o.content.kind==='interaction_feedback').every((o:any)=>o.simulation_time.round<ctx.round)).toBe(true);
    expect(ctx.X.observation_ids.every((s:string)=>s.includes(id))).toBe(true);
    expect(JSON.stringify(ctx).includes('private_person_')).toBe(false);
  }
  for(let round=1;round<=c.rounds;round++){
    const barrier=result.events.find(e=>e.type==='collection_barrier'&&e.payload.round===round)!;
    const inputs=result.events.filter(e=>e.type==='decision_input'&&e.payload.round===round);
    expect(inputs.every(e=>e.seq<barrier.seq)).toBe(true);
    const feedback=result.events.filter(e=>e.type==='observation_delivered'&&e.payload.simulation_time.round===round&&e.payload.content.kind==='interaction_feedback');
    expect(feedback.every(e=>e.seq>barrier.seq)).toBe(true);
  }
  expect(result.events.filter(e=>e.type==='action_result'&&!e.payload.accepted).length).toBe(4);
});
test('four condition switches keep public X and World rules while toggling only allowed views',async()=>{
  const c=small();const results=[];
  for(const condition of base.conditions){const result=await offline(c,condition);results.push(result);
    const ctx=result.events.find(e=>e.type==='decision_input')!.payload.context;
    expect(Boolean(ctx.capability)).toBe(condition.professional_enabled);
    expect(Object.values(ctx.P).some((l:any)=>Object.keys(l).length)).toBe(condition.persona_enabled);
    expect(ctx.X.version).toBeGreaterThan(0);
  }
  expect(results.map(r=>digest(r.world)).every(h=>h===digest(results[0].world))).toBe(true);
});
test('repetitions reset all state and offline response replay verifies exact trajectory',async()=>{
  const a=await offline(),b=await offline();expect(digest(a)).toBe(digest(b));
  const checked=await replayCheck(a.events,fixtures(),small(),base.conditions[3],21);expect(checked.report.status).toBe('passed');
  const bad=clone(a.events);bad.find((e:any)=>e.type==='decision_input').payload.context.P={leak:'other_agent'};
  let prev:any=null;for(const e of bad){delete e.hash;e.previous_hash=prev;e.hash=digest(e);prev=e.hash;}
  await expect(replayCheck(bad,fixtures(),small(),base.conditions[3],21)).rejects.toThrow();
});
test('GM rejects permissions, actor spoofing, stale World and malformed actions',()=>{
  const gm=new SharedGM(small(),fixtures()),pair={interaction_id:'x',actors:['p0','p1']};
  const r={actor_id:'p0',interaction_id:'x',action_type:'A',world_version:0,arguments:{}};
  expect(gm.inspect({...r,action_type:'B'},pair,'p0')).toBeNull();
  expect(gm.inspect({...r,action_type:'SET_PAYOFF'},pair,'p0')).toBe('permission_denied');
  expect(gm.inspect({...r,world_version:1},pair,'p0')).toBe('stale_world_version');
  expect(gm.inspect({...r,actor_id:'p1'},pair,'p0')).toBe('actor_not_authorized');
  expect(gm.inspect({...r,action_type:'bad'},pair,'p0')).toBe('invalid_action');
  expect(gm.world.version).toBe(0);
});
test('bad visibility blocked and duplicate observation fails without extra X update',()=>{
  const states=initialStates(fixtures()),episodes:any={p0:[],p1:[],p2:[],p3:[]},log=new EventLog();
  const obs=observation('p0','one',1,0,{kind:'round_open'});obs.visibility=['p1'];
  expect(deliverTo(states,episodes,obs,log)).toBe(false);expect(states.p0.X.version).toBe(0);
  obs.visibility=['p0'];deliverTo(states,episodes,obs,log);
  expect(()=>deliverTo(states,episodes,obs,log)).toThrow();expect(states.p0.X.version).toBe(1);expect(states.p1.X.version).toBe(0);
});
test('malformed decision and budget exhaustion explicitly fail before any partial World commit',async()=>{
  for(const backend of [{kind:'bad',async decide(){return {raw:'not json',metadata:{}};}},makeBackend(small(),new CostLedger({max_calls:1,max_tokens:100,max_usd:0}))]){
    const log=new EventLog();await expect(executeRun(fixtures(),small(),base.conditions[3],21,backend,log)).rejects.toThrow();
    expect(log.events.at(-1)?.type).toBe('run_failed');expect(log.events.at(-1)?.payload.world.version).toBe(0);
  }
});
test('public scope reveals only committed round, local scope excludes unrelated interactions',async()=>{
  const local=await offline();expect(local.events.filter(e=>e.type==='observation_delivered').every(e=>!e.payload.content.public_results)).toBe(true);
  const c=small();c.observation.scope='public_completed_round';const pub=await offline(c);
  for(const e of pub.events.filter(e=>e.type==='observation_delivered'&&e.payload.content.public_results))expect(e.payload.content.public_results.every((i:any)=>i.round===e.payload.simulation_time.round)).toBe(true);
});
test('offline Evaluation groups and filters agent, interaction, round, run, condition',async()=>{
  const r=await offline(),rows=extractRows(r.events,{run_id:'run',condition:{id:'D'},repetition:1,seed:21});
  expect(filterRows(rows.results,{agent_id:'p0',round:1,run:'run',condition:'D'}).length).toBe(2);
  expect(filterRows(rows.interactions,{interaction_id:'r1_i1'}).length).toBe(1);
  expect(basicStatistics(rows).executed).toBe(12);
});

function apiConfig(){const c=small();c.backend.kind='llm';c.backend.allow_remote_persona=true;
  Object.assign(c.backend.api,{input_usd_per_million:1,output_usd_per_million:2,max_input_tokens:32768});return c;}
const env={VS_LLM_ENDPOINT:'https://mock.invalid/chat/completions',VS_LLM_MODEL:'mock-model',VS_LLM_API_KEY:'MOCK_SECRET_NOT_REAL'};
test('API adapter uses same engine via mocked transport; metadata recorded, key never logged',async()=>{
  const c=apiConfig(),ledger=new CostLedger(c.budget);let calls=0;
  const backend=makeBackend(c,ledger,env,(async(_url:any,options:any)=>{calls++;
    const body=JSON.parse(options.body);expect(body.messages[1].content.includes('private_person_')).toBe(false);
    return new Response(JSON.stringify({model:'mock-model',choices:[{message:{content:'{"action":"B"}'}}],usage:{prompt_tokens:10,completion_tokens:6}}));}) as any);
  const result=await executeRun(fixtures(),c,base.conditions[3],21,backend);
  expect(calls).toBe(12);expect(result.world.version).toBe(3);expect(JSON.stringify(result).includes(env.VS_LLM_API_KEY)).toBe(false);
  expect(ledger.reported_tokens).toBe(192);
});
test('API startup and monetary/token/call budgets fail before network',async()=>{
  const c=apiConfig();expect(()=>makeBackend(c,new CostLedger(c.budget),{})).toThrow('environment incomplete');
  const bad=clone(c);bad.backend.api.input_usd_per_million=null;expect(()=>makeBackend(bad,new CostLedger(c.budget),env)).toThrow('pricing');
  let dispatched=false;const backend=makeBackend(c,new CostLedger({...c.budget,max_usd:0}),env,(async()=>{dispatched=true;throw new Error();}) as any);
  await expect(backend.decide({},[{role:'user',content:'hello'}])).rejects.toThrow('budget_cost_limit');expect(dispatched).toBe(false);
  const ledger=new CostLedger({max_calls:1,max_tokens:2,max_usd:1});expect(()=>ledger.reserve(3,0)).toThrow('token');ledger.reserve(1,0);expect(()=>ledger.reserve(0,0)).toThrow('call');
});
test('API HTTP failure keeps reservation, no implicit retry or offline fallback',async()=>{
  const c=apiConfig(),ledger=new CostLedger(c.budget);let calls=0;
  const b=makeBackend(c,ledger,env,(async()=>{calls++;return new Response('secret server error',{status:429});}) as any);
  await expect(b.decide({},[])).rejects.toThrow('API_HTTP_429');expect(calls).toBe(1);expect(ledger.reserved_usd).toBeGreaterThan(0);expect(ledger.unknown_usage_calls).toBe(1);
});
