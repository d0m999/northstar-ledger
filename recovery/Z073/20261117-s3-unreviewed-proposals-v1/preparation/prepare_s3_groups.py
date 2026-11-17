"""Build provisional reconstruction groups from explicit pairwise judgments, not final chunks."""
import pathlib,json,hashlib,math
ROOT=pathlib.Path(__file__).resolve().parents[1];RUN=ROOT/'deliveries/run-20261117-z073-rebuild-001'
P=RUN/'03_cleaned/ziwei/ziwei_073.jsonl';rows=[json.loads(l) for l in P.open()];sha=hashlib.sha256(P.read_bytes()).hexdigest()
assert json.loads((RUN/'status.json').read_text())['S2']=='passed'
pairs=[]
for f in ['continuity_001_125.json','continuity_126_250.json','continuity_251_379.json']:
 d=json.loads((ROOT/'preparation/s3_batches'/f).read_text());pairs.extend(d['records'])
pairs.sort(key=lambda x:x['left_ordinal']);assert [x['left_ordinal'] for x in pairs]==list(range(1,380))
groups=[]
def new(i):return {'group_id':f'G{len(groups)+1:03d}','unit_ordinals':[i],'text':rows[i-1]['chunk_text'],'unit_mappings':[{'ordinal':i,'unit_id':f'ziwei_073_u{i:04d}','cleaned_span':[0,len(rows[i-1]['chunk_text'])],'group_span':[0,len(rows[i-1]['chunk_text'])]}],'joins':[],'separator_spans':[]}
g=new(1)
for pair in pairs:
 i=pair['left_ordinal'];j=pair['right_ordinal'];assert j==i+1
 a,b=rows[i-1]['chunk_text'],rows[j-1]['chunk_text'];assert g['text'].endswith(a)
 if pair['status']!='confirmed':groups.append(g);g=new(j);continue
 k=pair['overlap_chars'];assert isinstance(k,int) and 0<=k<=min(len(a),len(b))
 if k:assert a[-k:]==b[:k];sep=''
 else:
  assert pair['relation']=='fragment_continuation';sep=pair.get('join_separator','');assert sep in ['', '\n','\n\n']
 start=len(g['text'])-k+len(sep)
 if sep:g['separator_spans'].append([len(g['text']),len(g['text'])+len(sep)])
 g['text']+=sep+b[k:];g['unit_ordinals'].append(j);g['unit_mappings'].append({'ordinal':j,'unit_id':f'ziwei_073_u{j:04d}','cleaned_span':[0,len(b)],'group_span':[start,start+len(b)]});g['joins'].append(pair)
groups.append(g)
for g in groups:
 g['estimated_tokens']=math.ceil(sum(1 if ord(c)>127 else .25 for c in g['text']));g['text_sha256']=hashlib.sha256(g['text'].encode()).hexdigest()
 for m in g['unit_mappings']:
  a,b=m['group_span'];assert g['text'][a:b]==rows[m['ordinal']-1]['chunk_text']
out=ROOT/'preparation/s3_groups';out.mkdir(exist_ok=True)
for g in groups:(out/(g['group_id']+'.json')).write_text(json.dumps(g,ensure_ascii=False,indent=2)+'\n')
(ROOT/'preparation/s3_groups_index.json').write_text(json.dumps({'status':'provisional_not_S4_approved','source_sha256':sha,'groups':[{'group_id':g['group_id'],'unit_ordinals':g['unit_ordinals'],'estimated_tokens':g['estimated_tokens'],'text_sha256':g['text_sha256']} for g in groups]},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'groups':len(groups),'confirmed_pairs':sum(p['status']=='confirmed' for p in pairs),'max_group_tokens':max(g['estimated_tokens'] for g in groups)}))
