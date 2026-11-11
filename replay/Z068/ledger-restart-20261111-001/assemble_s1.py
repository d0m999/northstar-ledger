"""Pure in-memory replay of explicitly selected human S1 decisions; no IO or inference."""
from __future__ import annotations
import copy
import hashlib
import json

FIELDS = ('chunk_id', 'source_file', 'page_range', 'chunk_text', 'chunk_index')
KINDS = ('table', 'punctuation', 'semantic')


def check(ok, message):
    if not ok:
        raise ValueError(message)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def jsonl(rows):
    return b''.join((json.dumps(r, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode() for r in rows)


def unique(items, label):
    check(len(items) == len(set(items)), 'duplicate ' + label)


def span(value, length):
    check(isinstance(value, list) and len(value) == 2 and all(type(x) is int for x in value), 'invalid span')
    a, b = value
    check(0 <= a <= b <= length, 'span out of bounds')
    return a, b


def substep_kind(record):
    explicit = record.get('substep')
    aliases = {'table/format': 'table', 'table': 'table', 'punctuation': 'punctuation',
               'semantic/OCR': 'semantic', 'semantic': 'semantic'}
    kind = aliases.get(explicit) if explicit is not None else record['substep_id'].rsplit('_', 1)[-1]
    check(kind in KINDS, 'unrecognized explicit substep')
    return kind


def replay_unit(raw, unit, proposal):
    """Recover private evidence from exact source spans; retain the human record verbatim."""
    check(set(raw) == set(FIELDS), 'working input must be five-field projection')
    text = raw['chunk_text']
    check(isinstance(text, str), 'invalid source text')
    check(proposal['source_id'] == unit['source_id'] == 'ziwei_068', 'source mismatch')
    check(proposal['unit_id'] == unit['unit_id'] and proposal['ordinal'] == unit['ordinal'], 'unit mismatch')
    check(proposal['input_text_sha256'] == unit['text_sha256'] == digest(text), 'source hash mismatch')
    check(proposal['input_codepoints'] == len(text) and proposal['read_span'] == [0, len(text)], 'incomplete reading')
    locator = unit['source_locator']
    check(locator['old_chunk_id'] == raw['chunk_id'] and locator['chunk_index'] == raw['chunk_index'], 'raw identity mismatch')
    check(locator['span'] == [0, len(text)], 'raw coordinate mismatch')
    check(proposal['filtered_units'] == [], 'nonempty filter shape requires explicit supported interface, not silent omission')
    patches = proposal['patches']; issues = proposal['unresolved']; steps = proposal['substeps']
    check(len(steps) == 3, 'three substeps required')
    kinds = [substep_kind(s) for s in steps]
    check(set(kinds) == set(KINDS), 'missing substep')
    pids = [p['patch_id'] for p in patches]; iids = [i['issue_id'] for i in issues]; sids = [s['substep_id'] for s in steps]
    unique(pids, 'patch'); unique(iids, 'issue'); unique(sids, 'substep')
    for s in steps:
        check(s['outcome'] in ('changed', 'no_change', 'not_applicable'), 'invalid substep outcome')
        check(isinstance(s['reason'], str) and s['reason'].strip(), 'missing human reason')
        unique(s['patch_ids'], 'substep patch'); unique(s['issue_ids'], 'substep issue')
        check(set(s['patch_ids']) <= set(pids) and set(s['issue_ids']) <= set(iids), 'unresolved substep references')
        check((s['outcome'] == 'changed') == bool(s['patch_ids']), 'changed outcome/patch disagreement')
    check(set(iids) == {i for s in steps for i in s['issue_ids']}, 'unassigned issue')
    changes = []; parts = []; cursor = 0; previous = None
    for p in patches:
        a, b = span(p['original_span'], len(text))
        check(a >= cursor and (previous is None or (a, b) != previous), 'patch ordering/overlap ambiguity')
        if previous is not None:
            check(not (a == previous[0] and (a == b or previous[0] == previous[1])), 'ambiguous insertion at patch start')
        check(isinstance(p['new_after'], str) and isinstance(p['reason'], str) and p['reason'].strip(), 'invalid patch')
        unique(p['substep_ids'], 'patch substep')
        check(set(p['substep_ids']) == {s['substep_id'] for s in steps if p['patch_id'] in s['patch_ids']} and bool(p['substep_ids']), 'patch/substep disagreement')
        before = text[a:b]
        if 'before_sha256' in p:
            check(p['before_sha256'] == digest(before), 'before hash mismatch')
        check(before != p['new_after'], 'no-op patch')
        parts.extend([text[cursor:a], p['new_after']]); cursor = b; previous = (a, b)
        change = copy.deepcopy(p)
        change.update(change_id=p['patch_id'], unit_id=unit['unit_id'], ordinal=unit['ordinal'],
                      source_locator=copy.deepcopy(locator), before=before, after=p['new_after'],
                      before_sha256=digest(before), after_sha256=digest(p['new_after']))
        changes.append(change)
    parts.append(text[cursor:]); result = ''.join(parts)
    check(digest(result) == proposal['result_text_sha256'] and len(result) == proposal['result_codepoints'], 'result mismatch')
    unresolved = []
    for issue in issues:
        a, b = span(issue['original_span'], len(text))
        check(all(isinstance(issue[k], str) and issue[k].strip() for k in ('category', 'reason', 'use_restriction')), 'issue missing disposition')
        item = copy.deepcopy(issue)
        item.update(unit_id=unit['unit_id'], ordinal=unit['ordinal'], source_locator=copy.deepcopy(locator),
                    original_excerpt=text[a:b], excerpt_sha256=digest(text[a:b]))
        unresolved.append(item)
    output = copy.deepcopy(raw); output['chunk_text'] = result
    process = {'unit_id': unit['unit_id'], 'ordinal': unit['ordinal'], 'author_record': copy.deepcopy(proposal),
               'changes': changes, 'unresolved': unresolved, 'filtered_units': [],
               'input_text_sha256': digest(text), 'result_text_sha256': digest(result)}
    return output, process


def assemble(raw_rows, units, selected_records, report_template, context):
    """No version selection: caller supplies exact ordered selected rows plus immutable evidence."""
    check(len(raw_rows) == len(units) == len(selected_records) == 276, 'complete 276 required')
    check([u['ordinal'] for u in units] == list(range(1, 277)), 'input units not original order')
    check([p['ordinal'] for p in selected_records] == list(range(1, 277)), 'selected records not complete original order')
    unique([u['unit_id'] for u in units], 'unit'); unique([r['chunk_id'] for r in raw_rows], 'raw ID')
    unique([p['proposal_id'] for p in selected_records], 'proposal')
    check(context['selection_evidence'], 'explicit final selection evidence required')
    outputs=[]; processes=[]
    for raw, unit, proposal in zip(raw_rows, units, selected_records):
        output, process = replay_unit(raw, unit, proposal); outputs.append(output); processes.append(process)
    all_changes=[c for p in processes for c in p['changes']]
    all_issues=[i for p in processes for i in p['unresolved']]
    # IDs are scoped by unit; neither repeated IDs nor shared reasons are silently deduplicated.
    clean=jsonl(outputs)
    clean_pin={'path':context['clean_output_path'], 'bytes':len(clean), 'sha256':hashlib.sha256(clean).hexdigest()}
    check(report_template.get('is_template') is True, 'fixed template required')
    def base():
        r=copy.deepcopy(report_template)
        r.update(step='S1_clean',status='needs_review',is_template=False,run_id=context['run_id'],
                 source_id='ziwei_068',route_id='B05',operation='reclean_all_surviving_blocks',
                 input_files=copy.deepcopy(context['input_files']),output_files=[clean_pin],
                 input_count=276,output_count=276,started_at=None,completed_at=None,
                 executor=context['executor'],model='unknown',usage=None,attempt=context['attempt'],
                 old_labels_exposed=False,human_review_status='pending_S2',semantic_acceptance=False,
                 decision='execute',execution_policy='resolve_history_first',
                 selection_evidence=copy.deepcopy(context['selection_evidence']))
        return r
    main=base();main.update(outcome='cleaned',changes=all_changes,unresolved=all_issues,
                           process_records=processes,filtered_units=[{'unit_id':u['unit_id'],'ordinal':u['ordinal'],'records':[]} for u in units],
                           substeps=[{'unit_id':p['unit_id'],'ordinal':p['ordinal'],'records':copy.deepcopy(p['author_record']['substeps'])} for p in processes],
                           counts={'units':276,'patches':len(all_changes),'changed_units':sum(bool(p['changes']) for p in processes),'unresolved':len(all_issues),'filtered':0})
    reports={'main':main}
    for kind in KINDS:
        r=base(); records=[]; changes=[]; issues=[]
        for process in processes:
            step=next(s for s in process['author_record']['substeps'] if substep_kind(s)==kind)
            records.append({'unit_id':process['unit_id'],'ordinal':process['ordinal'],'record':copy.deepcopy(step)})
            changes.extend(copy.deepcopy(c) for c in process['changes'] if c['patch_id'] in step['patch_ids'])
            issues.extend(copy.deepcopy(i) for i in process['unresolved'] if i['issue_id'] in step['issue_ids'])
        outcomes=[x['record']['outcome'] for x in records]
        outcome='changed' if 'changed' in outcomes else ('not_applicable' if set(outcomes)=={'not_applicable'} else 'no_change')
        r.update(substep=kind,outcome=outcome,substeps=records,changes=changes,unresolved=issues,
                 applicability_reason='See complete individual human substep dispositions.' if outcome=='not_applicable' else None,
                 filtered_units=copy.deepcopy(main['filtered_units']))
        reports[kind]=r
    return {'clean_bytes':clean,'reports':reports,'report_bytes':{k:encoded(v) for k,v in reports.items()}}
