"""验证跨季方案比较、来源版本与历史报告复现。"""
import json,unittest
from pathlib import Path
from climate_analysis.evidence import connect,receive
from climate_analysis.report import (seasonal_report,seasonal_difference,
                                     replay,list_reports,archive)
from climate_analysis.seasonal import compare_season,months_between,pins_from_report

DATA=json.loads((Path(__file__).resolve().parents[1]/'data/seasonal.json').read_text(encoding='utf8'))
SCENARIOS=DATA['scenarios']
EARLY,LATE=DATA['known_at']

class Seasonal(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:')
  for doc in DATA['documents']:receive(self.db,doc)
 def tearDown(self):self.db.close()

 def test_months_span_year_boundary(self):
  self.assertEqual(months_between('2026-11','2027-02'),
                   ['2026-11','2026-12','2027-01','2027-02'])
  with self.assertRaises(ValueError):months_between('2026-09','2026-07')
  with self.assertRaises(ValueError):months_between('2026-7','2026-09')

 def test_inputs_keep_own_time_scale(self):
  report=compare_season(self.db,EARLY,SCENARIOS[0])
  months={row['month']:row for row in report['months']}
  self.assertEqual(months['2026-07']['meltwater_estimate'],120)
  self.assertEqual(months['2026-07']['inflow'],80)
  self.assertEqual(months['2026-07']['storage_capacity'],200)
  self.assertEqual(months['2026-07']['irrigation_demand'],150)
  # 融水估计与来水观测并列显示, 各自保留来源。
  self.assertEqual(report['meltwater_vs_inflow'][0]['difference'],-40)
  kinds={row['source'] for row in report['sources']['meltwater']}
  self.assertIn('冰川估计组',kinds)

 def test_known_at_excludes_later_revisions(self):
  early=compare_season(self.db,EARLY,SCENARIOS[0])
  late=compare_season(self.db,LATE,SCENARIOS[0])
  early_aug=next(row for row in early['months'] if row['month']=='2026-08')
  late_aug=next(row for row in late['months'] if row['month']=='2026-08')
  self.assertEqual(early_aug['meltwater_estimate'],160)
  self.assertEqual(late_aug['meltwater_estimate'],210)

 def test_storage_maintenance_changes_commitment_months(self):
  report=seasonal_report(self.db,LATE,SCENARIOS,archived_at=LATE)
  pair=report['comparisons'][0]
  self.assertEqual(pair['changed_inputs'],['storage'])
  # 检修缩短可供水区间, 9 月不再能按原库容承诺。
  self.assertEqual(pair['affected_months'],['2026-09'])
  self.assertEqual(pair['months']['2026-09']['kind'],'可供水区间恢复')

 def test_estimate_revision_that_moves_commitment_is_flagged(self):
  report=seasonal_report(self.db,LATE,SCENARIOS,archived_at=LATE)
  pinned=compare_season(self.db,EARLY,SCENARIOS[1])
  revised=compare_season(self.db,LATE,SCENARIOS[1])
  diff=__import__('climate_analysis.seasonal',fromlist=['compare_reports']).compare_reports(pinned,revised)
  self.assertEqual(diff['changed_inputs'],['meltwater'])
  # 估计被上调后 9 月出现缺口, 虽只改估计也必须指出承诺受影响。
  self.assertIn('2026-09',diff['affected_months'])
  self.assertEqual(diff['months'][2]['kind'],'承诺变化')

 def test_estimate_change_without_commitment_change_is_caliber_only(self):
  a=compare_season(self.db,EARLY,SCENARIOS[0])
  diff=__import__('climate_analysis.seasonal',fromlist=['compare_reports']).compare_reports(a,a)
  self.assertEqual(diff['changed_inputs'],[])
  self.assertEqual(diff['affected_months'],[])
  self.assertEqual({row['kind'] for row in diff['months']}, {'同口径无差异'})

 def test_pinned_old_revision_reproduces_old_decision(self):
  early=seasonal_report(self.db,EARLY,SCENARIOS,archived_at=EARLY)
  original=early['scenarios']['irrigation-2026']
  # 修订到手后, 按当时使用过的各记录版本重算, 结论必须与旧报告一致。
  pinned=compare_season(self.db,LATE,dict(SCENARIOS[0],**pins_from_report(original)))
  self.assertEqual([row['meltwater_estimate'] for row in pinned['months']],
                   [row['meltwater_estimate'] for row in original['months']])
  self.assertEqual(pinned['supply'],original['supply'])

 def test_partial_pin_uses_latest_for_remaining_records(self):
  partial=compare_season(self.db,LATE,dict(SCENARIOS[0],meltwater={'melt-08':'r1'}))
  values=[row['meltwater_estimate'] for row in partial['months']]
  self.assertEqual(values,[120,160,60])

 def test_pinning_unknown_revision_is_rejected(self):
  with self.assertRaises(ValueError):
   compare_season(self.db,EARLY,dict(SCENARIOS[0],meltwater={'melt-08':'r2'}))

 def test_mixed_units_do_not_subtract(self):
  docs=[{'source':'灌溉部门','document_id':'demand-alt','revision':'r1',
         'received_at':'2026-07-27T00:00:00+00:00','records':[
           {'kind':'demand','id':'dem-08','revision':'r2','valid_from':'2026-08',
            'payload':{'metric':'demand','unit':'万m3','value':20}}]}]
  for doc in docs:receive(self.db,doc)
  with self.assertRaises(ValueError):
   compare_season(self.db,LATE,dict(SCENARIOS[0],demand={'dem-08':'r2'}))

 def test_missing_month_is_not_filled_from_previous(self):
  db=connect(':memory:')
  try:
   for doc in DATA['documents']:
    if doc['document_id'] in ('meltwater-estimate','inflow','storage-capacity'):receive(db,doc)
   receive(db,{'source':'灌溉部门','document_id':'demand-gap','revision':'r1',
     'received_at':'2026-07-27T00:00:00+00:00','records':[
       {'kind':'demand','id':'dem-gap-07','revision':'r1','valid_from':'2026-07',
        'payload':{'metric':'demand','unit':'m3','value':110}}]})
   report=compare_season(db,EARLY,SCENARIOS[0])
   months={row['month']:row for row in report['months']}
   # 8、9 月没有需求记录, 不得把 7 月的值顺延。
   self.assertEqual(months['2026-07']['irrigation_demand'],110)
   self.assertEqual(months['2026-08']['irrigation_demand'],None)
   self.assertEqual(months['2026-09']['irrigation_demand'],None)
  finally:db.close()

 def test_misaligned_ranges_compare_by_month(self):
  short=dict(SCENARIOS[0],id='short',end='2026-08')
  report=seasonal_report(self.db,EARLY,[SCENARIOS[0],short],archived_at=EARLY)
  pair=report['comparisons'][0]
  self.assertEqual(sorted(pair['months']),['2026-07','2026-08','2026-09'])
  self.assertEqual(pair['months']['2026-09']['kind'],'不可直接比较')
  self.assertEqual(pair['affected_months'],[])

 def test_archive_cannot_overwrite_history(self):
  first=seasonal_report(self.db,LATE,SCENARIOS,archived_at=LATE)
  replay_first=seasonal_report(self.db,LATE,SCENARIOS,archived_at=LATE)
  self.assertTrue(replay_first['replayed'])
  self.assertEqual(first['report_id'],replay_first['report_id'])
  self.assertEqual(first['fingerprint'],replay_first['fingerprint'])
  with self.assertRaises(ValueError):
   archive(self.db,'seasonal',{'scenario':'tampered'},LATE,SCENARIOS,archived_at=LATE)

 def test_replay_uses_archived_body_not_new_sources(self):
  saved=seasonal_report(self.db,EARLY,SCENARIOS,archived_at=EARLY)
  restored=replay(self.db,saved['report_id'])
  self.assertEqual(restored['fingerprint'],saved['fingerprint'])
  self.assertEqual(restored['scenarios'],saved['scenarios'])
  self.assertEqual([row['known_at'] for row in list_reports(self.db,'seasonal')],[EARLY])

 def test_two_periods_are_listed_separately(self):
  for known_at in (EARLY,LATE):
   seasonal_report(self.db,known_at,SCENARIOS,archived_at=known_at)
  self.assertEqual([row['known_at'] for row in list_reports(self.db,'seasonal')],[EARLY,LATE])

if __name__=='__main__':unittest.main()
