"""New deterministic S0 replay. No network, no old-label output, no semantic inference."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

SOURCE = 'ziwei_068'
FIELDS = ('chunk_id', 'source_file', 'page_range', 'chunk_text', 'chunk_index')
OUTPUTS = ('02_working_inputs/ziwei_068.jsonl', 'input_units/ziwei_068.jsonl',
           'image_links/ziwei_068.json', 'continuity/ziwei_068.json',
           'reports/ziwei_068/S0_input_check.json')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def pairs(items: list) -> dict:
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def decode(raw: bytes) -> object:
    def invalid(value):
        raise ValueError('nonfinite JSON')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


def jsonl(rows: list) -> bytes:
    return b''.join((json.dumps(r, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode() for r in rows)


def pin(path: str, raw: bytes) -> dict:
    return {'path': path, 'bytes': len(raw), 'sha256': sha(raw)}


def checked(item: dict) -> bytes:
    path = Path(item['path'])
    require(path.is_file() and not path.is_symlink(), 'input is not a regular file')
    raw = path.read_bytes()
    require(len(raw) == item['bytes'] and sha(raw) == item['sha256'], 'input identity mismatch')
    return raw


def project(raw: bytes, expected_count: int) -> tuple[list, list]:
    """Only return the five-field projection; never echo parse errors or old records."""
    rows, offsets = [], []
    start = 0
    lines = raw.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    for number, line in enumerate(lines, 1):
        require(bool(line.strip()), 'blank JSONL record')
        try:
            obj = decode(line)
            require(isinstance(obj, dict), 'JSONL object required')
            row = {k: obj[k] for k in FIELDS}
        except Exception:
            raise ValueError('invalid strict JSONL projection') from None
        require(isinstance(row['chunk_id'], str) and bool(row['chunk_id']), 'missing ID')
        require(isinstance(row['source_file'], str), 'invalid source file')
        require(isinstance(row['chunk_text'], str), 'invalid text')
        require(type(row['chunk_index']) is int, 'invalid index')
        require(isinstance(row['page_range'], list) and all(type(x) is int for x in row['page_range']), 'invalid pages')
        rows.append(row)
        offsets.append({'ordinal': number, 'byte_span': [start, start + len(line)],
                        'line_sha256': sha(line), 'text_sha256': sha(row['chunk_text'].encode()),
                        'text_codepoints': len(row['chunk_text'])})
        start += len(line) + 1
    require(len(rows) == expected_count, 'unexpected record count')
    require(len({r['chunk_id'] for r in rows}) == len(rows), 'duplicate chunk ID')
    return rows, offsets


def make_controls(manifest_template: dict, status_template: dict, run_id: str,
                  timestamp: str, authorization: dict, source_pin: dict) -> dict:
    """Pure preparation: caller must separately save/review these before actual S0."""
    status = copy.deepcopy(status_template)
    status.update(is_template=False, run_id=run_id)
    inventory = []
    for source in status['sources']:
        current = source['source_id'] == SOURCE
        source['execution_scope'] = 'current' if current else 'catalog_only_out_of_scope'
        for step, value in source['steps'].items():
            value['status'] = 'not_started' if current else 'blocked'
            value['decision'] = 'execute' if current else 'needs_confirmation'
            value['outcome'] = None
            value['reused_from'] = None
            value['output_files'] = []
            value['status_scope'] = 'this_new_run_only_not_historical_progress'
            reason = ('Explicit restart after unavailable prior work; not restoration of old results.'
                      if current else 'Outside this single-source run; historical progress not inferred.')
            inventory.append({'source_id': source['source_id'], 'step': step,
                              'decision': value['decision'], 'reason': reason,
                              'prior_run_id': None, 'prior_report': None, 'artifacts': [],
                              'validation_result': 'not_reused', 'unresolved': ['prior_work_not_available'] if current else ['not_inspected_out_of_scope']})
    require(len(status['sources']) == 150 and len(inventory) == 1200, 'catalog size mismatch')
    manifest = copy.deepcopy(manifest_template)
    manifest.update(is_template=False, run_id=run_id, started_at=timestamp,
                    user_start_instruction=authorization, environment='dot_cloud',
                    execution_scope=[SOURCE], inputs=[source_pin], history_roots=[],
                    unresolved=['Prior Z068 work unavailable; this is a new restart, not recovered old 369 chunks.'])
    return {'run_manifest.json': manifest, 'status.json': status,
            'resume_inventory.json': {'run_id': run_id, 'is_template': False, 'records': inventory}}


def materialize(plan: dict, raw: bytes, report_template: dict, old_status: dict) -> tuple[dict, dict, dict]:
    require(plan['source_id'] == SOURCE and plan['expected_count'] == 276, 'wrong source or scope')
    rows, offsets = project(raw, plan['expected_count'])
    indices = [r['chunk_index'] for r in rows]
    facts = {'index_gaps': sorted(set(range(min(indices), max(indices) + 1)) - set(indices)),
             'duplicate_indices': len(indices) - len(set(indices)),
             'nonmonotonic_indices': any(b <= a for a, b in zip(indices, indices[1:]))}
    require(facts == plan['expected_index_facts'], 'catalog index facts disagree')
    require(all(r['source_file'] == plan['expected_source_file'] for r in rows), 'source file mismatch')
    units = []
    for n, row in enumerate(rows, 1):
        units.append({'source_id': SOURCE, 'unit_id': f'{SOURCE}_u{n:04d}',
                      'unit_type': 'existing_chunk_text', 'ordinal': n,
                      'source_locator': {'input_path': plan['source']['path'],
                                         'input_sha256': plan['source']['sha256'],
                                         'old_chunk_id': row['chunk_id'], 'chunk_index': row['chunk_index'],
                                         'object_ordinal': n, 'line_span': [n, n],
                                         'span': [0, len(row['chunk_text'])]},
                      'page_range': row['page_range'], 'page_basis': 'original_unverified_page_range',
                      'text_sha256': sha(row['chunk_text'].encode())})
    neighbors = []
    for n, (left, right) in enumerate(zip(rows, rows[1:]), 1):
        neighbors.append({'left_unit_id': units[n-1]['unit_id'], 'right_unit_id': units[n]['unit_id'],
                          'status': 'unknown', 'reason': 'File adjacency only; no semantic continuity inference.',
                          'left_chunk_index': left['chunk_index'], 'right_chunk_index': right['chunk_index'],
                          'left_page_range': left['page_range'], 'right_page_range': right['page_range'],
                          'left_excerpt': left['chunk_text'][-80:], 'right_excerpt': right['chunk_text'][:80]})
    files = {OUTPUTS[0]: jsonl(rows), OUTPUTS[1]: jsonl(units),
             OUTPUTS[2]: encode({'source_id': SOURCE, 'links': [], 'scope': 'catalog_only',
                                'reason': 'Source card declares no existing image chunk IDs; no image files inspected or invented.'}),
             OUTPUTS[3]: encode({'source_id': SOURCE, 'pairs': neighbors, 'index_facts': facts,
                                'semantic_continuity_checked': False})}
    require(set(report_template) >= {'step', 'input_files', 'output_files', 'is_template'}, 'invalid report template')
    report = copy.deepcopy(report_template)
    outputs = [pin(str(Path(plan['run_root']) / rel), data) for rel, data in files.items()]
    report.update(step='S0_input_check', status='passed', is_template=False, run_id=plan['run_id'],
                  source_id=SOURCE, route_id='B05', operation='resolve_history_and_register_resume',
                  outcome='input_checked', started_at=None, completed_at=None,
                  executor=plan['executor'], model='unknown', usage=None,
                  input_files=plan['inputs'], output_files=outputs, input_count=276, output_count=276,
                  decision='execute', history_report_missing=True, old_labels_exposed=False,
                  unresolved=[{'type': 'history_unavailable', 'reason': 'New user-authorized restart; not recovered old result.'},
                              {'type': 'source_structure', **facts},
                              {'type': 'continuity', 'unknown_pairs': 275}],
                  nonblocking_issues=[{'type': 'catalog_limit', 'reason': 'Available units do not prove complete original book.'}],
                  sample_checks=[{'check': 'strict_JSONL_five_field_projection', 'records': 276,
                                  'whole_input_checked': True}], semantic_acceptance=False)
    files[OUTPUTS[4]] = encode(report)
    status = copy.deepcopy(old_status)
    require(status['run_id'] == plan['run_id'] and status['is_template'] is False, 'wrong run status')
    selected = [x for x in status['sources'] if x['source_id'] == SOURCE]
    require(len(selected) == 1 and selected[0]['steps']['S0_input_check']['status'] == 'not_started', 'S0 already started')
    selected[0]['steps']['S0_input_check'].update(status='passed', outcome='input_checked', decision='execute',
                                               output_files=[pin(str(Path(plan['run_root']) / r), b) for r, b in files.items()])
    recovery = {'schema': 'new_s0_replay_index_v1', 'run_id': plan['run_id'], 'source_id': SOURCE,
                'source_commit': plan['source_commit'], 'source_git_path': plan['source_git_path'],
                'source_sha256': plan['source']['sha256'], 'record_offsets': offsets,
                'outputs': [pin(r, b) for r, b in files.items()], 'index_facts': facts,
                'status_sha256': sha(encode(status)), 'original_content_included': False,
                'requires_private_source_access': True, 'replay_parameters': plan['replay_parameters'],
                'recorded_timestamp_is_not_execution_time': True}
    return files, status, recovery


def exclusive(path: Path, raw: bytes) -> None:
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    require(path.read_bytes() == raw, 'written bytes mismatch')


def run(plan_path: Path, approved_sha: str) -> None:
    plan_raw = plan_path.read_bytes()
    require(sha(plan_raw) == approved_sha, 'plan identity mismatch')
    plan = decode(plan_raw)
    require(plan['execution_ready'] is True, 'plan is not execution ready')
    root = Path(plan['run_root'])
    require(root.is_dir() and not root.is_symlink(), 'run root unavailable')
    inputs = {item['path']: checked(item) for item in plan['inputs']}
    for key in ('source', 'report_template', 'status'):
        require(plan[key] in plan['inputs'], 'missing actual input binding')
    require(Path(plan['status']['path']) == root / 'status.json', 'status outside run')
    files, status, recovery = materialize(plan, inputs[plan['source']['path']],
                                         decode(inputs[plan['report_template']['path']]),
                                         decode(inputs[plan['status']['path']]))
    archive = root / 'history' / SOURCE / 'pre_s0' / 'status.json'
    journal = root / 'S0_publication_journal.jsonl'
    recovery_path = root / 'S0_REPLAY_INDEX.json'
    temp = root / 'status.s0.tmp'
    targets = [root / r for r in OUTPUTS] + [archive, journal, recovery_path, temp]
    for target in targets:
        require(not target.exists() and not target.is_symlink(), 'output already exists')
        require(target.resolve().is_relative_to(root.resolve()), 'output escapes run')
    for item in plan['inputs']:
        require(sha(Path(item['path']).read_bytes()) == item['sha256'], 'input changed before publication')
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
    def event(kind: str, target: Path | None = None):
        with journal.open('ab') as handle:
            handle.write(jsonl([{'event': kind, 'path': str(target) if target else None}]))
            handle.flush(); os.fsync(handle.fileno())
    exclusive(journal, b'')
    event('begin')
    exclusive(archive, inputs[plan['status']['path']]); event('status_archived', archive)
    for rel, data in files.items():
        event('write_intent', root / rel)
        exclusive(root / rel, data); event('written', root / rel)
    exclusive(recovery_path, encode(recovery)); event('replay_index_written', recovery_path)
    exclusive(temp, encode(status))
    require(checked(plan['status']) == inputs[plan['status']['path']], 'status changed before replace')
    event('status_replace_intent', root / 'status.json')
    os.replace(temp, root / 'status.json')
    require((root / 'status.json').read_bytes() == encode(status), 'status readback mismatch')
    event('complete')
    print(json.dumps({'completed': True, 'replay_index_sha256': sha(encode(recovery))}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True)
    args = parser.parse_args()
    try:
        run(args.plan, args.approved_plan_sha256)
    except Exception as error:
        # Never emit the source JSON, decoder context, or old annotation values.
        print(json.dumps({'completed': False, 'exception_type': type(error).__name__,
                          'partial_effects_possible': True, 'automatic_retry': False}))
        raise SystemExit(1)
