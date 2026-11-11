"""Fresh-only S1 writer. No implicit selection, no source inference, no network."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

RELATIVE_OUTPUTS = (
    '03_cleaned/ziwei/ziwei_068.jsonl',
    'reports/ziwei_068/S1_text_treatment.json',
    'reports/ziwei_068/S1_table.json',
    'reports/ziwei_068/S1_punctuation.json',
    'reports/ziwei_068/S1_semantic.json',
)


def check(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def decode(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            check(k not in out, 'duplicate JSON key')
            out[k] = v
        return out
    def invalid(value):
        raise ValueError('nonfinite JSON')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


def lines(raw):
    items = raw.split(b'\n')
    if items[-1:] == [b'']:
        items.pop()
    check(all(x.strip() for x in items), 'blank JSONL line')
    return [decode(x) for x in items]


def pin(path, raw):
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def read_pin(item):
    path = Path(item['path'])
    check(path.is_file() and not path.is_symlink(), 'input not regular')
    raw = path.read_bytes()
    check(pin(path, raw) == item, 'input pin mismatch')
    return raw


def exclusive(path, raw):
    with path.open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    check(path.read_bytes() == raw, 'exclusive readback mismatch')


def output_paths(root):
    return [root / x for x in RELATIVE_OUTPUTS]


def setup_paths(root):
    return [root / '03_cleaned/ziwei', root / 'reports/ziwei_068',
            root / 'history/pre_s1_manual_completion']


def publication(root, payloads, old_status, new_status):
    """Require prepared parents. Preserve every effect on failure; never retry here."""
    targets = output_paths(root)
    check(set(payloads) == set(RELATIVE_OUTPUTS), 'wrong five payload targets')
    status = root / 'status.json'
    archive = root / 'history/pre_s1_manual_completion/status.json'
    journal = root / 'S1_publication_journal.jsonl'
    temps = [p.with_name(p.name + '.s1.tmp') for p in targets] + [root / 'status.s1.tmp']
    all_new = targets + [archive, journal] + temps
    check(root.is_dir() and not root.is_symlink(), 'run root missing')
    for parent in setup_paths(root):
        check(parent.is_dir() and not parent.is_symlink(), 'required parent missing')
    for path in all_new:
        check(path.resolve().is_relative_to(root.resolve()), 'output outside run')
        check(not path.exists() and not path.is_symlink(), 'fresh output exists')
    check(status.is_file() and not status.is_symlink() and status.read_bytes() == old_status, 'status guard before publish')
    exclusive(journal, b'')
    def event(kind, path=None):
        row = {'event': kind, 'path': str(path) if path else None}
        with journal.open('ab') as f:
            f.write((json.dumps(row, ensure_ascii=False) + '\n').encode()); f.flush(); os.fsync(f.fileno())
    event('begin')
    event('archive_intent', archive); exclusive(archive, old_status); event('archived', archive)
    values = [payloads[r] for r in RELATIVE_OUTPUTS] + [new_status]
    for temp, raw in zip(temps, values):
        event('temp_intent', temp); exclusive(temp, raw); event('temp_verified', temp)
    # All six temporary files were verified before any formal payload installation.
    for target, temp, raw in zip(targets, temps, values):
        event('install_intent', target)
        os.link(temp, target)  # atomic exclusive destination: never overwrites a racing target
        check(target.read_bytes() == raw, 'formal payload readback mismatch')
        temp.unlink(); event('installed', target)
    check(status.read_bytes() == old_status and not status.is_symlink(), 'final status guard')
    event('status_replace_intent', status)
    os.replace(temps[-1], status)
    check(status.read_bytes() == new_status, 'status readback mismatch')
    event('complete')
    return [pin(p, p.read_bytes()) for p in targets + [status, archive]]


def form(plan, content, pure):
    root = Path(plan['run_root'])
    for key in ('raw02', 'units', 'report_template', 'status', 'selection', 'pure_module'):
        check(plan[key] in plan['inputs'], 'missing input binding')
    check(Path(plan['raw02']['path']) == root / '02_working_inputs/ziwei_068.jsonl', 'body must be current 02 only')
    check(Path(plan['status']['path']) == root / 'status.json', 'status binding wrong')
    selection = decode(content[plan['selection']['path']])
    # Explicit final selection schema; no directory scans or latest/supersedes resolution.
    selected = selection['selected_units']
    batches = {x['path']: x for x in selection['selected_batches']}
    check(len(batches) == len(selection['selected_batches']), 'duplicate selected batch')
    check([s['ordinal'] for s in selected] == list(range(1, 277)), 'selection order/coverage')
    cache = {}; records = []
    for entry in selected:
        path = entry['batch_path']
        check(path in batches and batches[path] in plan['inputs'], 'unbound selected batch')
        check(entry['batch_sha256'] == batches[path]['sha256'], 'selected batch hash mismatch')
        if path not in cache:
            cache[path] = lines(content[path])
        line = entry['line_number']
        check(type(line) is int and 1 <= line <= len(cache[path]), 'selection line out of range')
        row = cache[path][line-1]
        check(row['ordinal'] == entry['ordinal'] and row['proposal_id'] == entry['proposal_id'], 'selected identity mismatch')
        check(row['unit_id'] == entry['unit_id'] and row['input_text_sha256'] == entry['input_text_sha256'] and row['result_text_sha256'] == entry['result_text_sha256'], 'selected unit/hash mismatch')
        records.append(row)
    status = decode(content[plan['status']['path']])
    check(status['run_id'] == plan['run_id'] and status['is_template'] is False, 'wrong status run')
    sources = [s for s in status['sources'] if s['source_id'] == 'ziwei_068']
    check(len(sources) == 1, 'source status identity')
    steps = sources[0]['steps']
    check(steps['S0_input_check']['status'] == 'passed' and steps['S1_clean']['status'] == 'not_started', 'stage prerequisites')
    context = dict(run_id=plan['run_id'], attempt=1, executor=plan['executor'],
                   input_files=plan['inputs'], clean_output_path=str(root / RELATIVE_OUTPUTS[0]),
                   selection_evidence=[plan['selection']])
    result = pure.assemble(lines(content[plan['raw02']['path']]), lines(content[plan['units']['path']]),
                           records, decode(content[plan['report_template']['path']]), context)
    payloads = {RELATIVE_OUTPUTS[0]: result['clean_bytes']}
    for rel, key in zip(RELATIVE_OUTPUTS[1:], ('main', 'table', 'punctuation', 'semantic')):
        payloads[rel] = result['report_bytes'][key]
    pins = [pin(root / r, b) for r, b in payloads.items()]
    steps['S1_clean'].update(status='needs_review', outcome='cleaned', decision='execute', output_files=pins)
    return payloads, encode(status)


def run(plan_path, approved_sha):
    raw = plan_path.read_bytes(); check(sha(raw) == approved_sha, 'plan hash mismatch')
    plan = decode(raw); check(plan['execution_ready'] is True, 'plan not ready')
    check(len({i['path'] for i in plan['inputs']}) == len(plan['inputs']), 'duplicate input paths')
    content = {i['path']: read_pin(i) for i in plan['inputs']}
    module_path = Path(plan['pure_module']['path'])
    check(read_pin(plan['pure_module']) == content[str(module_path)], 'pure module changed')
    spec = importlib.util.spec_from_file_location('approved_pure_s1', module_path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    payloads, status = form(plan, content, module)
    check(sum(len(b) for b in payloads.values()) + len(status) <= plan['output_cap_bytes'], 'output cap exceeded before write')
    for item in plan['inputs']:
        check(read_pin(item) == content[item['path']], 'input changed before publication')
    result = publication(Path(plan['run_root']), payloads, content[plan['status']['path']], status)
    print(json.dumps({'completed': True, 'outputs': result}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True); args = parser.parse_args()
    try:
        run(args.plan, args.approved_plan_sha256)
    except Exception as error:
        print(json.dumps({'completed': False, 'error_type': type(error).__name__, 'partial_effects_possible': True, 'automatic_retry': False}))
        raise SystemExit(1)
