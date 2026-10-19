"""Keep the test run independent of the shell it is started from.

A development machine usually has the real PBX_* variables set (Issabel address and login). With them
the tests would believe the PBX is configured, start background sync threads against the real server
and leave the runner lock busy for the next test. Tests that need PBX settings set them themselves
(mock.patch.dict), so every PBX_* variable is removed before anything else is imported.

The same goes for a real .env file next to app.py: the loader in envfile.py would put those values back
the moment `app` is imported, so it is switched off here (PBX_DOTENV=0).
"""
import os

for _name in [name for name in os.environ if name.startswith('PBX_')]:
    del os.environ[_name]

os.environ['PBX_DOTENV'] = '0'  # after the loop above, which removes every PBX_* name
