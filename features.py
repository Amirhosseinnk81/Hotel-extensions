"""Central registry of everything a user can be allowed to do (RBAC).

Every feature has a key of the form "feature.action" (e.g. outages.view). Roles are sets of these
keys, users may get extra grants or denies on top of their role, and the built-in Super Admin role
always has every key — including features registered in the future.

Adding a feature is one register_feature(...) line below; the route then uses
@require_permission('<key>') and templates check can('<key>'). The menu and the permission
form are built from this registry, so nothing else has to be listed by hand.

This module is plain Python (no Flask import) so it can be validated and tested on its own.
Mistakes (duplicate key, bad format, unknown group, unknown key in a default role) raise
ValueError at import time, i.e. the service refuses to start instead of running half-protected.
"""
import re
from collections import namedtuple

KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')

# Display order of the groups in the admin menu and in the permission form.
GROUPS = ('دفترچه', 'پایش', 'سیستم')

Feature = namedtuple('Feature', 'key label group description dangerous menu_item')
# endpoint: Flask endpoint the menu links to; nav: the `nav` value the page template sets (active item);
# label: menu text (defaults to the feature label); order: position inside its group.
MenuItem = namedtuple('MenuItem', 'endpoint icon nav label order', defaults=(None, None, 100))

FEATURES = {}  # key -> Feature, in registration order


def register_feature(key, label, group, description='', dangerous=False, menu_item=None):
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ValueError(f'Invalid feature key {key!r}: expected lowercase "feature.action", e.g. "outages.view"')
    if key in FEATURES:
        raise ValueError(f'Feature {key!r} is registered twice')
    if group not in GROUPS:
        raise ValueError(f'Feature {key!r}: unknown group {group!r} (add it to GROUPS first)')
    if not label:
        raise ValueError(f'Feature {key!r} needs a label')
    if menu_item is not None and not isinstance(menu_item, MenuItem):
        raise ValueError(f'Feature {key!r}: menu_item must be a MenuItem')
    FEATURES[key] = Feature(key, label, group, description, bool(dangerous), menu_item)
    return FEATURES[key]


def is_known(key):
    return key in FEATURES


def known_keys(keys):
    """Only the keys that exist in the registry; stale keys left in the database are silently dropped."""
    return {key for key in keys if key in FEATURES}


def grouped():
    """[(group, [Feature, ...]), ...] in GROUPS order, features in registration order; empty groups left out."""
    result = []
    for group in GROUPS:
        items = [f for f in FEATURES.values() if f.group == group]
        if items:
            result.append((group, items))
    return result


def menu():
    """[(group, [(Feature, MenuItem), ...]), ...] for every feature that has a menu item."""
    result = []
    for group, items in grouped():
        entries = sorted(((f, f.menu_item) for f in items if f.menu_item), key=lambda pair: pair[1].order)
        if entries:
            result.append((group, entries))
    return result


def check_menu_endpoints(endpoints):
    """Called once the app's routes exist: every menu item must point at a real endpoint."""
    missing = [f'{f.key} -> {f.menu_item.endpoint}' for f in FEATURES.values()
               if f.menu_item and f.menu_item.endpoint not in endpoints]
    if missing:
        raise ValueError('Menu items point at unknown endpoints: ' + ', '.join(missing))


# ---- Features ------------------------------------------------------------------------------------

BOOK, MONITOR, SYSTEM = GROUPS

register_feature('extensions.view', 'مشاهده‌ی لیست داخلی‌ها', BOOK, 'پنل مدیریت داخلی‌ها، اتاق‌ها و اطلاعات تماس',
                 menu_item=MenuItem('admin', 'list', 'extensions', 'لیست داخلی‌ها', 10))
register_feature('extensions.create', 'افزودن داخلی', BOOK)
register_feature('extensions.edit', 'ویرایش داخلی', BOOK, 'ویرایش، فعال/غیرفعال کردن گروهی')
register_feature('extensions.delete', 'حذف داخلی', BOOK, 'انتقال به سطل بازیافت (تکی و گروهی)')
register_feature('extensions.delete_all', 'حذف همه‌ی داخلی‌ها', BOOK, dangerous=True)
register_feature('extensions.import', 'ایمپورت فایل داخلی‌ها', BOOK, 'افزودن و بازنویسی گروهی از Excel/CSV', dangerous=True)
register_feature('extensions.mac_import', 'ایمپورت MAC تلفن‌ها', BOOK, 'بازنویسی گروهی MAC و مدل تلفن', dangerous=True,
                 menu_item=MenuItem('mac_import', 'upload', 'mac', order=20))
