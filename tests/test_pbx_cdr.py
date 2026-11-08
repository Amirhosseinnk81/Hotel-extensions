"""Call report (pbx_cdr.py + the /calls pages): Jalali dates, filters, SQL safety, statistics, CSV export,
activity log with IP, and the one-time grant of new permissions to the built-in roles.

No real MySQL: the routes get a fake CdrClient that records every query and answers with canned rows.
The SQL itself is only checked for shape and parameters here; it is run for real against MariaDB 5.5 on
the PBX (tools/check_pbx.py, then the page itself).
"""
import csv
import io
import os
import sqlite3
import unittest
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import app as m
import features
import i18n
import pbx_cdr
from pbx_cdr import Filters, Rules
from pbx_connectors import ConnectorError
from tests.test_rbac import LEGACY_PASSWORD, AppCase

TEHRAN = ZoneInfo('Asia/Tehran')
RULES = Rules(reception=('100',))
TODAY = date(2026, 11, 8)  # 1405/08/17
CDR_ENV = {'PBX_CDR_HOST': '10.0.0.2', 'PBX_CDR_USER': 'hotel_cdr', 'PBX_CDR_PASS': 'db-test-pw'}


class JalaliTests(unittest.TestCase):
    def test_known_dates(self):
        self.assertEqual(i18n.gregorian_to_jalali(2026, 11, 8), (1405, 8, 17))
        self.assertEqual(i18n.jalali_to_gregorian(1405, 1, 1), (2026, 3, 21))
        self.assertEqual(i18n.gregorian_to_jalali(2025, 3, 20), (1403, 12, 30))  # leap Esfand
        self.assertEqual(i18n.jalali_year(2026, 11, 8), 1405)

    def test_round_trip_over_decades(self):
        day = date(1995, 1, 1)
        while day < date(2045, 1, 1):
            j = i18n.gregorian_to_jalali(day.year, day.month, day.day)
            self.assertEqual(i18n.jalali_to_gregorian(*j), (day.year, day.month, day.day))
            day = date.fromordinal(day.toordinal() + 17)

    def test_validity(self):
        self.assertTrue(i18n.jalali_is_valid(1403, 12, 30))
        self.assertFalse(i18n.jalali_is_valid(1404, 12, 30))
        self.assertFalse(i18n.jalali_is_valid(1405, 7, 31))
        self.assertFalse(i18n.jalali_is_valid(1405, 13, 1))


class ParseDayTests(unittest.TestCase):
    def test_formats(self):
        for text in ('1405/08/17', '1405-8-17', '۱۴۰۵/۰۸/۱۷', ' ١٤٠٥/٠٨/١٧ ', '2026-11-08', '2026/11/08'):
            self.assertEqual(pbx_cdr.parse_day(text), TODAY, text)

    def test_invalid(self):
        for text in ('', None, '1404/12/30', '2026-02-30', '1405/08', 'yesterday', "1405/08/17' OR 1=1", '99999/1/1'):
            self.assertIsNone(pbx_cdr.parse_day(text), text)

    def test_jalali_text(self):
        self.assertEqual(pbx_cdr.jalali_text(TODAY), '1405/08/17')
        self.assertEqual(pbx_cdr.jalali_text(None), '')


