"""Synthetic objects only. Static delivery; execution requires separate approval."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import register_s2_v3 as m


def fixture():
    main={'process_records':[], 'changes':[{} for _ in range(85)],
          'unresolved':[{} for _ in range(527)],
          'filtered_units':[{'ordinal':i,'records':[]} for i in range(1,277)]}
    selection={'selected_units':[]};reviews=[]
    for n in range(1,277):
        p=[{'patch_id':f'p{n}'}] if n<=85 else []
        u=[{'issue_id':f'i{n}a'}]+([{'issue_id':f'i{n}b'}] if n<=251 else [])
        sub=[{'substep_id':str(i)} for i in range(3)]
        a={'proposal_id':f'a{n}','input_text_sha256':'a'*64,'result_text_sha256':'b'*64,
           'input_codepoints':1,'result_codepoints':1,'patches':p,'unresolved':u,'substeps':sub}
        main['process_records'].append({'ordinal':n,'unit_id':str(n),'author_record':a,
              'input_text_sha256':'a'*64,'result_text_sha256':'b'*64})
        selection['selected_units'].append({'ordinal':n,'unit_id':str(n),'proposal_id':f'a{n}','batch_sha256':'c'*64})
        r={'ordinal':n,'unit_id':str(n),'author_proposal_id':f'a{n}','author_batch_sha256':'c'*64,
           'original_text_sha256':'a'*64,'clean_text_sha256':'b'*64,
           'reviewed_patch_ids':[x['patch_id'] for x in p],'reviewed_uncertainty_ids':[x['issue_id'] for x in u],
           'three_substep_ids':['0','1','2'],'new_findings':[], 'result':'supported',
           'custom_original_record':{'preserve':True}}
        if n in m.REUSED:
            r.update(review_origin='prior_full_non_author_review',prior_review_sha256='d'*64,current_version_bridge_sha256='d'*64)
        else:r.update(review_origin='new_full_non_author_review',original_read_span=[0,1],actual_clean_read_span=[0,1])
        reviews.append(r)
    decision={'status':'passed','complete_current_review':True,'non_author_review_confirmed':True,
              'blocking_issues':[],'nonblocking_issues':[],'new_findings_in_source_order':[]}
    return main,selection,reviews,{'d'*64},decision


class Tests(unittest.TestCase):
    def test_additional_actual_labels(self):
        for label in ('pass','supported_with_nonblocking_source_findings',
                      'pass_with_retained_source_uncertainties','reviewed_no_blocking_findings',
                      'reviewed_with_source_uncertainties'):
            main,sel,rows,h,d=fixture();rows[219]['result']=label
            self.assertEqual(m.validate(main,sel,rows,h,d)['units'],276)
    def test_original_and_clean_span_pair(self):
        main,sel,rows,h,d=fixture();r=rows[219]
        r['clean_read_span']=r.pop('actual_clean_read_span')
        self.assertEqual(m.validate(main,sel,rows,h,d)['newly_read'],261)
    def test_wrong_or_missing_clean_span(self):
        for missing in (False,True):
            main,sel,rows,h,d=fixture();r=rows[219];r.pop('actual_clean_read_span')
            if not missing:r['clean_read_span']=[0,0]
            with self.assertRaises((ValueError,KeyError)):m.validate(main,sel,rows,h,d)
    def test_unknown_label_rejected(self):
        main,sel,rows,h,d=fixture();rows[219]['result']='unknown_result'
        with self.assertRaises(ValueError):m.validate(main,sel,rows,h,d)
    def test_stage_map_and_kind_records(self):
        main,sel,rows,h,d=fixture()
        reports=[{'substep':stage,'substeps':[{'ordinal':n,'unit_id':str(n),'record':{'substep_id':str(k)}} for n in range(1,277)]} for k,stage in enumerate(('table','punctuation','semantic'))]
        maps=m.stage_maps(main,reports);r=rows[219]
        r['reviewed_patches']=[];r['reviewed_uncertainties']=[{'issue_id':x} for x in r.pop('reviewed_uncertainty_ids')]
        r.pop('reviewed_patch_ids');r.pop('three_substep_ids',None)
        r['three_substep_review']=[{'substep':x,'result':'supported','reason':'synthetic'} for x in ('table','punctuation','semantic')]
        self.assertEqual(m.validate(main,sel,rows,h,d,maps)['units'],276)
        for labels in (('table','table','semantic'),('table','semantic'),('table','punctuation','other')):
            r['three_substep_review']=[{'substep':x} for x in labels]
            with self.assertRaises((ValueError,KeyError)):m.validate(main,sel,rows,h,d,maps)
    def test_invalid_actual_report_map(self):
        main,sel,rows,h,d=fixture()
        reports=[{'substep':stage,'substeps':[{'ordinal':n,'unit_id':str(n),'record':{'substep_id':str(k)}} for n in range(1,277)]} for k,stage in enumerate(('table','punctuation','semantic'))]
        reports[0]['substeps'][219]['record']['substep_id']='wrong'
        with self.assertRaises(ValueError):m.stage_maps(main,reports)
    def test_new_read_alias_and_span_guards(self):
        main,sel,rows,h,d=fixture();r=rows[249];r['review_origin']='new_full_non_author_read'
        r['read_span']=r.pop('original_read_span');r['clean_read_span']=r.pop('actual_clean_read_span')
        self.assertEqual(m.validate(main,sel,rows,h,d)['newly_read'],261)
        r['clean_read_span']=[0,0]
        with self.assertRaises(ValueError):m.validate(main,sel,rows,h,d)
        del r['clean_read_span']
        with self.assertRaises(KeyError):m.validate(main,sel,rows,h,d)
    def test_scope_and_preserve(self):
        main,sel,rows,hashes,d=fixture();before=copy.deepcopy(rows)
        self.assertEqual(m.validate(main,sel,rows,hashes,d)['uncertainties'],527)
        self.assertEqual(rows,before)
    def test_missing_review_patch_or_evidence(self):
        for defect in ('row','patch','proof'):
            main,sel,rows,h,d=fixture()
            if defect=='row':rows.pop()
            elif defect=='patch':rows[0]['reviewed_patch_ids']=[]
            else:h=set()
            with self.assertRaises(ValueError):m.validate(main,sel,rows,h,d)
    def test_failed_explicit_and_finding_preservation(self):
        main,sel,rows,h,d=fixture();finding={'category':'synthetic','restriction':'retain'}
        rows[20]['new_findings']=[finding];d.update(status='failed',blocking_issues=[{'ordinal':21}],new_findings_in_source_order=[{'ordinal':21,'finding':finding}])
        self.assertEqual(m.validate(main,sel,rows,h,d)['newly_read'],261)
        d['new_findings_in_source_order']=[]
        with self.assertRaises(ValueError):m.validate(main,sel,rows,h,d)
    def test_nonblocking_finding_does_not_force_source_rewrite(self):
        main,sel,rows,h,d=fixture();finding={'category':'synthetic','restriction':'do not infer'}
        rows[25]['new_findings']=[finding];d['new_findings_in_source_order']=[{'ordinal':26,'finding':finding}]
        d['nonblocking_issues']=[{'ordinal':26,'finding':finding}]
        self.assertEqual(m.validate(main,sel,rows,h,d)['patches'],85)
    def test_three_review_shapes(self):
        r={'reviewed_patches':[{'patch_id':'p'}],'reviewed_uncertainties':[{'issue_id':'i'}],'three_substep_review':[{'substep_id':'s'}]}
        self.assertEqual(m.review_keys(r),(['p'],['i'],['s']))
        r['reviewed_patches_with_specific_support_or_finding']=r.pop('reviewed_patches')
        r['reviewed_uncertainties_with_specific_disposition']=r.pop('reviewed_uncertainties')
        self.assertEqual(m.review_keys(r),(['p'],['i'],['s']))
    def test_publish_and_fresh_refusal(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);(r/'reports/ziwei_068').mkdir(parents=True);(r/'history/pre_s2_registration').mkdir(parents=True);(r/'status.json').write_bytes(b'old')
            m.publish_pair(r,b'report',b'old',b'new')
            self.assertEqual((r/'status.json').read_bytes(),b'new')
            self.assertEqual((r/'history/pre_s2_registration/status.json').read_bytes(),b'old')
            with self.assertRaises(ValueError):m.publish_pair(r,b'report',b'new',b'newer')
    def test_partial_failure_keeps_evidence_and_old_status(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);(r/'reports/ziwei_068').mkdir(parents=True);(r/'history/pre_s2_registration').mkdir(parents=True);(r/'status.json').write_bytes(b'old')
            with patch.object(m.os,'link',side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):m.publish_pair(r,b'report',b'old',b'new')
            self.assertEqual((r/'status.json').read_bytes(),b'old')
            self.assertEqual((r/'history/pre_s2_registration/status.json').read_bytes(),b'old')
            self.assertTrue((r/'status.s2.tmp').exists())
            self.assertTrue((r/'S2_registration_journal.jsonl').exists())

if __name__=='__main__':unittest.main()
