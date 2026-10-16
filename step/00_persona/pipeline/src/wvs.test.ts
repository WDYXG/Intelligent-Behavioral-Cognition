import {test,expect} from 'bun:test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parse} from 'csv-parse/sync';
import {convert,loadBundle,normalize,validator} from './wvs.ts';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const bundle=await loadBundle(root), v=await validator(root);
const meta={recordId:'TEST_ONLY_001',ordinal:1,hash:'a'.repeat(64),artifact:'synthetic_test_only.csv'};
const base={Q260:'2',Q261:'1980',Q262:'38',Q263:'1',Q270:'3',Q271:'1',Q272:'2870',Q273:'1',Q274:'1',Q275:'6',Q279:'1',Q281:'1',Q284:'2',Q285:'2',Q286:'2',Q287:'3',Q288:'5'};
const run=(patch:Record<string,string>={})=>convert({...base,...patch},meta,bundle);
const field=(r:any,id:string)=>Object.values(r.snapshot.fields).find((f:any)=>f.mapping_rule_id===id) as any;
for(const m of bundle.approved.value_map) test(`approved category ${m.source_field}=${m.raw_value}`,()=>{
  const rule=bundle.pack.field_rules.find((r:any)=>r.source_field===m.source_field);
  expect(normalize(String(m.raw_value),rule,bundle.pack).value).toEqual(m.standardized_value);
});
test('valid specimen satisfies all four-object references and schemas',()=>v.check(run(),bundle,meta,base));
test('Q263 excluded from snapshot, evidence and persona',()=>{
  const r=run(); expect(Object.values(r.snapshot.fields).some((f:any)=>f.source_field==='Q263')).toBe(false);
  expect(r.evidence.some((e:any)=>e.normalization.mapping_rule_id==='MAP_Q263')).toBe(false);
  expect(r.evidence.length).toBe(17);
});
test('7 children is a lower bound and zero children is not missing',()=>{
  expect(run({Q274:'7'}).persona.slow_attributes['family_structure.children'].value).toEqual({exact_count:null,minimum_count:7});
  expect(run({Q274:'0'}).persona.slow_attributes['family_structure.children'].value).toEqual({exact_count:0,minimum_count:0});
  expect(field(run({Q274:'0'}),'MAP_Q274_TOPCODED').normalized_value).toBe(false);
});
test('negative valid Q284 retained; missing -2 and -3 stay distinct',()=>{
  expect(field(run({Q284:'-5'}),'MAP_Q284').normalized_value).toBe('other_or_none_of_above');
  expect(field(run({Q284:'-5'}),'MAP_Q284').raw_missing_code).toBe(null);
  for(const code of ['-2','-3']) {const f=field(run({Q284:code}),'MAP_Q284');expect(f.normalized_value).toBe(null);expect(f.raw_missing_code).toBe(Number(code));}
  expect(field(run({Q284:'-1'}),'MAP_Q284').raw_missing_code).toBe(null);
});
test('unknown, empty and fractional values never become plausible zeros',()=>{
  for(const val of ['','abc','99','1.5']) expect(field(run({Q285:val}),'MAP_Q285').normalized_value).toBe(null);
  expect(run().persona.slow_attributes['economic_position.household_mainly_depends_on_respondent_income'].value).toBe(false);
});
test('birth year observed range is not treated as universal limit',()=>{
  expect(field(run({Q261:'1940'}),'MAP_Q261').normalized_value).toBe(1940);
  expect(field(run({Q261:'1940'}),'MAP_Q261').notes).toContain('outside_previously_observed_range');
});
for(const employment of ['1','2','3']) test(`current job branch ${employment}`,()=>{
  const p=run({Q279:employment}).persona;
  expect(p.fast_state['employment.current_job.occupational_group'].value).toBe('professional_or_technical');
  expect(p.slow_attributes['work_history.last_known_job.occupational_group']).toBeUndefined();
});
for(const employment of ['4','5','6','7']) test(`last job branch ${employment}`,()=>{
  const p=run({Q279:employment}).persona;
  expect(p.slow_attributes['work_history.last_known_job.occupational_group'].value).toBe('professional_or_technical');
  expect(p.fast_state['employment.current_job.occupational_group']).toBeUndefined();
});
for(const employment of ['8','-2']) test(`unknown job context ${employment}`,()=>{
  const p=run({Q279:employment}).persona;
  expect(p.fast_state['employment.current_job.sector']).toBeUndefined();
  expect(p.slow_attributes['work_history.last_known_job.sector']).toBeUndefined();
});
test('occupation zero does not invent a job',()=>expect(run({Q281:'0'}).persona.fast_state['employment.current_job.occupational_group']).toBeUndefined());
test('Q286 remains 2017 reference; age remains snapshot only',()=>{
  const r=run(); expect(field(r,'MAP_Q286').observation.reference_period).toBe('2017');
  expect(r.review.find((x:any)=>x.source_field==='Q286').persona_path).toBe(null);
  expect(r.review.find((x:any)=>x.source_field==='Q262').persona_path).toBe(null);
});
test('children and employment-derived paths reference their supporting evidence',()=>{
  const p=run().persona;
  expect(p.slow_attributes['family_structure.children'].evidence_ids.length).toBe(2);
  expect(p.fast_state['employment.current_job.sector'].evidence_ids.length).toBe(2);
});
test('cross-person, fabricated value, extra target behavior and false approval rejected',()=>{
  for(const mutate of [(r:any)=>r.evidence[0].provenance.source_record_id='OTHER',(r:any)=>r.persona.stable_attributes.birth_year.value=1999,(r:any)=>r.persona.fast_state.target_behavior={value:'yes',evidence_ids:[r.evidence[0].evidence_id],observation_status:'self_report',update_policy:'stable'},(r:any)=>r.persona.projection_status='confirmed']) {
    const r=run();mutate(r);expect(()=>v.check(r,bundle,meta,base)).toThrow();
  }
});
test('parser supports quoted commas, multiline cells and mixed CRLF/LF, rejects wrong column count',()=>{
  const options={columns:true,record_delimiter:['\r\n','\n','\r']};
  expect(parse('a,b\r\n1,"x,y"\n2,"x\ny"\n',options)).toEqual([{a:'1',b:'x,y'},{a:'2',b:'x\ny'}]);
  expect(()=>parse('a,b\n1,2,3\n',options)).toThrow();
});
