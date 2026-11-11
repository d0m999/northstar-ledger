import copy
import unittest
from assemble_s1 import assemble, digest, replay_unit


def fixture(ordinal=1):
    text='甲乙丙。'
    raw=dict(chunk_id=f'fake_{ordinal}',source_file='fake.pdf',page_range=[1],chunk_text=text,chunk_index=ordinal)
    unit=dict(source_id='ziwei_068',unit_id=f'fake_u{ordinal}',ordinal=ordinal,text_sha256=digest(text),source_locator=dict(old_chunk_id=raw['chunk_id'],chunk_index=ordinal,span=[0,4]))
    p=dict(source_id='ziwei_068',unit_id=unit['unit_id'],ordinal=ordinal,input_text_sha256=digest(text),input_codepoints=4,read_span=[0,4],proposal_id=f'p{ordinal}',decision_revision=1,author_id='synthetic',supersedes=None,patches=[],unresolved=[],filtered_units=[],result_text_sha256=digest(text),result_codepoints=4,substeps=[dict(substep_id=f'{ordinal}_{k}',outcome='no_change',reason='Synthetic decision only.',patch_ids=[],issue_ids=[]) for k in ('table','punctuation','semantic')])
    return raw,unit,p


def changed():
    raw,u,p=fixture();p['patches']=[dict(patch_id='x',original_span=[1,2],new_after='丁',kind='fake',reason='Synthetic edit.',substep_ids=['1_semantic'])]
    p['substeps'][2].update(outcome='changed',patch_ids=['x']);p['result_text_sha256']=digest('甲丁丙。');return raw,u,p


class Tests(unittest.TestCase):
    def test_no_change_preserves_record(self):
        r,u,p=fixture();out,proc=replay_unit(r,u,p);self.assertEqual(out,r);self.assertEqual(proc['author_record'],p)

    def test_replacement_private_before(self):
        out,proc=replay_unit(*changed());self.assertEqual(out['chunk_text'],'甲丁丙。');self.assertEqual(proc['changes'][0]['before'],'乙')

    def test_wrong_hash_rejected(self):
        r,u,p=fixture();p['input_text_sha256']='wrong'
        with self.assertRaises(ValueError):replay_unit(r,u,p)

    def test_partial_read_rejected(self):
        r,u,p=fixture();p['read_span']=[0,3]
        with self.assertRaises(ValueError):replay_unit(r,u,p)

    def test_issue_literal(self):
        r,u,p=fixture();p['unresolved']=[dict(issue_id='q',original_span=[1,3],category='fake',reason='Synthetic uncertainty.',use_restriction='No assertion.')];p['substeps'][2]['issue_ids']=['q'];out,proc=replay_unit(r,u,p);self.assertEqual(proc['unresolved'][0]['original_excerpt'],'乙丙')

    def test_insertion_and_delete(self):
        r,u,p=changed();p['patches'][0].update(original_span=[1,1],new_after='丁');p['result_text_sha256']=digest('甲丁乙丙。');p['result_codepoints']=5
        self.assertEqual(replay_unit(r,u,p)[0]['chunk_text'],'甲丁乙丙。')
        p['patches'][0].update(original_span=[1,2],new_after='');p['result_text_sha256']=digest('甲丙。');p['result_codepoints']=3
        self.assertEqual(replay_unit(r,u,p)[0]['chunk_text'],'甲丙。')

    def test_filter_and_link_rejected(self):
        r,u,p=fixture();p['filtered_units']=[{}]
        with self.assertRaises(ValueError):replay_unit(r,u,p)
        r,u,p=changed();p['patches'][0]['substep_ids']=[]
        with self.assertRaises(ValueError):replay_unit(r,u,p)

    def test_complete_and_missing_selection(self):
        triples=[fixture(i) for i in range(1,277)];raw,units,props=map(list,zip(*triples))
        ctx=dict(selection_evidence=[{'synthetic':True}],clean_output_path='fake/03.jsonl',run_id='fake',input_files=[],executor='fake',attempt=1)
        result=assemble(raw,units,props,{'is_template':True,'custom_template_field':'keep'},ctx)
        self.assertEqual(len(result['reports']['main']['process_records']),276)
        self.assertEqual(result['reports']['main']['custom_template_field'],'keep')
        self.assertEqual(result['reports']['main']['status'],'needs_review')
        with self.assertRaises(ValueError):assemble(raw,units,props[:-1],{'is_template':True},ctx)


if __name__=='__main__':unittest.main()
