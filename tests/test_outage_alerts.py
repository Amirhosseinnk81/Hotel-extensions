"""Alert planning and report helpers (no Flask / database needed)."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import outage_alerts as oa
import outage_report as orp

TEHRAN = ZoneInfo('Asia/Tehran')


def ev(event_id, kind='offline', extension=None, title='Reception', batch='b1', detail='14:02'):
    return {'id': event_id, 'kind': kind, 'batch': batch, 'extension': extension or str(250 + event_id), 'title': title, 'detail': detail}


class SingleAlertTests(unittest.TestCase):
    def test_offline_text(self):
        [alert] = oa.plan_alerts([ev(1, extension='255', title='پذیرش 3')], url='/x/outages')
        self.assertEqual(alert['title'], '☎ داخلی 255 (پذیرش 3) آفلاین شد')
        self.assertEqual(alert['body'], 'قطع از ساعت 14:02')
        self.assertFalse(alert['quiet'])
        self.assertEqual((alert['kind'], alert['id'], alert['url']), ('offline', 1, '/x/outages'))

    def test_online_is_quiet_and_reports_the_duration(self):
        [alert] = oa.plan_alerts([ev(2, 'online', '255', 'پذیرش 3', detail='00:13:11')])
        self.assertEqual(alert['title'], '✅ داخلی 255 (پذیرش 3) دوباره وصل شد')
        self.assertEqual(alert['body'], 'مدت قطعی: 00:13:11')
        self.assertTrue(alert['quiet'])

    def test_missing_title_leaves_no_empty_brackets(self):
        [alert] = oa.plan_alerts([ev(1, extension='255', title='')])
        self.assertEqual(alert['title'], '☎ داخلی 255 آفلاین شد')

    def test_every_alert_has_its_own_tag(self):
        alerts = oa.plan_alerts([ev(1), ev(2), ev(3)])
        self.assertEqual(len({a['tag'] for a in alerts}), 3)


class StormControlTests(unittest.TestCase):
    def test_up_to_the_threshold_stays_individual(self):
        alerts = oa.plan_alerts([ev(i) for i in range(1, 6)], threshold=5)
        self.assertEqual([a['kind'] for a in alerts], ['offline'] * 5)

    def test_more_than_the_threshold_becomes_one_summary(self):
        alerts = oa.plan_alerts([ev(i) for i in range(1, 13)], threshold=5)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['title'], '☎ ۱۲ تلفن همزمان آفلاین شدند')
        self.assertEqual(alerts[0]['kind'], 'summary-offline')
        self.assertFalse(alerts[0]['quiet'])
        self.assertEqual(alerts[0]['id'], 12)   # the newest event: the browser then moves on past all of them

    def test_summary_names_some_phones_and_counts_the_rest(self):
        [alert] = oa.plan_alerts([ev(i, extension=str(300 + i)) for i in range(1, 13)], threshold=5)
        self.assertIn('301، 302', alert['body'])
        self.assertIn('308', alert['body'])
        self.assertNotIn('309', alert['body'])
        self.assertTrue(alert['body'].endswith('و ۴ مورد دیگر'))

    def test_different_sync_cycles_are_not_merged(self):
        events = [ev(i, batch='b1') for i in range(1, 4)] + [ev(i, batch='b2') for i in range(4, 7)]
        alerts = oa.plan_alerts(events, threshold=5)
        self.assertEqual(len(alerts), 6)    # 3 + 3, neither group above the threshold

    def test_offline_and_online_are_counted_separately(self):
        events = [ev(i) for i in range(1, 5)] + [ev(i, 'online', detail='00:01:00') for i in range(5, 9)]
        alerts = oa.plan_alerts(events, threshold=5)
        self.assertEqual(len(alerts), 8)

    def test_many_recoveries_give_one_quiet_summary(self):
        alerts = oa.plan_alerts([ev(i, 'online', detail='00:05:00') for i in range(1, 9)], threshold=5)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['title'], '✅ ۸ تلفن دوباره وصل شدند')
        self.assertTrue(alerts[0]['quiet'])


class RecoverySwitchTests(unittest.TestCase):
    def test_recovery_alerts_can_be_switched_off(self):
        events = [ev(1), ev(2, 'online', detail='00:01:00')]
        alerts = oa.plan_alerts(events, on_recovery=False)
        self.assertEqual([a['kind'] for a in alerts], ['offline'])

    def test_alerts_come_out_in_event_order(self):
        events = [ev(1), ev(2, 'online', detail='00:01:00'), ev(3, batch='b2')]
        self.assertEqual([a['id'] for a in oa.plan_alerts(events)], [1, 2, 3])

    def test_no_events_no_alerts(self):
        self.assertEqual(oa.plan_alerts([]), [])


class DigitTests(unittest.TestCase):
    def test_persian_digits(self):
        self.assertEqual(oa.persian_digits(1204), '۱۲۰۴')

    def test_normalize_digits_goes_the_other_way(self):
        self.assertEqual(orp.normalize_digits('۲۵۵ و ٢٥٦'), '255 و 256')


class DayBoundTests(unittest.TestCase):
    def test_parse_day(self):
        self.assertEqual(orp.parse_day('2026-10-16'), date(2026, 10, 16))
        self.assertEqual(orp.parse_day('۲۰۲۶-۱۰-۱۶'), date(2026, 10, 16))
        for bad in (None, '', 'tomorrow', '2026-13-40', '16/10/2026'):
            self.assertIsNone(orp.parse_day(bad), bad)

    def test_a_local_day_is_converted_to_utc(self):
        # Tehran is UTC+03:30 all year (no daylight saving since 2022)
        start, end = orp.day_bounds_utc(date(2026, 10, 16), date(2026, 10, 16), TEHRAN)
        self.assertEqual(start, datetime(2026, 10, 15, 20, 30))
        self.assertEqual(end, datetime(2026, 10, 16, 20, 30))

    def test_open_ended_ranges(self):
        start, end = orp.day_bounds_utc(date(2026, 10, 1), None, TEHRAN)
        self.assertEqual((start, end), (datetime(2026, 9, 30, 20, 30), None))
        start, end = orp.day_bounds_utc(None, date(2026, 10, 1), TEHRAN)
        self.assertEqual((start, end), (None, datetime(2026, 10, 1, 20, 30)))

    def test_reversed_days_are_swapped(self):
        self.assertEqual(orp.day_bounds_utc(date(2026, 10, 5), date(2026, 10, 1), TEHRAN),
                         orp.day_bounds_utc(date(2026, 10, 1), date(2026, 10, 5), TEHRAN))

    def test_today_starts_at_local_midnight(self):
        # 22:00 UTC is already 01:30 of the next day in Tehran
        self.assertEqual(orp.local_midnight_utc(datetime(2026, 10, 16, 22, 0), TEHRAN), datetime(2026, 10, 16, 20, 30))
        self.assertEqual(orp.local_midnight_utc(datetime(2026, 10, 16, 10, 0), TEHRAN), datetime(2026, 10, 15, 20, 30))


class WorstOffenderTests(unittest.TestCase):
    def test_ranked_by_count_then_downtime(self):
        rows = [('255', 'Old title', 60), ('256', 'Kitchen', 600), ('255', 'Reception', 30), ('257', 'Bar', 5000), ('256', 'Kitchen', 600)]
        ranked = orp.worst_offenders(rows)
        self.assertEqual([r['extension'] for r in ranked], ['256', '255', '257'])
        self.assertEqual((ranked[0]['count'], ranked[0]['seconds'], ranked[0]['duration']), (2, 1200, '00:20:00'))
        self.assertEqual(ranked[1]['title'], 'Reception')   # the latest title wins

    def test_limit(self):
        rows = [(str(300 + i), 't', 1) for i in range(9)]
        self.assertEqual(len(orp.worst_offenders(rows, limit=5)), 5)

    def test_empty(self):
        self.assertEqual(orp.worst_offenders([]), [])


class CsvSafeTests(unittest.TestCase):
    def test_formula_starts_are_defused(self):
        for text in ('=1+1', '+SUM(A1)', '-2', '@cmd'):
            self.assertEqual(orp.csv_safe(text), "'" + text)

    def test_normal_text_and_none(self):
        self.assertEqual(orp.csv_safe('پذیرش 3'), 'پذیرش 3')
        self.assertEqual(orp.csv_safe(None), '')


if __name__ == '__main__':
    unittest.main()
