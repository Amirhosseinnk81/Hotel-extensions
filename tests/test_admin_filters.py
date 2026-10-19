"""Tests for the extra filters in the admin search bar (department, active, phone state, MAC)."""
import unittest
from datetime import datetime, timedelta

import app as m
from tests.test_pbx_foundation import AppTestCase


class AdminFilterTests(AppTestCase):
    def setUp(self):
        self.make_app()
        with self.app.app_context():
            front = m.Department(name='تست-الف')
            hk = m.Department(name='تست-ب')
            m.db.session.add_all([front, hk])
            m.db.session.commit()
            self.front_id, self.hk_id = front.id, hk.id
        now = datetime.utcnow()
        self.add('101', 'Front desk', department_id=self.front_id, mac='00:15:65:AA:BB:01',
                 reg_status='online', rtt_ms=20, status_updated_at=now)
        self.add('102', 'Front slow', department_id=self.front_id,
                 reg_status='online', rtt_ms=m.PBX_SLOW_MS + 50, status_updated_at=now)
        self.add('103', 'Housekeeping', department_id=self.hk_id, is_active=False,
                 reg_status='offline', status_updated_at=now)
        self.add('104', 'No dept', reg_status='online', status_updated_at=now - timedelta(minutes=m.PBX_STATUS_STALE_MINUTES + 5))
        self.add('105', 'No PBX data')

    def found(self, **params):
        resp = self.admin.get(f'/{self.prefix}', query_string=params)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        return {e for e in ('101', '102', '103', '104', '105') if f'data-ext="{e}"' in html}

    def test_department(self):
        self.assertEqual(self.found(department=self.front_id), {'101', '102'})
        self.assertEqual(self.found(department='none'), {'104', '105'})

    def test_active(self):
        self.assertEqual(self.found(active='0'), {'103'})
        self.assertEqual(self.found(active='1'), {'101', '102', '104', '105'})

    def test_mac_state(self):
        self.assertEqual(self.found(mac_state='has'), {'101'})
        self.assertEqual(self.found(mac_state='missing'), {'102', '103', '104', '105'})

    def test_phone_state(self):
        self.assertEqual(self.found(phone='online'), {'101', '102'})
        self.assertEqual(self.found(phone='slow'), {'102'})
        self.assertEqual(self.found(phone='offline'), {'103'})
        self.assertEqual(self.found(phone='unknown'), {'104'})
        self.assertEqual(self.found(phone='none'), {'105'})

    def test_combined_and_invalid_values(self):
        self.assertEqual(self.found(department=self.front_id, phone='slow'), {'102'})
        self.assertEqual(self.found(q='Front', mac_state='has'), {'101'})
        # garbage values are ignored instead of raising or hiding everything
        self.assertEqual(self.found(department='abc', active='x', phone='zzz', mac_state='?'), {'101', '102', '103', '104', '105'})


if __name__ == '__main__':
    unittest.main()