class FilterTests(unittest.TestCase):
    def parse(self, **args):
        return pbx_cdr.parse_filters(args, RULES, TODAY)

    def test_default_is_today(self):
        f, warnings = self.parse()
        self.assertEqual((f.day_from, f.day_to, f.page), (TODAY, TODAY, 1))
        self.assertEqual(warnings, [])

    def test_quick_ranges(self):
        self.assertEqual(self.parse(range='yesterday')[0].day_from, date(2026, 11, 7))
        f = self.parse(range='7d')[0]
        self.assertEqual((f.day_from, f.day_to), (date(2026, 11, 2), TODAY))
        self.assertEqual(self.parse(range='30d')[0].day_from, date(2026, 10, 10))

    def test_reversed_range_is_swapped(self):
        f, _ = self.parse(**{'from': '1405/08/17', 'to': '1405/08/10'})
        self.assertEqual((f.day_from, f.day_to), (date(2026, 11, 1), TODAY))

    def test_range_is_capped(self):
        f, warnings = self.parse(**{'from': '1405/01/01', 'to': '1405/08/17'})
        self.assertEqual((f.day_to - f.day_from).days + 1, RULES.max_days)
        self.assertEqual(f.day_to, TODAY)
        self.assertEqual(len(warnings), 1)

    def test_bad_input_falls_back(self):
        f, warnings = self.parse(**{'from': 'nonsense', 'extension': "101' OR '1'='1", 'direction': 'sideways',
                                    'status': 'DROP', 'page': '-4'})
        self.assertEqual((f.day_from, f.extension, f.direction, f.status, f.page), (TODAY, '', '', '', 1))
        self.assertEqual(len(warnings), 2)

    def test_values(self):
        f, warnings = self.parse(extension='۱۰۱', direction='inbound', status='missed', page='3')
        self.assertEqual((f.extension, f.direction, f.status, f.page, warnings), ('101', 'inbound', 'missed', 3, []))
        self.assertEqual(self.parse(page='999999')[0].page, pbx_cdr.max_page(RULES))

    def test_as_args_round_trip(self):
        f, _ = self.parse(**{'from': '1405/08/01', 'to': '1405/08/17', 'extension': '101', 'status': 'busy'})
        again, _ = pbx_cdr.parse_filters(f.as_args(), RULES, TODAY)
        self.assertEqual(again, f)


class SqlTests(unittest.TestCase):
    EVIL = "101'; DROP TABLE cdr; --"

    def test_user_values_are_parameters_only(self):
        f = Filters(TODAY, TODAY, extension='101', direction='internal', status='failed', page=2)
        for sql, params in [pbx_cdr.page_query(f, RULES), pbx_cdr.count_query(f, RULES), pbx_cdr.export_query(f, RULES),
                            *pbx_cdr.stats_queries(f, RULES).values()]:
            self.assertEqual(sql.count('%s'), len(params), sql)
            self.assertNotIn('101', sql)
            self.assertTrue(sql.startswith('SELECT') and ';' not in sql)
            self.assertIn('calldate >= %s AND calldate < %s', sql)  # the range is always there

    def test_extension_filter(self):
        f = Filters(TODAY, TODAY, extension='101')
        _sql, params = pbx_cdr.where_clause(f, RULES)
        self.assertEqual(params[2:], ['101', '101', '%/101-%', '%/101-%'])

    def test_date_bounds_same_zone(self):
        _sql, params = pbx_cdr.where_clause(Filters(date(2026, 11, 1), TODAY), RULES)
        self.assertEqual(params[:2], [datetime(2026, 11, 1), datetime(2026, 11, 9)])

    def test_date_bounds_other_pbx_zone(self):
        rules = Rules(app_tz=TEHRAN, pbx_tz=ZoneInfo('UTC'))
        _sql, params = pbx_cdr.where_clause(Filters(TODAY, TODAY), rules)
        self.assertEqual(params[:2], [datetime(2026, 11, 7, 20, 30), datetime(2026, 11, 8, 20, 30)])
        self.assertEqual(pbx_cdr.format_call_time(datetime(2026, 11, 7, 20, 30), rules), '1405/08/17 00:00:00')

    def test_paging_and_limits(self):
        sql, params = pbx_cdr.page_query(Filters(TODAY, TODAY, page=3), RULES)
        self.assertEqual(params[-2:], [pbx_cdr.PAGE_SIZE, 2 * pbx_cdr.PAGE_SIZE])
        self.assertEqual(pbx_cdr.export_query(Filters(TODAY, TODAY), RULES)[1][-1], RULES.export_rows)

    def test_status_and_direction(self):
        self.assertEqual([pbx_cdr.status_of(d) for d in ('ANSWERED', 'NO ANSWER', 'busy', 'CONGESTION', 'FAILED', 'x', None)],
                         ['answered', 'missed', 'busy', 'failed', 'failed', '', ''])
        rows = [({'dcontext': 'from-trunk', 'dst': '100'}, 'inbound'), ({'dcontext': 'from-internal', 'dst': '205'}, 'internal'),
                ({'dcontext': 'from-internal', 'dst': '09121234567'}, 'outbound'), ({'dcontext': 'ext-local', 'dst': 's'}, 'outbound')]
        for row, expected in rows:
            self.assertEqual(pbx_cdr.direction_of(row, RULES), expected, row)

    def test_load_rules(self):
        rules = pbx_cdr.load_rules({'PBX_CDR_RECEPTION': '100, 101,abc', 'PBX_CDR_MAX_DAYS': '9999', 'PBX_CDR_INTERNAL_PATTERN': '([',
                                    'PBX_TIMEZONE': 'Not/AZone', 'PBX_CDR_INBOUND_CONTEXTS': 'from-pstn'}, TEHRAN)
        self.assertEqual((rules.reception, rules.max_days, rules.internal_pattern, rules.pbx_tz, rules.inbound_contexts),
                         (('100', '101'), 366, Rules.internal_pattern, TEHRAN, ('from-pstn',)))


