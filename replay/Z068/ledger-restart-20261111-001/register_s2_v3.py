"""Register explicit completed S2 judgments without rewriting review schemas or source."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

REUSED = set(range(1, 6)) | set(range(93, 98)) | set(range(185, 190))
PASS_RESULTS = {'reused_prior_full_non_author_pass', 'pass_with_retained_uncertainties',
                'supported', 'supported_with_retained_uncertainties',
                'supported_with_retained_source_uncertainties', 'pass',
                'supported_with_nonblocking_source_findings', 'pass_with_retained_source_uncertainties',
                'reviewed_no_blocking_findings', 'reviewed_with_source_uncertainties'}


def require(ok, why):
    if not ok: raise ValueError(why)


def encode(x): return (json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
def sha(b): return hashlib.sha256(b).hexdigest()
def pin(p,b): return {'path':str(p),'bytes':len(b),'sha256':sha(b)}


def decode(b):
    def pairs(items):
        out={}
        for k,v in items:
            require(k not in out,'duplicate JSON key');out[k]=v
        return out
    def invalid(x): raise ValueError('nonfinite JSON')
    return json.loads(b.decode(),object_pairs_hook=pairs,parse_constant=invalid)


def checked(p):
    f=Path(p['path']);require(f.is_file() and not f.is_symlink(),'nonregular input')
    b=f.read_bytes();require(pin(f,b)==p,'input pin mismatch');return b


def unique_ids(values, expected, label):
    require(len(values)==len(set(values)) and set(values)==set(expected),label+' coverage mismatch')


def review_keys(r, stage_map=None):
    if 'reviewed_patch_ids' in r:
        return r['reviewed_patch_ids'],r['reviewed_uncertainty_ids'],r['three_substep_ids']
    if 'reviewed_patches' in r:
        patches=r['reviewed_patches'];issues=r['reviewed_uncertainties']
    else:
        patches=r['reviewed_patches_with_specific_support_or_finding'];issues=r['reviewed_uncertainties_with_specific_disposition']
    steps=r['three_substep_review']
    if all('substep_id' in x for x in steps):sids=[x['substep_id'] for x in steps]
    else:
        require(stage_map is not None,'missing actual stage map')
        labels=[x['substep'] for x in steps]
        unique_ids(labels,['table','punctuation','semantic'],'review stage')
        sids=[stage_map[k] for k in labels]
    return ([x['patch_id'] for x in patches],[x['issue_id'] for x in issues],sids)


def stage_maps(main, reports):
    require(len(reports)==3,'three actual subreports required')
    result={i:{} for i in range(1,277)};seen=[]
    for report in reports:
        stage=report['substep'];require(stage in ('table','punctuation','semantic'),'unknown report stage');seen.append(stage)
        rows=report['substeps'];require(len(rows)==276,'subreport coverage')
        for n,(row,process) in enumerate(zip(rows,main['process_records']),1):
            require(row['ordinal']==n==process['ordinal'] and row['unit_id']==process['unit_id'],'subreport unit')
            result[n][stage]=row['record']['substep_id']
    unique_ids(seen,['table','punctuation','semantic'],'subreport stages')
    for n,process in enumerate(main['process_records'],1):
        unique_ids(list(result[n].values()),[x['substep_id'] for x in process['author_record']['substeps']],'actual stage IDs')
    return result


def reuse_digests(r):
    if 'reused_review_binding' in r:
        b=r['reused_review_binding'];return [b[k] for k in ('prior_review_sha256','successor_review_sha256','current_version_bridge_sha256')]
    if 'prior_review_binding' in r:
        b=r['prior_review_binding'];return [b['review_sha256'],b['bridge_sha256']]
    return [r['prior_review_sha256'],r['current_version_bridge_sha256']]


def validate(main, selection, reviews, available_hashes, decision, actual_stage_maps=None):
    processes=main['process_records']; selected=selection['selected_units']
    require(len(processes)==len(selected)==len(reviews)==276,'full 276 required')
    require([p['ordinal'] for p in processes]==list(range(1,277)),'main order')
    require([r['ordinal'] for r in reviews]==list(range(1,277)),'review order')
    require([s['ordinal'] for s in selected]==list(range(1,277)),'selected order')
    counts={'units':276,'reused':0,'newly_read':0,'patches':0,'uncertainties':0}
    for process,s,r in zip(processes,selected,reviews):
        a=process['author_record'];n=process['ordinal']
        require(r['unit_id']==process['unit_id']==s['unit_id'],'unit mismatch')
        require(r['author_proposal_id']==a['proposal_id']==s['proposal_id'],'proposal mismatch')
        require(r['author_batch_sha256']==s['batch_sha256'],'author batch mismatch')
        require(r['original_text_sha256']==a['input_text_sha256']==process['input_text_sha256'],'original hash mismatch')
        require(r['clean_text_sha256']==a['result_text_sha256']==process['result_text_sha256'],'clean hash mismatch')
        pids,iids,sids=review_keys(r,actual_stage_maps.get(n) if actual_stage_maps is not None else None)
        unique_ids(pids,[p['patch_id'] for p in a['patches']],'patch')
        unique_ids(iids,[i['issue_id'] for i in a['unresolved']],'uncertainty')
        unique_ids(sids,[x['substep_id'] for x in a['substeps']],'substep')
        counts['patches']+=len(pids);counts['uncertainties']+=len(iids)
        if n in REUSED:
            require(r['review_origin']=='prior_full_non_author_review','wrong reuse assignment')
            require(all(h in available_hashes for h in reuse_digests(r)),'missing actual reuse evidence')
            require(r.get('new_body_read_claim',False) is False,'reuse falsely new read')
            counts['reused']+=1
        else:
            require(r['review_origin'] in ('new_full_non_author_review','new_full_non_author_read'),'missing new full review')
            if 'read_spans' in r:
                old,new=r['read_spans']['original'],r['read_spans']['actual_clean']
            elif 'original_read_span' in r:
                old=r['original_read_span']
                if 'actual_clean_read_span' in r:new=r['actual_clean_read_span']
                else:new=r['clean_read_span']
            else:
                old,new=r['read_span'],r['clean_read_span']
            require(old==[0,a['input_codepoints']] and new==[0,a['result_codepoints']],'incomplete read spans')
            counts['newly_read']+=1
        # Outcome semantics are supplied by the independent final decision, never inferred by this program.
        if decision['status']=='passed':
            require(r['result'] in PASS_RESULTS,'unsupported/nonpassing record result')
    require(counts=={'units':276,'reused':15,'newly_read':261,'patches':85,'uncertainties':527},'final scope counts mismatch')
    require(len(main['changes'])==85 and len(main['unresolved'])==527,'main full detail counts')
    require(len(main['filtered_units'])==276 and all(x['ordinal']==i and x['records']==[] for i,x in enumerate(main['filtered_units'],1)),'filtered coverage')
    require(decision['status'] in ('passed','failed'),'explicit terminal decision required')
    require(decision['complete_current_review'] is True,'final incomplete')
    require(decision['non_author_review_confirmed'] is True,'independent scope not confirmed')
    if decision['status']=='passed':require(decision['blocking_issues']==[],'passed with blockers')
    else:require(bool(decision['blocking_issues']),'failed without explicit finding')
    # No new finding may vanish merely because it is not an old author uncertainty.
    expected=[{'ordinal':r['ordinal'],'finding':f} for r in reviews for f in r['new_findings']]
    require(decision['new_findings_in_source_order']==expected,'new finding omission/change')
    return counts


def form(template,main,status,author_selection,reviews,decision,context):
    counts=validate(main,author_selection,reviews,{i['sha256'] for i in context['inputs']},decision,context['actual_stage_maps'])
    report=copy.deepcopy(template);passed=decision['status']=='passed'
    report.update(is_template=False,run_id=context['run_id'],source_id='ziwei_068',step='S2_clean_review',
                  route_id='B05',status=decision['status'],operation='review_cleaning_and_coverage',
                  outcome='text_checked' if passed else None,input_files=copy.deepcopy(context['inputs']),
                  output_files=[],executor=context['executor'],model='unknown',usage=None,
                  started_at=None,completed_at=None,input_count=276,output_count=276,
                  human_review_status='completed',old_labels_exposed=False,semantic_acceptance=False,
                  clean_review_passed=passed,decision='execute',execution_policy='resolve_history_first',
                  review_findings=copy.deepcopy(reviews),unresolved=copy.deepcopy(main['unresolved']),
                  changes=copy.deepcopy(main['changes']),filtered_units=copy.deepcopy(main['filtered_units']),
                  original_author_process_records=copy.deepcopy(main['process_records']),
                  final_review_decision=copy.deepcopy(decision),counts=counts,
                  selected_review_manifest=copy.deepcopy(context['selected_review_manifest']),
                  blocking_issues=copy.deepcopy(decision['blocking_issues']),
                  nonblocking_issues=copy.deepcopy(decision['nonblocking_issues']),
                  frozen_artifacts=copy.deepcopy(context['s1_artifacts']) if passed else [])
    new=copy.deepcopy(status);require(new['run_id']==context['run_id'] and new['is_template'] is False,'run identity')
    rows=[s for s in new['sources'] if s['source_id']=='ziwei_068'];require(len(rows)==1,'source status')
    steps=rows[0]['steps'];require(steps['S1_clean']['status']=='needs_review' and steps['S2_clean_review']['status']=='not_started','stage state')
    report_bytes=encode(report);rp=pin(context['report_target'],report_bytes)
    steps['S2_clean_review'].update(status=decision['status'],outcome='text_checked' if passed else None,
                                   decision='execute',output_files=[rp])
    if passed:steps['S1_clean']['status']='passed'
    return report_bytes,encode(new)


def load(plan):
    require(len({i['path'] for i in plan['inputs']})==len(plan['inputs']),'duplicate inputs')
    content={i['path']:checked(i) for i in plan['inputs']}
    def obj(key):
        require(plan[key] in plan['inputs'],'missing exact input');return decode(content[plan[key]['path']])
    selection=obj('review_selection');reviews=[]
    # Coordinator supplies explicit line mapping only after all three reviewer finals exist.
    for e in selection['selected_reviews']:
        require(e['file'] in plan['inputs'],'unbound review file')
        raw=content[e['file']['path']];lines=raw.splitlines();line=e['line_number']
        require(type(line) is int and 1<=line<=len(lines),'review line')
        r=decode(lines[line-1]);require(r['ordinal']==e['ordinal'] and r['unit_id']==e['unit_id'],'review selected identity');reviews.append(r)
    for item in plan['protected']+plan['s1_artifacts']:require(item in plan['inputs'],'unbound protected artifact')
    require(len(plan['s1_artifacts'])==5,'five S1 freeze pins required')
    require(all(i in plan['protected'] for i in plan['s1_artifacts']),'S1 protection missing')
    require(plan['status'] not in plan['protected'],'status cannot be protected old input')
    main=obj('main')
    require(len(plan['subreports'])==3 and all(x in plan['s1_artifacts'] for x in plan['subreports']),'actual subreports must be protected S1 inputs')
    maps=stage_maps(main,[decode(content[x['path']]) for x in plan['subreports']])
    context={'run_id':plan['run_id'],'inputs':plan['inputs'],'executor':plan['executor'],
             's1_artifacts':plan['s1_artifacts'],'report_target':plan['report_target'],
             'selected_review_manifest':selection,'actual_stage_maps':maps}
    return content,form(obj('template'),main,obj('status'),obj('author_selection'),reviews,obj('final_decision'),context)


def exclusive(path,raw):
    with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    require(path.read_bytes()==raw,'readback mismatch')


def publish_pair(root,report_bytes,old_status,new_status):
    require(root.is_dir() and not root.is_symlink(),'invalid run root')
    report=root/'reports/ziwei_068/S2_clean_review.json';status=root/'status.json'
    archive=root/'history/pre_s2_registration/status.json';journal=root/'S2_registration_journal.jsonl'
    temps=[report.with_name('S2_clean_review.json.s2.tmp'),root/'status.s2.tmp']
    for p in [report,archive,journal,*temps]:
        require(p.resolve().is_relative_to(root.resolve()) and not p.exists() and not p.is_symlink(),'fresh target/containment')
        require(p.parent.is_dir() and not p.parent.is_symlink(),'missing parent')
    require(not status.is_symlink() and status.read_bytes()==old_status,'old status guard')
    exclusive(journal,b'')
    def event(kind,path=None):
        with journal.open('ab') as f:f.write((json.dumps({'event':kind,'path':str(path) if path else None})+'\n').encode());f.flush();os.fsync(f.fileno())
    event('begin');exclusive(archive,old_status);event('archived',archive)
    for p,b in zip(temps,[report_bytes,new_status]):exclusive(p,b);event('temp_verified',p)
    event('report_install_intent',report);os.link(temps[0],report);require(report.read_bytes()==report_bytes,'report mismatch');temps[0].unlink();event('report_installed',report)
    require(not status.is_symlink() and status.read_bytes()==old_status,'final status guard')
    event('status_replace_intent',status);os.replace(temps[1],status);require(status.read_bytes()==new_status,'status mismatch');event('complete')


def run(plan_path,expected,mode,manifest_path=None,manifest_sha=None):
    raw=plan_path.read_bytes();require(sha(raw)==expected,'plan hash');p=decode(raw);require(p['execution_ready'] is True,'incomplete plan')
    root=Path(p['run_root']);require(Path(p['report_target'])==root/'reports/ziwei_068/S2_clean_review.json','report target');require(Path(p['status']['path'])==root/'status.json','status target')
    content,(report,status)=load(p)
    require(len(report)+len(status)<=p['output_cap_bytes'],'output cap before writes')
    for i in p['inputs']:require(checked(i)==content[i['path']],'input changed')
    directory=Path(p['candidate_directory']);names=['S2_clean_review.json','status.json']
    manifest={'plan_sha256':expected,'outputs':[pin(directory/n,b) for n,b in zip(names,[report,status])]}
    if mode=='build':
        require(not directory.exists() and not directory.is_symlink(),'candidate exists');directory.mkdir()
        for n,b in zip(names,[report,status]):exclusive(directory/n,b)
        exclusive(directory/'manifest.json',encode(manifest))
    else:
        require(manifest_path==directory/'manifest.json','wrong manifest path');mb=manifest_path.read_bytes();require(sha(mb)==manifest_sha and decode(mb)==manifest,'manifest mismatch')
        for item,b in zip(manifest['outputs'],[report,status]):require(checked(item)==b,'candidate differs from independent replay')
        publish_pair(root,report,content[p['status']['path']],status)
        for i in p['protected']:checked(i)
    print(json.dumps({'completed':True,'mode':mode,'report_sha256':sha(report),'status_sha256':sha(status)}))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('mode',choices=['build','publish']);a.add_argument('--plan',type=Path,required=True);a.add_argument('--approved-plan-sha256',required=True);a.add_argument('--manifest',type=Path);a.add_argument('--approved-manifest-sha256');x=a.parse_args()
    try:run(x.plan,x.approved_plan_sha256,x.mode,x.manifest,x.approved_manifest_sha256)
    except Exception as error:
        print(json.dumps({'completed':False,'error_type':type(error).__name__,'partial_effects_possible':True,'automatic_retry':False}));raise SystemExit(1)
