"""Integrate reviewed-by-author semantic proposals, producing S3 work for independent S4."""
import pathlib,json,hashlib,math,difflib,copy,datetime
ROOT=pathlib.Path(__file__).resolve().parents[1];RUN=ROOT/'deliveries/run-20261117-z073-rebuild-001'
def read(p):return json.loads(p.read_text())
def write(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def jsonl(p,rs):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rs))
def ref(p):return {'path':str(p.relative_to(RUN)) if p.is_relative_to(RUN) else str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
def rows(p):return [json.loads(l) for l in p.open()]
def tokens(t):return math.ceil(sum(1 if ord(c)>127 else .25 for c in t))
original=rows(RUN/'02_working_inputs/ziwei_073.jsonl');clean=rows(RUN/'03_cleaned/ziwei/ziwei_073.jsonl');units=rows(RUN/'input_units/ziwei_073.jsonl');s1=read(RUN/'reports/ziwei_073/S1_text_treatment.json');s2=read(RUN/'reports/ziwei_073/S2_clean_review.json');assert s2['status']=='passed'
for frozen in s2['frozen_artifacts']:assert ref(RUN/frozen['path'])['sha256']==frozen['sha256']
changes={u['unit_id']:[] for u in units}
for c in s1['changes']:changes[c['unit_id']].append(c)
cmaps=[]
for row,cr,u in zip(original,clean,units):
 t=row['chunk_text'];out='';mapping=[];cursor=0
 for c in sorted(changes[u['unit_id']],key=lambda x:x['span']):
  a,b=c['span'];assert t[a:b]==c['before'] and a>=cursor
  out+=t[cursor:a];mapping.extend((x,x+1,None) for x in range(cursor,a))
  before,after=c['before'],c['after'];out+=after
  for op,i,j,k,l in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes():
   if op=='equal':mapping.extend((a+i+q,a+i+q+1,c['change_id']) for q in range(l-k))
   elif op in ['insert','replace']:mapping.extend((a+i,a+j,c['change_id']) for _ in range(l-k))
  cursor=b
 out+=t[cursor:];mapping.extend((x,x+1,None) for x in range(cursor,len(t)))
 assert out==cr['chunk_text'] and len(mapping)==len(out);cmaps.append(mapping)
proposals=[]
for f in ['segmentation_G001_G050.json','segmentation_G051_G100.json','segmentation_G101_G148.json']:proposals.extend(read(ROOT/'preparation/s3_batches'/f)['records'])
proposals.sort(key=lambda r:r['group_id']);assert [r['group_id'] for r in proposals]==[f'G{i:03d}' for i in range(1,149)]
chunks=[];lineage=[];segment_refs={u['unit_id']:[] for u in units};diagnostics=[]
for prop in proposals:
 g=read(ROOT/'preparation/s3_groups'/(prop['group_id']+'.json'));assert prop['text_sha256']==g['text_sha256'] and not prop.get('blocking_issues');assert not prop.get('unresolved') or prop.get('unresolved_classification')=='inherited_nonblocking_source_findings';cursor=0
 for part in prop['segments']:
  a,b=part['span'];assert a==cursor and a<b<=len(g['text']);cursor=b;text=g['text'][a:b];n=tokens(text);assert n<=850 and n==part['estimated_tokens']
  cid=f'dots_all_20261013_ziwei_073_c{len(chunks)+1:03d}';segments=[];pages=set();oldids=[];cids=set()
  for um in g['unit_mappings']:
   ua,ub=um['group_span'];lo,hi=max(a,ua),min(b,ub)
   if lo>=hi:continue
   i=um['ordinal']-1;u=units[i];assert um['cleaned_span']==[0,len(clean[i]['chunk_text'])];clo,chi=lo-ua,hi-ua;maps=cmaps[i][clo:chi];nonzero=[m for m in maps if m[0]<m[1]]
   # A separator-only slice has no source characters; its provenance is the join record.
   if not nonzero:continue
   oa,ob=min(m[0] for m in nonzero),max(m[1] for m in nonzero);editids=sorted({m[2] for m in maps if m[2]})
   seg={'unit_id':u['unit_id'],'source_locator':u['source_locator'],'span':[oa,ob],'original_excerpt':original[i]['chunk_text'][oa:ob],'cleaned_span':[clo,chi],'group_span':[lo,hi],'new_chunk_span':[lo-a,hi-a],'cleaning_change_ids':editids};segments.append(seg);segment_refs[u['unit_id']].append({'new_chunk_id':cid,'original_span':[oa,ob],'cleaned_span':[clo,chi]});pages.update(original[i]['page_range']);oldids.append(original[i]['chunk_id']);cids.update(editids)
  assert segments
  chunks.append({'chunk_id':cid,'source_file':'紫云论斗数曜赋性第二册  390页','page_range':sorted(pages),'chunk_text':text,'chunk_index':len(chunks)+1})
  lineage.append({'source_id':'ziwei_073','new_chunk_id':cid,'input_path':s1['input_files'][0]['path'],'input_sha256':s1['input_files'][0]['sha256'],'segments':segments,'old_chunk_ids':oldids,'cleaning_change_ids':sorted(cids),'overlap_segments':[],'context_header':None,'group_id':g['group_id'],'group_span':[a,b],'source_overlap_reconstruction':g['joins'],'page_basis':'inherited_unverified_union_of_contributing_input_units'})
  diagnostics.append({'chunk_id':cid,'group_id':g['group_id'],'estimated_tokens':n,'reason':part['reason'],'short_semantic_exception':part.get('short_semantic_exception') if n<100 else None})
 assert cursor==len(g['text'])
coverage=[]
for i,u in enumerate(units):
 text=original[i]['chunk_text'];maps=segment_refs[u['unit_id']];h=next(c for c in changes[u['unit_id']] if c['change_id'].startswith('S1_HEADER_'));he=h['span'][1];bounds={0,len(text),he}
 for m in maps:bounds.update(m['original_span'])
 for c in changes[u['unit_id']]:bounds.update(c['span'])
 bounds=sorted(bounds);ds=[]
 for a,b in zip(bounds,bounds[1:]):
  if a==b:continue
  if b<=he:ds.append({'span':[a,b],'action':'filtered','new_chunk_ids':[],'reason':'Inherited unverified context wrapper separated at S1; preserved verbatim in metadata.','cleaning_change_ids':[h['change_id']],'metadata_path':'metadata/ziwei_073_context_headers.jsonl'});continue
  ids=sorted({m['new_chunk_id'] for m in maps if m['original_span'][0]<b and m['original_span'][1]>a});edits=[c['change_id'] for c in changes[u['unit_id']] if (c['span'][0]<b and c['span'][1]>a) or (c['span'][0]==c['span'][1] and (a<=c['span'][0]<b or c['span'][0]==b==len(text)))]
  if not ids:
   assert edits,'Unmapped original body span';nearest=min(maps,key=lambda m:min(abs(m['original_span'][0]-b),abs(m['original_span'][1]-a)));ids=[nearest['new_chunk_id']]
  ds.append({'span':[a,b],'action':'split' if len(ids)>1 else 'kept','new_chunk_ids':ids,'reason':'Original source body retained via exact cleaned mapping; documented S1 correction where applicable.','cleaning_change_ids':edits})
 assert ds[0]['span'][0]==0 and ds[-1]['span'][1]==len(text) and all(x['span'][1]==y['span'][0] for x,y in zip(ds,ds[1:]));coverage.append({'source_id':'ziwei_073','unit_id':u['unit_id'],'source_locator':u['source_locator'],'text_sha256':u['text_sha256'],'dispositions':ds})
assert {c['change_id'] for cs in changes.values() for c in cs} <= {cid for cv in coverage for ds in cv['dispositions'] for cid in ds['cleaning_change_ids']}
for path,data in [('04_chunked/ziwei/ziwei_073.jsonl',chunks),('lineage/ziwei_073.jsonl',lineage),('coverage/ziwei_073.jsonl',coverage)]:jsonl(RUN/path,data)
# Preserve historical input continuity before the contract-authorized S3 update.
cont=RUN/'continuity/ziwei_073.json';hist=RUN/'history/S0/continuity_ziwei_073.json';hist.parent.mkdir(parents=True,exist_ok=True)
if not hist.exists():hist.write_bytes(cont.read_bytes())
old=read(hist);pairrows=[]
for f in ['continuity_001_125.json','continuity_126_250.json','continuity_251_379.json']:pairrows.extend(read(ROOT/'preparation/s3_batches'/f)['records'])
write(cont,{'source_id':'ziwei_073','stage':'S3','status':'needs_S4_review','prior_S0_continuity':ref(hist),'cleaned_input':ref(RUN/'03_cleaned/ziwei/ziwei_073.jsonl'),'pairs':pairrows,'no_book_reconstruction_claim':True})
report=read(ROOT/'frozen_package/templates/stage_report.json');now=datetime.datetime.now(datetime.timezone.utc).isoformat();report.update(step='S3_chunking',status='needs_review',source_id='ziwei_073',run_id=RUN.name,route_id='B05',operation='semantic_rechunk_evidence_supported_groups',outcome='resegmented',rule_version='dots-all-sources-2026-10-13-v4',instruction_revision=4,is_template=False,executor='dot',model='unknown',started_at=now,completed_at=now,input_count=380,output_count=len(chunks),attempt=3,old_labels_exposed=False,input_files=[ref(RUN/'03_cleaned/ziwei/ziwei_073.jsonl'),ref(RUN/'input_units/ziwei_073.jsonl'),ref(RUN/'reports/ziwei_073/S1_text_treatment.json'),ref(RUN/'reports/ziwei_073/S2_clean_review.json'),ref(RUN/'02_working_inputs/ziwei_073.jsonl'),ref(ROOT/'frozen_package/rules/ChunkRecord.schema.json'),ref(hist),ref(RUN/'metadata/ziwei_073_context_headers.jsonl'),ref(ROOT/'preparation/S3_GROUP_FILES_MANIFEST_V1.json'),ref(ROOT/'preparation/integrate_s3.py')]+[ref(ROOT/'preparation/s3_batches'/f) for f in ['segmentation_G001_G050.json','segmentation_G051_G100.json','segmentation_G101_G148.json','continuity_001_125.json','continuity_126_250.json','continuity_251_379.json']],output_files=[ref(RUN/p) for p in ['04_chunked/ziwei/ziwei_073.jsonl','lineage/ziwei_073.jsonl','coverage/ziwei_073.jsonl','continuity/ziwei_073.json']],blocking_issues=[],nonblocking_issues=['Unknown/disconnected source adjacency remains isolated; inherited incomplete source fragments and chart disorder are not repaired by inference.','Duplicate/overlap source spans share output characters with explicit multi-source lineage; this does not establish original-book completeness.','All S1 nonbody prefix spans retained in metadata and marked filtered in full coverage.'],sample_checks=diagnostics,review_findings=[{'group_id':p['group_id'],'inherited_source_findings':p.get('unresolved',[])} for p in proposals if p.get('unresolved')],execution_policy='user_authorized_rebuild_after_workspace_loss',decision='execute',human_review_status='not_performed',unresolved=[])
write(RUN/'reports/ziwei_073/S3_chunking.json',report);status=read(RUN/'status.json');status.update(S3='needs_review',S4='not_started');write(RUN/'status.json',status);print(json.dumps({'chunks':len(chunks),'units_covered':len(coverage),'max_tokens':max(x['estimated_tokens'] for x in diagnostics)}))