class SummaryTests(unittest.TestCase):
    def test_summarize(self):
        results = {'totals': [{'calls': 40, 'answered': 30}], 'hours': [{'hour': 9, 'calls': 10}, {'hour': 14, 'calls': 5}],
                   'top': [{'ext': '100', 'calls': 25}], 'reception': [{'calls': 20, 'answered': 19}]}
        s = pbx_cdr.summarize(results, RULES)
        self.assertEqual((s['calls'], s['unanswered'], s['answer_rate'], s['peak_hours']), (40, 10, 75.0, [9]))
        self.assertEqual(s['hours'][14], {'hour': 14, 'calls': 5, 'width': 50})
        self.assertEqual(len(s['hours']), 24)
        self.assertEqual(s['reception'], {'calls': 20, 'answered': 19, 'rate': 95.0})
        self.assertEqual(s['top'], [{'extension': '100', 'calls': 25}])

    def test_no_calls(self):
        s = pbx_cdr.summarize({'totals': [{'calls': 0, 'answered': None}], 'hours': [], 'top': []}, Rules())
        self.assertEqual((s['answer_rate'], s['peak_hours'], s['reception']), (None, [], None))

    def test_ttl_cache(self):
        now = [0.0]
        cache = pbx_cdr.TTLCache(ttl=60, size=2, clock=lambda: now[0])
        cache.put('a', 1)
        now[0] = 59
        self.assertEqual(cache.get('a'), 1)
        now[0] = 61
        self.assertIsNone(cache.get('a'))
        for key in 'bcd':
            cache.put(key, key)
        self.assertIsNone(cache.get('b'))  # oldest dropped at the size limit
        self.assertEqual(pbx_cdr.format_seconds(3725), '1:02:05')
        self.assertEqual(pbx_cdr.format_seconds(65), '1:05')


# ---------------------------------------------------------------------------- pages
SAMPLE_ROWS = [
    {'calldate': datetime(2026, 11, 8, 9, 15, 0), 'src': '+989121234567', 'dst': '100', 'dcontext': 'from-trunk',
     'channel': 'SIP/trunk1-0000001', 'dstchannel': 'SIP/100-0000002', 'lastapp': 'Dial', 'disposition': 'ANSWERED',
     'duration': 75, 'billsec': 65, 'uniqueid': '1762580100.1'},
    {'calldate': datetime(2026, 11, 8, 9, 10, 0), 'src': '205', 'dst': '=1+2', 'dcontext': 'from-internal',
     'channel': 'SIP/205-0000003', 'dstchannel': '', 'lastapp': 'Dial', 'disposition': 'NO ANSWER',
     'duration': 20, 'billsec': 0, 'uniqueid': '1762579800.2'},
]


