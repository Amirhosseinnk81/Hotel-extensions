"""SSH link as an alternative to AMI (pbx_connectors.SshRunner / SshReader / SshWriter / SshPeerSource,
tools/hotel-pbx.sh) and the phone web-config button.

The Windows ssh client is replaced by a fake subprocess.run that answers like tools/hotel-pbx.sh would,
so everything from the command line options to the parsing is exercised. The script itself is run with
Git Bash against a fake `asterisk` binary that only logs what it was asked (skipped without Git Bash).
"""
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import app as m
import features
import pbx_connectors as pc
import pbx_daynight
import pbx_dnd
import pbx_snapshot
from tests.test_pbx_connectors import load_tool
from tests.test_pbx_foundation import AppTestCase
from tests.test_rbac import LEGACY_PASSWORD, AppCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RO_KEY, RW_KEY, KNOWN = r'C:\keys\pbx_ro', r'C:\keys\pbx_rw', r'C:\keys\known_hosts'
SSH_ENV = {'PBX_LINK': 'ssh', 'PBX_SSH_HOST': '10.0.0.9', 'PBX_SSH_USER': 'hotelpbx', 'PBX_SSH_KEY': RO_KEY,
           'PBX_SSH_WRITE_KEY': RW_KEY, 'PBX_SSH_KNOWN_HOSTS': KNOWN, 'PBX_SSH_MAX_SECONDS': '10'}
SIP_PEERS = """driver=sip
Name/username             Host                                    Dyn Forcerport Comedia    ACL Port     Status      Description
100/100                   192.168.225.66                           D  Yes        Yes            A  5060     OK (3 ms)
201/201                   192.168.224.36                           D  Yes        Yes            A  5060     OK (12 ms)
202/202                   (Unspecified)                            D  Yes        Yes            A  0        UNKNOWN
main-trunk                10.0.0.1                                    Yes        Yes               5060     OK (20 ms)
4 sip peers [Monitored: 3 online, 1 offline Unmonitored: 0 online, 0 offline]"""
SIP_REGISTRY = """driver=sip
Host                                    dnsmgr Username       Refresh State                Reg.Time
sip.provider.example:5060               N      trunk1             105 Registered           Wed, 11 Nov 2026 10:00:00
10.1.1.1:5060                           N      trunk2             120 Rejected
2 SIP registrations."""
SYSTEM = """cpu=12
mem_MemTotal=8000000
mem_MemFree=1000000
mem_MemAvailable=6000000
mem_SwapTotal=2000000
mem_SwapFree=2000000
disk=/ 45
disk=/var/spool/asterisk 91
service=asterisk active
service=mariadb active
service=httpd inactive"""


class FakePbx:
    """Answers like tools/hotel-pbx.sh, through a fake subprocess.run."""

    def __init__(self):
        self.astdb = {('DND', '201'): 'YES', ('CF', '305'): '09121234567', ('DAYNIGHT', 'C0'): 'DAY'}
        self.requests = []   # (request, key file)
        self.ssh_failure = None  # (returncode, stderr)

    def __call__(self, cmd, **kwargs):
        key, request = cmd[cmd.index('-i') + 1], cmd[-1]
        self.requests.append((request, key))
        if self.ssh_failure:
            code, stderr = self.ssh_failure
            return SimpleNamespace(returncode=code, stdout='', stderr=stderr)
        words = request.split()
        out, code = self.answer(words, key == RW_KEY)
        return SimpleNamespace(returncode=code, stdout=out, stderr='')

    def answer(self, words, rw):
        command, args = words[0], words[1:]
        if command == 'astdb':
            lines = []
            for family in args:
                if family not in pc.SSH_FAMILIES:
                    return 'ERR family not allowed\n', 2
                lines.append(f'== {family} ==')
                rows = [(k, v) for (f, k), v in self.astdb.items() if f == family]
                lines += [f'/{family}/{k:<40}: {v}' for k, v in rows] + [f'{len(rows)} results found.']
            return '\n'.join(lines) + '\n', 0
        if command == 'core':
            return 'System uptime: 3600\nLast reload: 600\n2 active channels\n1 active call\n10 calls processed\n', 0
        if command == 'driver':
            return 'driver=sip\n', 0
        if command == 'peers':
            return SIP_PEERS + '\n', 0
        if command == 'registry':
            return SIP_REGISTRY + '\n', 0
        if command == 'system':
            return SYSTEM + '\n', 0
        if command in ('daynight-set', 'probe-set') and not rw:
            return 'ERR not allowed with this key\n', 2
        if command == 'daynight-set':
            self.astdb[('DAYNIGHT', f'C{args[0]}')] = args[1]
            return 'OK state\n' + ('OK blf\n' if args[2] == '1' else ''), 0
        if command == 'probe-set':
            self.astdb[('HOTELTEST', 'probe')] = args[0]
            return 'OK probe\n', 0
        return 'ERR command not allowed\n', 2