register_feature('extensions.export', 'خروجی کامل داخلی‌ها', BOOK, 'CSV/Excel/PDF همراه با اطلاعات تماس و شبکه')
register_feature('departments.manage', 'مدیریت بخش‌ها', BOOK, 'افزودن، ویرایش و حذف بخش‌ها')

register_feature('pbx.view', 'مشاهده‌ی وضعیت PBX', MONITOR, 'صفحه‌ی PBX و جزئیات زنده‌ی تلفن‌ها (IP، تأخیر)',
                 menu_item=MenuItem('pbx_status', 'phone', 'pbx', 'وضعیت PBX', 10))
register_feature('pbx.sync', 'همگام‌سازی دستی PBX', MONITOR)
register_feature('pbx.review', 'تعیین نوع داخلی‌های جدید PBX', MONITOR, 'پرسنل یا اتاق مهمان')
register_feature('outages.view', 'مشاهده‌ی گزارش قطعی‌ها', MONITOR,
                 menu_item=MenuItem('outages_report', 'activity', 'outages', 'گزارش قطعی‌ها', 20))
register_feature('outages.export', 'خروجی CSV قطعی‌ها', MONITOR)
register_feature('outages.alerts', 'هشدار قطعی در مرورگر', MONITOR, 'زنگوله‌ی هشدار قطعی تلفن‌ها')

register_feature('trash.view', 'مشاهده‌ی سطل بازیافت', SYSTEM,
                 menu_item=MenuItem('trash', 'trash', 'trash', 'سطل بازیافت', 10))
register_feature('trash.restore', 'بازگردانی از سطل بازیافت', SYSTEM)
register_feature('trash.purge', 'حذف دائمی از سطل بازیافت', SYSTEM, dangerous=True)
register_feature('backup.download', 'دانلود بکاپ دیتابیس', SYSTEM, 'فایل کامل دیتابیس، شامل هش رمزهای عبور', dangerous=True)
register_feature('users.manage', 'مدیریت کاربران', SYSTEM, 'افزودن، ویرایش، حذف و تعیین دسترسی کاربران', dangerous=True,
                 menu_item=MenuItem('admin_users', 'users', 'admins', 'کاربران', 20))
register_feature('roles.manage', 'مدیریت نقش‌ها', SYSTEM, 'ساخت و ویرایش نقش‌ها و دسترسی‌هایشان', dangerous=True,
                 menu_item=MenuItem('admin_roles', 'key', 'roles', 'نقش‌ها و دسترسی‌ها', 25))
register_feature('activity_log.view', 'مشاهده‌ی لاگ فعالیت‌ها', SYSTEM,
                 menu_item=MenuItem('activity_log', 'clock', 'activity', 'لاگ فعالیت‌ها', 30))


# ---- Built-in roles -------------------------------------------------------------------------------
# Created on first start only; afterwards their permissions are whatever the admins set in the UI.
# Super Admin has no permission list: it is granted everything in code (see app.user_can).

SUPER_ADMIN = 'super_admin'

SYSTEM_ROLES = (
    (SUPER_ADMIN, 'مدیر ارشد', 'همه‌ی دسترسی‌ها، حتی قابلیت‌های آینده. قابل ویرایش یا حذف نیست.', None),
    ('operator', 'اپراتور', 'کار روزمره: ویرایش داخلی‌ها، بخش‌ها و پایش PBX؛ بدون عملیات حساس و بدون لاگ‌ها.', (
        'extensions.view', 'extensions.edit', 'extensions.delete', 'extensions.export', 'departments.manage',
        'pbx.view', 'pbx.sync', 'pbx.review', 'outages.view', 'outages.export', 'outages.alerts',
        'trash.view', 'trash.restore',
    )),
    ('viewer', 'مشاهده‌گر', 'فقط مشاهده‌ی داخلی‌ها، وضعیت PBX و قطعی‌ها.', (
        'extensions.view', 'pbx.view', 'outages.view', 'outages.alerts',
    )),
)


def _check_system_roles():
    for key, _name, _description, permissions in SYSTEM_ROLES:
        unknown = [p for p in permissions or () if p not in FEATURES]
        if unknown:
            raise ValueError(f'System role {key!r} lists unknown permissions: {", ".join(unknown)}')


_check_system_roles()