class FakeCdr:
    def __init__(self, fail=None):
        self.fail = fail
        self.queries = []

    def select(self, sql, params=(), max_rows=1000):
        self.queries.append((sql, list(params), max_rows))
        if self.fail:
            raise self.fail
        if sql.startswith('SELECT COUNT(*)'):
            return [{'n': 2}]
        if 'COUNT(DISTINCT uniqueid) AS calls, COUNT' in sql:
            return [{'calls': 2, 'answered': 1}]
        if 'HOUR(calldate)' in sql:
            return [{'hour': 9, 'calls': 2}]
        if 'AS legs' in sql:
            return [{'ext': '100', 'calls': 1}, {'ext': '205', 'calls': 1}]
        return list(SAMPLE_ROWS)[:max_rows]


class CallPageCase(AppCase):
    def setUp(self):
        super().setUp()
        m._cdr_stats_cache.clear()
        self.addCleanup(m._cdr_stats_cache.clear)
        env = mock.patch.dict(os.environ, CDR_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.fake = FakeCdr()
        patcher = mock.patch.object(m, 'make_cdr_client', lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            roles = {r.key: r.id for r in m.Role.query.all()}
            for name, role in (('boss', features.SUPER_ADMIN), ('op', 'operator'), ('view', 'viewer')):
                user = m.User(username=name, display_name=name, role_id=roles[role])
                user.set_password(LEGACY_PASSWORD)
                m.db.session.add(user)
            m.db.session.commit()

    def client(self, name='boss'):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client

    def url(self, path=''):
        return f'/{self.prefix}/calls{path}'


class CallPageTests(CallPageCase):
    def test_report_shows_rows_and_statistics(self):
        page = self.client().get(self.url('?from=1405/08/17&to=1405/08/17')).get_data(as_text=True)
        self.assertIn('1405/08/17 09:15:00', page)
        self.assertIn('+989121234567', page)
        self.assertIn('50.0%', page)            # 1 of 2 calls answered
        self.assertIn('گزارش تماس‌ها', page)
        self.assertIn('⬇', page)                # super admin may export
        first = self.fake.queries[0]
        self.assertEqual(first[1][:2], [datetime(2026, 11, 8), datetime(2026, 11, 9)])

    def test_statistics_are_cached_between_pages(self):
        client = self.client()
        client.get(self.url('?range=today'))
        first = len(self.fake.queries)
        client.get(self.url('?range=today&page=2'))
        self.assertEqual(len(self.fake.queries) - first, 2)  # count + rows only

    def test_pbx_error_is_shown_in_persian(self):
        self.fake.fail = ConnectorError('unreachable', 'cannot connect to 10.0.0.2:3306', 'cdr')
        response = self.client().get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('در دسترس نیست', response.get_data(as_text=True))

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {'PBX_CDR_HOST': ''}):
            page = self.client().get(self.url()).get_data(as_text=True)
        self.assertIn('هنوز تنظیم نشده', page)
        self.assertEqual(self.fake.queries, [])

    def test_injection_attempt_never_reaches_the_sql(self):
        self.client().get(self.url("?extension=101'%20OR%201=1--&direction=x"))
        for sql, params, _max in self.fake.queries:
            self.assertNotIn("OR 1=1", sql + str(params))

    def test_operator_sees_report_without_export(self):
        client = self.client('op')
        page = client.get(self.url()).get_data(as_text=True)
        self.assertNotIn('⬇', page)
        self.assertEqual(client.get(self.url('/export.csv')).status_code, 403)
        self.assertEqual(self.client('view').get(self.url()).status_code, 403)


class CallExportTests(CallPageCase):
    def test_csv_is_safe_and_logged_with_ip(self):
        response = self.client().get(self.url('/export.csv?from=1405/08/17&to=1405/08/17&status=answered'),
                                     environ_base={'REMOTE_ADDR': '10.20.30.40'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        rows = list(csv.reader(io.StringIO(response.get_data().decode('utf-8-sig'))))
        self.assertEqual(rows[0][:4], ['time', 'time_jalali', 'src', 'dst'])
        self.assertEqual(rows[1][:4], ['2026/11/08 09:15:00', '1405/08/17 09:15:00', "'+989121234567", '100'])
        self.assertEqual(rows[2][3], "'=1+2")  # a formula is never live in Excel
        with self.app.app_context():
            log = m.ActivityLog.query.filter_by(entity_type='calls').one()
        self.assertEqual((log.action, log.actor, log.ip), ('exported', 'boss', '10.20.30.40'))
        self.assertIn('1405/08/17', log.entity_label)
        self.assertIn('2 ردیف', log.details)
        self.assertNotIn('989121234567', log.entity_label + log.details)  # no phone numbers in the log

    def test_export_failure_redirects_with_message(self):
        self.fake.fail = ConnectorError('auth', 'Access denied', 'cdr')
        client = self.client()
        response = client.get(self.url('/export.csv?range=7d'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/calls?', response.headers['Location'])
        page = client.get(response.headers['Location']).get_data(as_text=True)
        self.assertIn('خروجی گرفته نشد', page)
        with self.app.app_context():
            self.assertEqual(m.ActivityLog.query.filter_by(entity_type='calls').count(), 0)


class ActivityIpTests(AppCase):
    def test_old_activity_table_gets_the_ip_column(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('CREATE TABLE activity_log (id INTEGER PRIMARY KEY, actor VARCHAR(120) NOT NULL, action VARCHAR(30) NOT NULL, '
                     'entity_type VARCHAR(30) NOT NULL, entity_label VARCHAR(200) NOT NULL, details VARCHAR(300), created_at DATETIME NOT NULL)')
        conn.execute("INSERT INTO activity_log (actor, action, entity_type, entity_label, created_at) VALUES ('x', 'created', 'extension', 'old', '2026-01-01')")
        conn.commit()
        conn.close()
        self.start()
        with self.app.app_context():
            old = m.ActivityLog.query.filter_by(entity_label='old').one()
            self.assertIsNone(old.ip)


class MigratedDefaultsTests(AppCase):
    def operator_keys(self):
        with self.app.app_context():
            role = m.Role.query.filter_by(key='operator').one()
            return {p.permission_key for p in role.permissions}

    def grant_logs(self):
        with self.app.app_context():
            return m.ActivityLog.query.filter(m.ActivityLog.details.like('دسترسی پیش‌فرض تازه:%')).count()

    def test_fresh_install_has_it_without_log(self):
        self.start()
        self.assertIn('calls.view', self.operator_keys())
        self.assertEqual(self.grant_logs(), 0)
        with self.app.app_context():
            self.assertEqual({g.permission_key for g in m.RoleDefaultGrant.query.all()}, set(features.MIGRATED_DEFAULTS))

    def test_existing_roles_get_new_keys_once(self):
        self.start()
        with self.app.app_context():  # simulate a database from before this release
            role = m.Role.query.filter_by(key='operator').one()
            role.permissions = [p for p in role.permissions if p.permission_key != 'calls.view']
            m.RoleDefaultGrant.query.delete()
            m.db.session.commit()
        self.start()  # service restart with the new version
        self.assertIn('calls.view', self.operator_keys())
        self.assertEqual(self.grant_logs(), 1)
        with self.app.app_context():  # an admin takes it away again
            role = m.Role.query.filter_by(key='operator').one()
            role.permissions = [p for p in role.permissions if p.permission_key != 'calls.view']
            m.db.session.commit()
        self.start()
        self.assertNotIn('calls.view', self.operator_keys())  # never added back
        self.assertEqual(self.grant_logs(), 1)

    def test_viewer_is_not_touched(self):
        self.start()
        with self.app.app_context():
            viewer = m.Role.query.filter_by(key='viewer').one()
            self.assertNotIn('calls.view', {p.permission_key for p in viewer.permissions})
        self.assertEqual(features.default_roles_for('calls.view'), ('operator',))


if __name__ == '__main__':
    unittest.main()