def ssh_settings(**extra):
    return pc.load_settings({**SSH_ENV, **extra})


class FakeSubprocessCase(unittest.TestCase):
    def setUp(self):
        self.pbx = FakePbx()
        patcher = mock.patch.object(pc.subprocess, 'run', self.pbx)
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------- connector
class RunnerTests(FakeSubprocessCase):
    def test_command_line_is_strict(self):
        cmd = pc.SshRunner(ssh_settings().ssh).command('ping')
        for option in ('BatchMode=yes', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes', f'UserKnownHostsFile={KNOWN}'):
            self.assertIn(option, cmd)
        self.assertEqual((cmd[cmd.index('-i') + 1], cmd[-2], cmd[-1]), (RO_KEY, 'hotelpbx@10.0.0.9', 'ping'))
        self.assertEqual(pc.SshRunner(ssh_settings().ssh).command('ping', write=True)[2], RW_KEY)

    def test_odd_requests_never_leave_the_app(self):
        runner = pc.SshRunner(ssh_settings().ssh)
        for bad in ('astdb DND; rm -rf /', 'ping && id', '$(id)', 'core\nid', 'x' * 201, '', "astdb 'DND'"):
            with self.assertRaises(ValueError):
                runner.run(bad)
        self.assertEqual(self.pbx.requests, [])

    def test_errors_are_classified(self):
        runner = pc.SshRunner(ssh_settings().ssh)
        cases = (('Host key verification failed.', 'hostkey'), ('hotelpbx@10.0.0.9: Permission denied (publickey).', 'auth'),
                 ('ssh: connect to host 10.0.0.9 port 22: Connection timed out', 'timeout'),
                 ('ssh: connect to host 10.0.0.9 port 22: Connection refused', 'unreachable'), ('weird', 'protocol'))
        for stderr, kind in cases:
            self.pbx.ssh_failure = (255, stderr)
            with self.assertRaises(pc.ConnectorError) as ctx:
                runner.run('ping')
            self.assertEqual(ctx.exception.kind, kind, stderr)
        self.pbx.ssh_failure = None
        with self.assertRaises(pc.ConnectorError) as ctx:
            runner.run('daynight-set 0 NIGHT 1')  # read-only key
        self.assertEqual(ctx.exception.kind, 'permission')
        self.assertIn('PBX_SSH_KNOWN_HOSTS', pc.FA_MESSAGES['hostkey'])

    def test_missing_client_and_timeout(self):
        runner = pc.SshRunner(ssh_settings().ssh)
        with mock.patch.object(pc.subprocess, 'run', side_effect=FileNotFoundError()):
            with self.assertRaises(pc.ConnectorError) as ctx:
                runner.run('ping')
        self.assertEqual(ctx.exception.kind, 'dependency')
        with mock.patch.object(pc.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 10)):
            with self.assertRaises(pc.ConnectorError) as ctx:
                runner.run('ping')
        self.assertEqual(ctx.exception.kind, 'timeout')

    def test_not_configured(self):
        with self.assertRaises(pc.ConnectorError) as ctx:
            pc.SshRunner(pc.load_settings({}).ssh).run('ping')
        self.assertEqual(ctx.exception.kind, 'config')
        with self.assertRaises(pc.ConnectorError):
            pc.SshRunner(ssh_settings(PBX_SSH_WRITE_KEY='').ssh).run('probe-set T1', write=True)


class ReaderTests(FakeSubprocessCase):
    def test_link_choice(self):
        ami = {'PBX_AMI_HOST': 'h', 'PBX_AMI_USER': 'u', 'PBX_AMI_SECRET': 's'}
        self.assertEqual(pc.load_settings({**SSH_ENV, 'PBX_LINK': 'auto'}).link, 'ssh')
        self.assertEqual(pc.load_settings({**SSH_ENV, **ami, 'PBX_LINK': 'auto'}).link, 'ami')
        self.assertEqual(pc.load_settings({**SSH_ENV, **ami}).link, 'ssh')       # PBX_LINK=ssh wins
        self.assertIsNone(pc.load_settings({**ami, 'PBX_LINK': 'ssh'}).link)     # wanted but not configured
        self.assertEqual(pc.load_settings({'PBX_SSH_USER': 'u', 'PBX_SSH_KEY': 'k', 'PBX_SSH_KNOWN_HOSTS': 'kh',
                                           'PBX_AMI_HOST': '10.1.1.1'}).ssh.host, '10.1.1.1')
        self.assertIsInstance(pc.open_reader(ssh_settings()), pc.SshReader)
        self.assertIsInstance(pc.open_writer(ssh_settings()), pc.SshWriter)
        self.assertTrue(ssh_settings().writer_ready)
        self.assertFalse(ssh_settings(PBX_SSH_WRITE_KEY='').writer_ready)
        with self.assertRaises(pc.ConnectorError):
            pc.open_reader(pc.load_settings({}))

    def test_dnd_snapshot_is_one_call(self):
        with pc.open_reader(ssh_settings()) as reader:
            data = pbx_dnd.read_snapshot(reader, ['201', '305', '100'])
        self.assertEqual(data['flags'], {'201': {'dnd': True}, '305': {'cf': '09121234567'}})
        self.assertEqual([r for r, _k in self.pbx.requests], ['astdb DND CF CFB CFU'])

    def test_core_registry_peers_system(self):
        with pc.open_reader(ssh_settings()) as reader:
            core = reader.core_status()
            driver, events = reader.registry()
            names = reader.peer_names()
            system = reader.system()
            self.assertEqual(reader.db_get('DAYNIGHT', 'C0'), 'DAY')
            self.assertIsNone(reader.db_get('DAYNIGHT', 'C5'))
            with self.assertRaises(pc.ConnectorError):
                reader.db_get('AMPUSER', '101')  # not one of the families the script serves
        self.assertEqual(core['current_calls'], 1)
        self.assertTrue(core['startup'])
        self.assertEqual((driver, [(e['Username'], e['State']) for e in events]), ('sip', [('trunk1', 'Registered'), ('trunk2', 'Rejected')]))
        self.assertIn('201', names)
        self.assertEqual((system['cpu'], system['memory'], system['swap']), (12.0, 25.0, 0.0))
        self.assertEqual(system['disks'][1], {'mount': '/var/spool/asterisk', 'percent': 91.0})
        self.assertEqual(system['services'], {'asterisk': 'running', 'mysql': 'running', 'apache': 'stopped'})
        self.assertTrue(all(key == RO_KEY for _r, key in self.pbx.requests))  # reads never use the write key

    def test_pjsip_registry(self):
        events = pc.parse_registry_text('pjsip', ' <Registration/ServerURI....>  <Auth....>  <Status....>\n'
                                                 ' itsp/sip:sip.provider.example:5060   itsp-auth   Unregistered\n')
        self.assertEqual(events, [{'Event': 'OutboundRegistrationDetail', 'ObjectName': 'itsp', 'Status': 'Unregistered'}])

    def test_writer(self):
        writer = pc.open_writer(ssh_settings())
        self.assertEqual(writer.set_daynight(0, 'NIGHT', devstate=True), 2)
        self.assertEqual(self.pbx.astdb[('DAYNIGHT', 'C0')], 'NIGHT')
        self.assertEqual(self.pbx.requests[-1], ('daynight-set 0 NIGHT 1', RW_KEY))
        with self.assertRaises(ValueError):
            writer.set_daynight(12, 'NIGHT')
        self.assertEqual(writer.set_probe('T123'), 1)

    def test_writer_reports_a_missing_blf(self):
        writer = pc.SshWriter(ssh_settings(), runner=lambda request, write=False: 'OK state\nWARN blf: no\n')
        with self.assertRaises(pc.ConnectorError) as ctx:
            writer.set_daynight(0, 'DAY', devstate=True)
        self.assertEqual(ctx.exception.completed, 1)  # the state itself was written
        writer = pc.SshWriter(ssh_settings(), runner=lambda request, write=False: 'nothing\n')
        with self.assertRaises(pc.ConnectorError) as ctx:
            writer.set_daynight(0, 'DAY', devstate=False)
        self.assertEqual(ctx.exception.completed, 0)

    def test_peer_source_errors_are_ami_errors(self):
        source = pc.SshPeerSource(ssh_settings())
        self.assertEqual(source.fetch_peer_states()['201'].state, 'online')
        self.pbx.ssh_failure = (255, 'Connection refused')
        with self.assertRaises(pc.AMIError):
            source.fetch_peer_states()


# ---------------------------------------------------------------------------- the app over SSH
class SshAppCase(AppCase):
    EXTRA = {}

    def setUp(self):
        super().setUp()
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.pbx = FakePbx()
        for patcher in (mock.patch.object(pc.subprocess, 'run', self.pbx),
                        mock.patch.dict(os.environ, {**SSH_ENV, 'PBX_DAYNIGHT_ALLOWED': '0', 'PBX_DAYNIGHT_WRITE_ENABLED': '1', **self.EXTRA})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            for ext, title in (('201', 'Room 201'), ('305', 'Room 305'), ('100', 'Reception')):
                m.db.session.add(m.Extension(extension=ext, title=title, kind='room' if ext != '100' else 'staff'))
            role = m.Role.query.filter_by(key=features.SUPER_ADMIN).one()
            user = m.User(username='boss', display_name='boss', role_id=role.id)
            user.set_password(LEGACY_PASSWORD)
            m.db.session.add(user)
            m.db.session.commit()

    def client(self):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': 'boss', 'password': LEGACY_PASSWORD})
        return client


class SshAppTests(SshAppCase):
    def test_dnd_page_over_ssh(self):
        self.assertEqual(pbx_snapshot.refresh(pbx_dnd.SNAPSHOT_NAME, force=True).state, 'ok')
        page = self.client().get(f'/{self.prefix}/dnd').get_data(as_text=True)
        self.assertIn('Room 201', page)
        self.assertIn('09121234567', page)
        self.assertEqual([r for r, _k in self.pbx.requests], ['astdb DND CF CFB CFU'])

    def test_day_night_switch_over_ssh(self):
        pbx_snapshot.refresh(pbx_daynight.SNAPSHOT_NAME, force=True)
        client = self.client()
        response = client.post(f'/{self.prefix}/daynight/0/switch', data={'expected': 'DAY', 'target': 'NIGHT'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.pbx.astdb[('DAYNIGHT', 'C0')], 'NIGHT')
        writes = [(r, k) for r, k in self.pbx.requests if r.startswith('daynight-set')]
        self.assertEqual(writes, [('daynight-set 0 NIGHT 1', RW_KEY)])
        with self.app.app_context():
            self.assertEqual(m.ActivityLog.query.filter_by(entity_type='daynight', action='switched').count(), 1)

    def test_health_reads_cpu_and_disk_from_the_pbx(self):
        reading = m.read_health()
        self.assertTrue(reading['ami_ok'])
        self.assertEqual(reading['link'], 'ssh')
        self.assertEqual((reading['dashboard']['cpu'], reading['asterisk']), (12.0, 'running'))
        self.assertEqual([t['registered'] for t in reading['trunks']], [True, False])
        self.assertIsNone(reading['web_error'])  # the web dashboard was not needed

    def test_check_pbx_and_write_test_over_ssh(self):
        tool = load_tool('check_pbx')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tool.main(['--dbget', '5', '--test-daynight-write'])
        text = out.getvalue()
        self.assertIn('SSH        OK  hotelpbx@10.0.0.9:22', text)
        self.assertIn('CPU 12.0%', text)
        self.assertIn('Write test OK', text)
        self.assertTrue(self.pbx.astdb[('HOTELTEST', 'probe')].startswith('T'))
        self.assertEqual(self.pbx.astdb[('DAYNIGHT', 'C0')], 'DAY')
        self.assertNotIn(RW_KEY, text)


class SshPhoneSyncTests(SshAppCase):
    EXTRA = {'PBX_SOURCE': 'ssh', 'PBX_SYNC_AUTOSTART': '0'}

    def test_phone_states_over_ssh(self):
        self.assertEqual(m.pbx_config()['source'], 'ssh')
        with self.app.app_context():
            summary = m.sync_pbx_once()
            entry = m.Extension.query.filter_by(extension='201').one()
        self.assertEqual((entry.reg_status, entry.rtt_ms), ('online', 12))
        self.assertEqual(summary['ignored'], 1)  # main-trunk

    def test_lost_ssh_is_pbx_lost_not_a_crash(self):
        self.pbx.ssh_failure = (255, 'ssh: connect to host 10.0.0.9 port 22: Connection refused')
        m.pbx_sync_state.update(last_error=None, error_since=None)
        with self.assertLogs(self.app.logger, 'WARNING'):
            ok, message = m.run_pbx_sync(self.app)
        self.assertFalse(ok)
        self.assertIn('ssh unreachable', message)
        self.assertIsNotNone(m.pbx_sync_state['error_since'])
        m.pbx_sync_state.update(last_error=None, error_since=None)


# ---------------------------------------------------------------------------- the PBX-side script
def git_bash():
    for candidate in (r'C:\Program Files\Git\bin\bash.exe', r'C:\Program Files\Git\usr\bin\bash.exe', shutil.which('bash') or ''):
        if candidate and os.path.isfile(candidate) and 'system32' not in candidate.lower():
            return candidate
    return None


def posix(path):
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return ('/' + drive[0].lower() if drive else '') + rest.replace('\\', '/')


FAKE_ASTERISK = r"""#!/bin/bash
echo "$2" >> "$HOTEL_FAKE_LOG"
case "$2" in
  "database put"*) echo "Updated database successfully" ;;
  "devstate change"*) echo "Changing ${3:-} to INUSE" ;;
  "database show DND") echo "/DND/201                                         : YES"; echo "1 results found." ;;
  "database show "*) echo "0 results found." ;;
  "module show like chan_sip.so") printf 'Module Description\nchan_sip.so  Session Initiation Protocol\n1 modules loaded\n' ;;
  "module show like res_pjsip.so") echo "0 modules loaded" ;;
  *) echo "ok" ;;
esac
"""


@unittest.skipUnless(git_bash(), 'Git Bash is needed to run tools/hotel-pbx.sh')
class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = os.path.join(self.tmp, 'asterisk')
        with open(self.fake, 'w', newline='\n') as handle:
            handle.write(FAKE_ASTERISK)
        self.log = os.path.join(self.tmp, 'calls.log')
        open(self.log, 'w').close()

    def run_script(self, request, mode='ro'):
        env = {**os.environ, 'SSH_ORIGINAL_COMMAND': request, 'HOTEL_PBX_ASTERISK': posix(self.fake), 'HOTEL_FAKE_LOG': posix(self.log)}
        done = subprocess.run([git_bash(), posix(os.path.join(ROOT, 'tools', 'hotel-pbx.sh')), mode], capture_output=True, text=True,
                              env=env, timeout=30)
        with open(self.log) as handle:
            calls = [line.strip() for line in handle if line.strip()]
        open(self.log, 'w').close()
        return done.stdout, calls

    def test_reads(self):
        self.assertEqual(self.run_script('ping')[0].strip(), 'OK hotel-pbx ro')
        out, calls = self.run_script('astdb DND CF')
        self.assertIn('== DND ==', out)
        self.assertIn('/DND/201', out)
        self.assertEqual(calls, ['database show DND', 'database show CF'])
        out, _calls = self.run_script('driver')
        self.assertEqual(out.strip(), 'driver=sip')

    def test_refusals_never_reach_asterisk(self):
        for request in ('ping; rm -rf /', '$(id)', 'astdb SIP', 'astdb ../etc', 'reboot', '', 'core extra', 'astdb'):
            out, calls = self.run_script(request)
            self.assertTrue(out.startswith('ERR '), (request, out))
            self.assertEqual(calls, [], request)
        out, calls = self.run_script('daynight-set 0 NIGHT 1', mode='ro')
        self.assertEqual((out.strip(), calls), ('ERR not allowed with this key', []))
        out, _ = self.run_script('ping', mode='admin')
        self.assertTrue(out.startswith('ERR bad mode'))

    def test_day_night_write(self):
        out, calls = self.run_script('daynight-set 0 NIGHT 1', mode='rw')
        self.assertEqual(out.split(), ['OK', 'state', 'OK', 'blf'])
        self.assertEqual(calls, ['database put DAYNIGHT C0 NIGHT', 'devstate change Custom:DAYNIGHT0 INUSE'])
        out, calls = self.run_script('daynight-set 3 DAY 0', mode='rw')
        self.assertEqual((out.strip(), calls), ('OK state', ['database put DAYNIGHT C3 DAY']))
        for bad in ('daynight-set 10 NIGHT 1', 'daynight-set 0 night 1', 'daynight-set 0 NIGHT 2', 'daynight-set 0 NIGHT'):
            out, calls = self.run_script(bad, mode='rw')
            self.assertTrue(out.startswith('ERR'), bad)
            self.assertEqual(calls, [], bad)
        out, calls = self.run_script('probe-set T123', mode='rw')
        self.assertEqual((out.strip(), calls), ('OK probe', ['database put HOTELTEST probe T123']))


# ---------------------------------------------------------------------------- phone web config button
class PhoneWebButtonTests(AppTestCase):
    def edit_page(self, number):
        entry_id = self.get_ext(number)['id']
        return self.admin.get(self.url(f'/extensions/{entry_id}/edit')).get_data(as_text=True)

    def test_button_opens_the_phone_ip(self):
        self.add('201', 'Room 201', kind='room', last_ip='192.168.224.36')
        page = self.edit_page('201')
        self.assertIn('href="http://192.168.224.36/" class="btn btn-outline-primary phone-web-btn" target="_blank" rel="noopener noreferrer"', page)
        with mock.patch.dict(os.environ, {'PHONE_WEB_SCHEME': 'https'}):
            self.assertIn('href="https://192.168.224.36/"', self.edit_page('201'))

    def test_no_button_without_a_usable_ip(self):
        self.add('202', 'Room 202', kind='room')
        self.add('203', 'Room 203', kind='room', last_ip='javascript:alert(1)')
        for number in ('202', '203'):
            self.assertNotIn('phone-web-btn', self.edit_page(number))

    def test_helper(self):
        self.assertEqual(m.phone_web_url('fe80::1'), 'http://[fe80::1]/')
        for bad in (None, '', '0.0.0.0', '127.0.0.1', '224.0.0.1', '192.168.1.5/admin', 'phone.local'):
            self.assertIsNone(m.phone_web_url(bad), bad)


if __name__ == '__main__':
    unittest.main()
