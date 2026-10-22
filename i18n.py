"""Persian/English UI strings.

Persian is the source language: templates and Python code keep their Persian
text, and ``translate()`` swaps it for English when the visitor picked English.
Lookups ignore the zero-width non-joiner (U+200C) so keys never have to be
retyped exactly.  Dynamic messages (counts, extension numbers, ...) are handled
by the PATTERNS list at the bottom.  
"""
import re

LANGS = ('fa', 'en')
DEFAULT_LANG = 'fa'
_ZW = re.compile('[\u200c\u200f\u200e]')
_FA = re.compile('[\u0600-\u06FF]')


def norm(text):
    return _ZW.sub('', text)


EN = {"پنل مدیریت": "Admin panel","لاگ فعالیتها": "Activity log","← بازگشت به مدیریت": "← Back to admin","زمان": "Time","کاربر": "User","عملیات": "Action","مورد": "Item","جزئیات": "Details","ایجاد": "Created","ویرایش": "Edited","حذف": "Deleted","ایمپورت": "Imported","خروجی": "Exported","داخلی": "Extension","بخش": "Department","کاربر مدیر": "Admin user","بکاپ": "Backup","هنوز فعالیتی ثبت نشده است.": "No activity has been recorded yet.","داخلیهای هتل": "Hotel extensions","+ ثبت داخلی جدید": "+ New extension","کل داخلیها": "Total extensions","بخشها": "Departments","فعال": "Active","غیرفعال": "Inactive","اتاق مهمان (مخفی از پرسنل)": "Guest rooms (hidden from staff)","در انتظار بررسی": "Pending review","آخرین بهروزرسانی": "Last updated","ایمپورت اطلاعات": "Import data","قبل از ثبت نهایی، پیشنمایش تعداد رکوردهای معتبر/تکراری/خطا نمایش داده میشود.": "Before the final save you get a preview of valid, duplicate and error records.","پیشنمایش": "Preview","اتصال به PBX (ایزابل)": "PBX connection (Issabel)","وضعیت زندهٔ تلفنها، داخلیهای جدید برای تأیید و تلفنهای آفلاین.": "Live phone status, new extensions to approve, and offline phones.","هنوز تنظیم نشده؛ راهنما در DEPLOYMENT.md.": "Not configured yet; see DEPLOYMENT.md.","باز کردن صفحهٔ PBX": "Open the PBX page","ایمپورت MAC تلفنها": "Import phone MACs","از فایل Excel یا CSV؛ ستونها را خودتان انتخاب میکنید و قبل از ثبت، پیشنمایش کامل میبینید. فایل ناقص هم مشکلی ندارد.": "From an Excel or CSV file; you pick the columns and see a full preview before saving. Incomplete files are fine.","شروع ایمپورت MAC": "Start MAC import","خروجی گرفتن": "Export","نسخه پشتیبان یا فایل قابل ویرایش دریافت کنید.": "Download a backup or an editable file.","بکاپ کامل": "Full backup","گزارش PDF تفکیکشده": "PDF report by department","برای هر بخش یک جدول مستقل در PDF ساخته میشود.": "One separate table per department is generated in the PDF.","همه بخشها": "All departments","دانلود": "Download","افزودن بخش جدید": "Add a department","یک واحد یا دپارتمان تازه به فهرست بخشها اضافه کنید.": "Add a new unit or department to the department list.","مثلاً کافیشاپ": "e.g. Coffee shop","ساعت کاری (مثلاً ۰۸:۰۰ تا ۲۰:۰۰)": "Working hours (e.g. 08:00 to 20:00)","افزودن": "Add","پاکسازی همه داخلیها": "Clear all extensions","همه به سطل بازیافت منتقل میشوند و از آنجا قابل بازگردانی هستند.": "Everything moves to the recycle bin and can be restored from there.","همه داخلیها به سطل بازیافت منتقل شوند؟ میتوانید بعداً از سطل بازیافت برشان گردانید.": "Move all extensions to the recycle bin? You can restore them from the bin later.","انتقال همه داخلیها": "Move all extensions","بله، منتقل کن": "Yes, move it","بنویسید: پاک کن": "Type: CLEAR","پاکسازی": "Clear","پیشنمایش ایمپورت —": "Import preview —","کل رکوردها:": "Total records:","معتبر:": "Valid:","رکورد جدید:": "New records:","بهروزرسانی موجود:": "Existing updates:","تکراری در فایل:": "Duplicates in file:","خطا:": "Errors:","مخفی از پرسنل (اتاق / در انتظار):": "Hidden from staff (rooms / pending):","مقدار نامعتبر (نادیده گرفته میشود):": "Invalid value (ignored):","تأیید و ثبت نهایی": "Confirm and save","انصراف": "Cancel","مدیریت بخشها": "Manage departments","ساعت کاری…": "Working hours…","ذخیره": "Save","این بخش حذف شود؟ داخلیهای وابسته بدون بخش باقی میمانند.": "Delete this department? Its extensions will be left without a department.","حذف بخش": "Delete department","بله، حذف کن": "Yes, delete","بخشی ثبت نشده است.": "No departments yet.","جستوجو (شماره، نام، عنوان، محل، IP یا MAC)…": "Search (number, name, title, location, IP or MAC)…","همه انواع": "All types","بدون بخش": "No department","وضعیت ثبت": "Record status","فعال و غیرفعال": "Active and inactive","فقط فعال": "Active only","فقط غیرفعال": "Inactive only","وضعیت تلفن": "Phone status","همه وضعیتهای تلفن": "All phone states","MAC: همه": "MAC: all","دارای MAC": "Has MAC","بدون MAC": "No MAC","نمای نمایش": "Display mode","نمای فشرده (کاشی)": "Compact view (tiles)","نمای جدول با جزئیات": "Detailed table view","جستوجو": "Search","پاک کردن": "Clear","انتخاب همه": "Select all","مورد انتخاب شده": "selected","فعالسازی": "Activate","غیرفعالسازی": "Deactivate","موارد انتخابشده به سطل بازیافت منتقل شوند؟": "Move the selected items to the recycle bin?","انتقال به سطل بازیافت": "Move to recycle bin","آنلاین": "Online","آفلاین": "Offline","تأخیر بالا": "High latency","انتخاب": "Select","نتیجهای برای این جستوجو نیست.": "No results for this search.","هنوز داخلی ثبت نشده است.": "No extensions have been added yet.","عنوان / مسئول": "Title / owner","محل": "Location","وضعیت": "Status","تلفن (IP / MAC)": "Phone (IP / MAC)","🟢 فعال": "🟢 Active","🔴 غیرفعال": "🔴 Inactive","این داخلی به سطل بازیافت منتقل شود؟ تا قبل از حذف نهایی میتوانید آن را برگردانید.": "Move this extension to the recycle bin? You can restore it until it is permanently deleted.","مدیران سامانه": "System admins","افزودن مدیر جدید": "Add a new admin","کاربر جدید در اولین ورود موظف به تغییر رمز عبور است.": "The new user must change the password on first sign-in.","نام کاربری (انگلیسی)": "Username (Latin letters)","نام نمایشی": "Display name","رمز عبور موقت (حداقل ۶ کاراکتر)": "Temporary password (min. 6 characters)","افزودن مدیر": "Add admin","نام کاربری": "Username","(خودتان)": "(you)","در انتظار تغییر رمز": "Password change pending","این مدیر حذف شود؟": "Delete this admin?","حذف مدیر": "Delete admin","هتلفون | دفترچه داخلیهای هتل": "Yes, delete","پرش به محتوای اصلی": "Skip to main content","یک بهروزرسانی جدید موجود است.": "A new update is available.","مشاهده تغییرات": "View changes","بستن": "Close","هتل آراز": "Hotel Araz","دفترچه داخلیهای هتل": "Hotel extension directory","صفحه اصلی": "Home","مدیریت": "Management","لیست داخلیها": "Extension list","گزارش قطعیها": "Outage report","سطل بازیافت": "Recycle bin","مدیریت ادمینها": "Manage admins","دانلود بکاپ دیتابیس": "Download database backup","تغییر رمز عبور": "Change password","خروج": "Sign out","هشدار قطعی تلفنها (خاموش)": "Phone outage alerts (off)","هشدار قطعی تلفنها": "Phone outage alerts","انتخاب پوسته": "Choose theme","روشن": "Light","تیره": "Dark","طلایی": "Gold","هتل آراز — دفترچه ارتباطات داخلی": "Hotel Araz — internal communications directory","تأیید عملیات": "Confirm action","بله، ادامه بده": "Yes, continue","برای ادامه استفاده از پنل مدیریت، ابتدا باید رمز عبور خود را تغییر دهید.": "To keep using the admin panel, you must change your password first.","رمز عبور فعلی": "Current password","رمز عبور جدید": "New password","تکرار رمز عبور جدید": "Repeat new password","رمز عبور باید حداقل ۶ کاراکتر باشد.": "The password must be at least 6 characters.","بازگشت به صفحه اصلی": "Back to home","ثبت": "Save","اطلاعات ضروری اتاق و داخلی": "Essential room and extension info","فیلدهای ستارهدار الزامی هستند.": "Fields marked with a star are required.","شماره داخلی": "Extension number","عنوان / سمت": "Title / position","مثلاً پذیرش اصلی": "e.g. Main reception","نام مسئول": "Owner name","انتخاب نشده": "Not selected","محل استقرار": "Location","لابی، طبقه ۲، …": "Lobby, floor 2, …","نوع داخلی": "Extension type","خودکار (طبق الگوی شماره اتاقها)": "Automatic (by room-number pattern)","«اتاق مهمان» و «در انتظار بررسی» برای پرسنل نمایش داده نمیشوند.": "\"Guest room\" and \"Pending review\" extensions are not shown to staff.","ایمیل": "Email","موبایل سازمانی": "Company mobile","داخلی فعال است": "Extension is active","هماکنون حاضر است": "Currently present","فیلد «حضور» فعلاً بهصورت دستی تنظیم میشود و آماده اتصال به سیستم حضور و غیاب در آینده است.": "The \"presence\" field is set manually for now and is ready to connect to an attendance system later.","یادداشت": "Notes","تنظیمات شبکه و سختافزار": "Network and hardware settings","اختیاری — MAC و مدل تلفن": "Optional — MAC and phone model","اختیاری": "Optional","MAC تلفن": "Phone MAC","مدل تلفن": "Phone model","اطلاعات زندهٔ تلفن (فقطخواندنی، از ایزابل خوانده میشود)": "Live phone data (read-only, read from Issabel)","تأخیر:": "Latency:","آخرین بار دیدهشده:": "Last seen:","در حال ذخیره...": "Saving...","ذخیره اطلاعات": "Save changes","آخرین بررسی:": "Last check:","دفترچه ارتباطات هتل آراز": "Hotel Araz communications directory","داخلی مورد نظرتان را سریع پیدا کنید": "Find the extension you need, fast","لیست بهروز واحدها، بخشها و همکاران هتل": "An up-to-date list of units, departments and colleagues","نام، واحد، شماره داخلی یا محل…": "Name, unit, extension or location…","جستوجوی داخلی": "Search extensions","پاک کردن جستوجو": "Clear search","فعال یا غیرفعال": "Active or inactive","همه وضعیتها": "All statuses","مرتبسازی": "Sort","صعودی": "Ascending","نزولی": "Descending","عنوان": "Title","الف تا ی": "A to Z","ی تا الف": "Z to A","تاریخ ثبت": "Date added","جدیدترین": "Newest","قدیمیترین": "Oldest","تعداد در هر صفحه": "Items per page","۲۰ مورد": "20 items","۵۰ مورد": "50 items","۱۰۰ مورد": "100 items","همه موارد": "All items","جستوجوی پیشرفته": "Advanced search","فقط علاقهمندیها": "Favorites only","نام شخص": "Person name","عنوان شغلی": "Job title","فیلتر وضعیت خطوط": "Line status filter","همه": "All","فقط آنلاین": "Online only","فقط آفلاین / قطع": "Offline / down only","در حال اتصال…": "Connecting…","بروزرسانی همین حالا": "Refresh now","داخلی یافت شد": "extensions found","صفحه": "Page","از": "of","نمای کارت": "Card view","نمای جدول فشرده": "Compact table view","چاپ": "Print","موردی مطابق جستوجو یا فیلتر انتخابی در این صفحه پیدا نشد.": "Nothing matches your search or filter on this page.","ثبت نشده": "Not set","افزودن به علاقهمندیها": "Add to favorites","حاضر": "Present","خارج از شیفت": "Off shift","کپی شماره": "Copy number","نتیجهای پیدا نشد.": "No results found.","تلفن": "Phone","کپی": "Copy","نمایش جزئیات بیشتر": "Show more details","صفحهبندی": "Pagination","قبلی": "Previous","بعدی": "Next","جزئیات داخلی": "Extension details","ساعات کاری بخش": "Department working hours","وضعیت حضور": "Presence","آخرین بررسی": "Last check","توضیحات": "Notes","تاریخ ایجاد": "Created","تاریخ ویرایش": "Last edited","خوش آمدید": "Welcome","برای مدیریت دفترچه داخلیها وارد شوید.": "Sign in to manage the extension directory.","رمز عبور": "Password","در حال ورود...": "Signing in...","ورود به پنل": "Sign in","۱. فایل را انتخاب کنید": "1. Choose the file","یک فایل Excel (.xlsx) یا CSV که شمارهٔ داخلی و MAC تلفنها را دارد. ستونها را در مرحلهٔ بعد انتخاب میکنید و قبل از ثبت، پیشنمایش کامل میبینید. ردیفهای خالی یا ناقص اشکالی ندارند و فقط ردیفهای کامل اعمال میشوند.": "An Excel (.xlsx) or CSV file that contains the extension numbers and phone MACs. You choose the columns in the next step and see a full preview before saving. Empty or incomplete rows are fine; only complete rows are applied.","آپلود و ادامه": "Upload and continue","۲. ستونها و گزینهها": "2. Columns and options","برگه (Sheet)": "Sheet","ستون شمارهٔ داخلی *": "Extension number column *","ستون MAC *": "MAC column *","ستون مدل تلفن (اختیاری)": "Phone model column (optional)","ندارد": "None","ردیف اول عنوان ستونهاست": "The first row contains column titles","اگر MAC دیگری ثبت است، جایگزین شود": "Replace the MAC if a different one is already saved","داخلیهای ناموجود ساخته شوند (اتاقها بهصورت اتاق مهمان، بقیه «در انتظار بررسی» و مخفی از پرسنل)": "Create missing extensions (rooms as guest rooms, the rest as \"Pending review\" and hidden from staff)","نمایش همهٔ ردیفها": "Show all rows","فقط موارد مشکلدار / ردشده": "Problem / rejected rows only","بهروزرسانی پیشنمایش": "Refresh preview","ستون شمارهٔ داخلی و ستون MAC را انتخاب کنید و «بهروزرسانی پیشنمایش» را بزنید.": "Choose the extension-number column and the MAC column, then click \"Refresh preview\".","۳. پیشنمایش نتیجه": "3. Result preview","MAC جدید:": "New MAC:","جایگزینی:": "Replaced:","داخلی جدید:": "New extension:","بدون تغییر:": "No change:","MAC متفاوت (رد شد):": "Different MAC (rejected):","MAC نامعتبر:": "Invalid MAC:","داخلی در دیتابیس نیست:": "Extension not in database:","در سطل بازیافت:": "In recycle bin:","MAC خالی:": "Empty MAC:","MAC مشترک بین چند داخلی:": "MAC shared by several extensions:","ردیف": "Row","MAC در فایل": "MAC in file","MAC نرمالشده": "Normalized MAC","مدل": "Model","نتیجه": "Result","توضیح": "Note","MAC مشترک": "Shared MAC","ردیفی برای نمایش نیست.": "No rows to show.","فقط": "Only","ردیف از": "rows of","ردیف نمایش داده شد؛ همهٔ ردیفها اعمال میشوند.": "rows are shown; all rows will be applied.","اعمال تغییرات (": "Apply changes (","داخلی)": "extensions)","گزارش قطعی تلفنها": "Phone outage report","هر ردیف یک بار قطعشدنِ تأییدشدهٔ تلفن روی ایزابل است. قطعیهای کوتاهتر از زمان تأیید ثبت نمیشوند.": "Each row is one confirmed phone outage on Issabel. Outages shorter than the confirmation time are not recorded.","ثبت قطعیها اکنون کار نمیکند (اتصال به PBX تنظیم نشده یا": "Outage logging is not running (PBX connection is not configured or","است). رکوردهای قبلی همچنان دیده میشوند.": "is off). Earlier records are still visible.","آفلاین الان": "Offline now","قطعیهای جاری": "Ongoing outages","قطعیهای امروز": "Outages today","از نیمهشب به وقت محلی": "Since midnight, local time","بدقولترین داخلیها (۳۰ روز اخیر)": "Least reliable extensions (last 30 days)","بار · جمعاً": "times · total","در ۳۰ روز اخیر قطعیای ثبت نشده است.": "No outages were recorded in the last 30 days.","داخلی، عنوان یا IP…": "Extension, title or IP…","از تاریخ": "From date","تا تاریخ": "To date","امروز": "Today","۷ روز": "7 days","۳۰ روز": "30 days","فقط قطعیهای جاری": "Ongoing outages only","پاک کردن فیلترها": "Clear filters","بازهٔ تاریخ بر اساس زمان شروع قطعی است.": "The date range is based on the outage start time.","قطعی": "outages","مورد آخر نمایش داده میشود؛ فایل CSV همهٔ موارد را دارد.": "The latest items are shown; the CSV file contains all of them.","خروجی CSV": "CSV export","زمان قطع": "Went down","زمان وصل": "Came back","مدت": "Duration","هنوز قطع است": "Still down","داخلی غیرفعال، حذف یا از ایزابل پاک شد": "Extension was disabled, deleted or removed from Issabel","پایان پایش": "Monitoring ended","قطعیای با این فیلترها ثبت نشده است.": "No outages match these filters.","قطعیهای تمامشدهی قدیمیتر از": "Finished outages older than","روز خودکار پاک میشوند.": "days are cleaned up automatically.","اتصال به PBX تنظیم نشده است. یا متغیرهای محیطی": "The PBX connection is not configured. Either set the environment variables","،": ",","و": "and","را ست کنید، یا برای اتصال از طریق وبپنل ایزابل (بدون AMI)": ", or to connect through the Issabel web panel (without AMI) set","را (راهنما در DEPLOYMENT.md)، و برنامه را ریاستارت کنید.": "(see DEPLOYMENT.md) and restart the app.","سرور:": "Server:","(وبپنل ایزابل)": "(Issabel web panel)","· بروزرسانی هر": "· refreshes every","ثانیه": "seconds","آخرین همگامسازی موفق:": "Last successful sync:","ثانیه)": "sec)","هنوز انجام نشده": "Not done yet","آخرین خطا:": "Last error:","در حال دریافت...": "Loading...","همگامسازی با PBX": "Sync with PBX","داخلیهای دیدهشده روی PBX:": "Extensions seen on the PBX:","جدید در آخرین بار:": "New in the last run:","نادیده (غیرداخلی، مثل ترانک):": "Ignored (non-extensions, e.g. trunks):","وضعیت تلفنها به تفکیک نوع": "Phone status by type","نوع": "Type","نامشخص": "Unknown","بدون داده": "No data","«بدون داده» یعنی این داخلی هنوز از PBX خوانده نشده یا روی PBX وجود ندارد. وضعیتها طبق آخرین همگامسازی هستند.": "\"No data\" means this extension has not been read from the PBX yet or does not exist on the PBX. Statuses reflect the last sync.","داخلیهای جدید در انتظار تأیید (": "New extensions awaiting approval (","این داخلیها روی PBX هستند ولی در دفترچه نیستند و فعلاً از پرسنل مخفیاند. نوعشان را مشخص کنید؛ برای ویرایش عنوان و بخش از «ویرایش» استفاده کنید.": "These extensions exist on the PBX but not in the directory, and are hidden from staff for now. Set their type; use \"Edit\" to change the title and department.","آخرین وضعیت": "Last status","پرسنل / اداری": "Staff / office","اتاق مهمان": "Guest room","تلفنهای آفلاین (": "Offline phones (","آخرین IP": "Last IP","آخرین بار آنلاین": "Last online","در دفترچه هست ولی روی PBX دیده نشد (": "In the directory but not seen on the PBX (","ممکن است شمارهٔ قدیمی، خط خارجی یا داخلیای باشد که از PBX حذف شده.": "It may be an old number, an external line, or an extension that was removed from the PBX.","MAC ثبتشده با MAC تلفن فرق دارد (": "Saved MAC differs from the phone's MAC (","MAC داخل User-Agent خود تلفن (مثل Htek) با MAC ثبتشده در دیتابیس یکی نیست؛ شاید تلفن عوض شده یا MAC اشتباه ثبت شده.": "The MAC inside the phone's own User-Agent (e.g. Htek) does not match the MAC saved in the database; the phone may have been replaced or the MAC entered wrongly.","MAC ثبتشده": "Saved MAC","MAC خود تلفن": "Phone's own MAC","تاریخ حذف": "Deleted on","بازگردانی": "Restore","این داخلی برای همیشه حذف شود؟ این کار قابل بازگشت نیست.": "Delete this extension permanently? This cannot be undone.","حذف نهایی": "Delete permanently","بله، برای همیشه حذف کن": "Yes, delete permanently","حذف همیشگی": "Permanent deletion","سطل بازیافت خالی است.": "The recycle bin is empty.","تلفن آنلاین": "Phone online","تلفن آفلاین": "Phone offline","وضعیت تلفن نامشخص": "Phone status unknown","تلفن آنلاین (تأخیر بالا)": "Phone online (high latency)","هیچ فونت فارسی روی این سرور پیدا نشد. یک فایل TTF فارسی (مثلاً Vazirmatn) را در پوشهٔ fonts کنار app.py بگذارید یا مسیر آن را در متغیر محیطی PDF_FONT_PATH ست کنید.": "No Persian font was found on this server. Put a Persian TTF file (e.g. Vazirmatn) in the fonts folder next to app.py, or set its path in the PDF_FONT_PATH environment variable.","آنلاین با تأخیر بالا": "Online with high latency","بدون اطلاعات PBX": "No PBX data","ثبت MAC جدید": "Register new MAC","جایگزینی MAC": "Replace MAC","ساخت داخلی جدید": "Create new extension","بدون تغییر": "No change","MAC متفاوتی ثبت است (رد شد)": "A different MAC is already saved (rejected)","MAC نامعتبر": "Invalid MAC","داخلی در دیتابیس نیست": "Extension is not in the database","داخلی در سطل بازیافت است": "Extension is in the recycle bin","داخلی تکراری در فایل": "Duplicate extension in file","MAC خالی": "Empty MAC","شماره داخلی خالی": "Empty extension number","ساختهشده از روی ایزابل؛ مخفی از پرسنل تا تأیید مدیر": "Created from Issabel; hidden from staff until an admin approves it","یک همگامسازی دیگر در حال اجراست.": "Another sync is already running.","خطای داخلی در همگامسازی؛ جزئیات در لاگ سرور ثبت شد.": "Internal error during sync; details were written to the server log.","نشست شما منقضی شده یا صفحه در تب دیگری قدیمی بوده است. لطفاً دوباره تلاش کنید.": "Your session has expired or the page was stale in another tab. Please try again.","صفحه پیدا نشد": "Page not found","آدرسی که وارد کردید وجود ندارد.": "The address you entered does not exist.","خطای داخلی سرور": "Internal server error","مشکلی پیش آمد. لطفاً کمی بعد دوباره تلاش کنید.": "Something went wrong. Please try again shortly.","تعداد تلاشهای ناموفق بیش از حد مجاز است. چند دقیقه دیگر دوباره امتحان کنید.": "Too many failed attempts. Try again in a few minutes.","برای ادامه، ابتدا رمز عبور خود را تغییر دهید.": "To continue, first change your password.","نام کاربری یا رمز عبور صحیح نیست.": "The username or password is incorrect.","رمز عبور فعلی صحیح نیست.": "The current password is incorrect.","رمز عبور جدید باید حداقل ۶ کاراکتر باشد.": "The new password must be at least 6 characters.","این رمز عبور پیشفرض و قابل حدس است؛ رمز دیگری انتخاب کنید.": "This password is a default and easy to guess; choose another one.","رمز عبور جدید و تکرار آن یکسان نیستند.": "The new password and its repeat do not match.","رمز عبور با موفقیت تغییر کرد.": "Password changed successfully.","نام کاربری، نام نمایشی و رمز عبور (حداقل ۶ کاراکتر) الزامی هستند.": "Username, display name and password (min. 6 characters) are required.","این نام کاربری قبلاً ثبت شده است.": "This username is already taken.","مدیر جدید افزوده شد. در اولین ورود باید رمز عبور را تغییر دهد.": "New admin added. They must change the password on first sign-in.","نمیتوانید حساب کاربری خودتان را حذف کنید.": "You cannot delete your own account.","حداقل یک مدیر باید در سامانه باقی بماند.": "At least one admin must remain in the system.","مدیر حذف شد.": "Admin deleted.","شماره داخلی و عنوان الزامی هستند.": "Extension number and title are required.","این شماره داخلی در سطل بازیافت وجود دارد. برای استفاده مجدد از آن، ابتدا از سطل بازیافت بازگردانید یا برای همیشه حذف کنید.": "This extension number exists in the recycle bin. To reuse it, first restore it from the bin or delete it permanently.","این شماره داخلی قبلاً ثبت شده است.": "This extension number is already registered.","MAC نامعتبر است. مثال درست: 00:15:65:AA:BB:CC": "Invalid MAC. Correct example: 00:15:65:AA:BB:CC","داخلی با موفقیت ذخیره شد.": "Extension saved successfully.","به سطل بازیافت منتقل شد": "Moved to the recycle bin","داخلی به سطل بازیافت منتقل شد.": "Extension moved to the recycle bin.","برای پاکسازی، عبارت «پاک کن» را دقیق وارد کنید.": "To clear everything, type the phrase \"CLEAR\" exactly.","از سطل بازیافت بازگردانده شد": "Restored from the recycle bin","داخلی بازگردانده شد.": "Extension restored.","داخلی برای همیشه حذف شد.": "Extension permanently deleted.","هیچ داخلیای انتخاب نشده بود.": "No extension was selected.","بخش جدید افزوده شد.": "New department added.","نام بخش خالی یا تکراری است.": "The department name is empty or already exists.","نام بخش نمیتواند خالی باشد.": "The department name cannot be empty.","بخشی با این نام از قبل وجود دارد.": "A department with this name already exists.","بخش ویرایش شد.": "Department updated.","بخش حذف شد؛ داخلیهای آن بدون بخش باقی ماندهاند.": "Department deleted; its extensions were left without a department.","فایل دیتابیس پیدا نشد.": "Database file not found.","ساخت نسخهٔ پشتیبان ناموفق بود. جزئیات در لاگ سرور ثبت شد.": "Creating the backup failed. Details were written to the server log.","دانلود نسخه پشتیبان کامل دیتابیس": "Full database backup downloaded","فرمت نامعتبر است.": "Invalid format.","هیچ داخلیای برای این جستوجو/فیلتر برای خروجی گرفتن پیدا نشد.": "No extension matches this search/filter for export.","هنوز داخلیای برای خروجی گرفتن ثبت نشده است.": "There are no extensions to export yet.","داخلیای برای بخش انتخابشده پیدا نشد.": "No extensions were found for the selected department.","فایلی انتخاب نشده است.": "No file was selected.","فقط فایل CSV یا XLSX پذیرفته میشود.": "Only CSV or XLSX files are accepted.","فایل قابل خواندن نیست. ستونهای استاندارد را بررسی کنید.": "The file cannot be read. Check the standard columns.","پیشنمایشی برای تأیید پیدا نشد؛ دوباره فایل را انتخاب کنید.": "No preview found to confirm; select the file again.","فایل موقت پیدا نشد؛ دوباره تلاش کنید.": "The temporary file was not found; try again.","فایل قابل خواندن نیست. یک فایل Excel (.xlsx) یا CSV معتبر انتخاب کنید.": "The file cannot be read. Choose a valid Excel (.xlsx) or CSV file.","فایل پیدا نشد؛ دوباره آپلود کنید.": "File not found; upload it again.","ستون شماره داخلی و ستون MAC را انتخاب کنید.": "Choose the extension-number column and the MAC column.","ثبت MAC ناموفق بود. جزئیات در لاگ سرور ثبت شد.": "Saving the MAC addresses failed. Details were written to the server log.","اتصال به PBX تنظیم نشده است.": "The PBX connection is not configured.","درخواست نامعتبر است.": "Invalid request.","لیست داخلیهای هتل آراز": "Hotel Araz extension list","هیچ داخلیای برای این خروجی پیدا نشد.": "No extensions were found for this export.","مدیر سیستم": "System admin","ادامه میدهید؟": "Do you want to continue?","در حال ارسال...": "Sending...","نمایش": "Showing","مورد این صفحه": "items on this page","وضعیت داخلی": "Extension status","اطلاعات بیشتری ثبت نشده است.": "No further information has been recorded.","بروزرسانی زنده": "Live updates","· آخرین همگامسازی با PBX:": "· Last PBX sync:","ارتباط با سرور برقرار نیست — نمایش آخرین وضعیت شناختهشده": "No connection to the server — showing the last known status","خاموش": "off","روشن — اعلان ویندوز": "on — Windows notification","روشن — فقط داخل همین صفحه": "on — in this page only","هشدار قطعی تلفنها (": "Phone outage alerts (","این مرورگر اعلان سیستم را پشتیبانی نمیکند؛ هشدار فقط داخل همین صفحه نشان داده میشود.": "This browser does not support system notifications; alerts are shown in this page only.","مرورگر روی آدرس http (غیر از localhost) اعلان ویندوز نمیدهد. برای اعلان، سایت را با HTTPS یا از روی localhost باز کنید. فعلاً هشدار فقط داخل همین صفحه نشان داده میشود.": "The browser does not show system notifications on plain http (except localhost). Open the site over HTTPS or from localhost to get them. For now alerts are shown in this page only.","هشدار قطعی تلفنها روشن شد.": "Phone outage alerts turned on.","اعلان برای این سایت در مرورگر مسدود است (قفل کنار آدرس ← Notifications ← Allow). فعلاً هشدار فقط داخل همین صفحه نشان داده میشود.": "Notifications are blocked for this site (lock icon next to the address → Notifications → Allow). For now alerts are shown in this page only.","اجازهٔ اعلان داده نشد؛ هشدار فقط داخل همین صفحه نشان داده میشود.": "Notification permission was not granted; alerts are shown in this page only.","هشدار قطعی تلفنها خاموش شد.": "Phone outage alerts turned off.","پذیرش": "Reception","خانهداری": "Housekeeping","رستوران": "Restaurant","فنی": "Technical","وضعیت PBX": "PBX status","دفترچه عمومی": "Public directory","پوسته": "Theme","زبان": "Language","منو": "Menu","دفترچه": "Directory","پایش": "Monitoring","سیستم": "System","نمای کلی": "Overview","هشدار": "Alerts","خروج از حساب": "Sign out","ادمین": "Administrator","ایمپورت MAC": "MAC import","جستوجوی سریع": "Quick search"}

# Users, roles and permissions (RBAC): pages, feature labels from features.py and their messages.
for _k, _v in {
    '+ افزودن کاربر': '+ Add user',
    '+ نقش جدید': '+ New role',
    'آخرین ورود': 'Last login',
    'از نقش': 'From role',
    'اضافه': 'Granted',
    'محدود': 'Denied',
    'افزودن کاربر': 'Add user',
    'ویرایش کاربر': 'Edit user',
    'حذف کاربر': 'Delete user',
    'این کاربر حذف شود؟': 'Delete this user?',
    'اگر فقط می‌خواهید موقتاً جلوی ورود را بگیرید، حساب را غیرفعال کنید.': 'If you only want to block sign-in for a while, deactivate the account instead.',
    'این نقش حذف شود؟': 'Delete this role?',
    'حذف نقش': 'Delete role',
    'بدون نقش': 'No role',
    'تغییرات از درخواست بعدی برای همه‌ی کاربرانِ این نقش اعمال می‌شود.': 'Changes apply to every user with this role from their next request.',
    'تیک‌ها از نقش می‌آیند. هر تغییری نسبت به نقش فقط برای همین کاربر به‌صورت «اضافه» یا «محدود» ذخیره می‌شود؛ «محدود» بر نقش برتری دارد.':
        'Ticks come from the role. Anything you change is saved for this user only, as “granted” or “denied”; “denied” always wins over the role.',
    'ثبت رمز': 'Set password',
    'حساب فعال است': 'Account is active',
    'حساب کاربری': 'Account',
    'حساس': 'Sensitive',
    'عملیات حساس؛ فقط به افراد مطمئن بدهید.': 'Sensitive operation; give it to trusted people only.',
    'دسترسی اضافه': 'Extra permissions',
    'دسترسی محدودشده': 'Denied permissions',
    'دسترسی‌ها': 'Permissions',
    'ریست رمز عبور': 'Reset password',
    'یک رمز موقت بگذارید؛ کاربر در ورود بعدی باید آن را تغییر دهد.': 'Set a temporary password; the user must change it at the next sign-in.',
    'سیستمی': 'Built-in',
    'مدیر ارشد همیشه همه‌ی دسترسی‌ها را دارد، حتی قابلیت‌هایی که بعداً اضافه می‌شوند.': 'A Super Admin always has every permission, including features added later.',
    'مشخصات نقش': 'Role details',
    'نام نقش': 'Role name',
    'نقش': 'Role',
    'نقش جدید': 'New role',
    'ویرایش نقش': 'Edit role',
    'نقش سیستمی: نام و دسترسی‌ها قابل تغییرند ولی حذف نمی‌شود.': 'Built-in role: its name and permissions can be changed, but it cannot be deleted.',
    'نقش و وضعیت': 'Role and status',
    'نقش، دسترسی‌های پایه را تعیین می‌کند.': 'The role sets the base permissions.',
    'نقش، وضعیت و دسترسی‌های حساب خودتان را فقط مدیر دیگری می‌تواند تغییر دهد.': 'Only another administrator can change the role, status or permissions of your own account.',
    'نقش‌ها و دسترسی‌ها': 'Roles & permissions',
    'نقش‌های سیستمی حذف نمی‌شوند. نقشی که به کاربری داده شده، تا وقتی کاربر دارد قابل حذف نیست.': 'Built-in roles cannot be deleted. A role cannot be deleted while users have it.',
    'کاربران': 'Users',
    '← بازگشت به نقش‌ها': '← Back to roles',
    '← بازگشت به کاربران': '← Back to users',
    'همه': 'All',
    'کپی': 'Copy',
    'در حال ذخیره...': 'Saving...',
    # feature labels and descriptions (features.py)
    'مشاهده‌ی لیست داخلی‌ها': 'View extension list',
    'پنل مدیریت داخلی‌ها، اتاق‌ها و اطلاعات تماس': 'Admin list of extensions, rooms and contact details',
    'افزودن داخلی': 'Add extensions',
    'ویرایش داخلی': 'Edit extensions',
    'ویرایش، فعال/غیرفعال کردن گروهی': 'Edit, bulk activate / deactivate',
    'حذف داخلی': 'Delete extensions',
    'انتقال به سطل بازیافت (تکی و گروهی)': 'Move to the recycle bin (single and bulk)',
    'حذف همه‌ی داخلی‌ها': 'Delete all extensions',
    'ایمپورت فایل داخلی‌ها': 'Import extension files',
    'افزودن و بازنویسی گروهی از Excel/CSV': 'Bulk add and overwrite from Excel/CSV',
    'بازنویسی گروهی MAC و مدل تلفن': 'Bulk overwrite of MAC and phone model',
    'خروجی کامل داخلی‌ها': 'Full extension export',
    'CSV/Excel/PDF همراه با اطلاعات تماس و شبکه': 'CSV/Excel/PDF including contact and network details',
    'مدیریت بخش‌ها': 'Manage departments',
    'افزودن، ویرایش و حذف بخش‌ها': 'Add, edit and delete departments',
    'مشاهده‌ی وضعیت PBX': 'View PBX status',
    'صفحه‌ی PBX و جزئیات زنده‌ی تلفن‌ها (IP، تأخیر)': 'PBX page and live phone details (IP, latency)',
    'همگام‌سازی دستی PBX': 'Manual PBX sync',
    'تعیین نوع داخلی‌های جدید PBX': 'Set the type of new PBX extensions',
    'پرسنل یا اتاق مهمان': 'Staff or guest room',
    'مشاهده‌ی گزارش قطعی‌ها': 'View outage report',
    'خروجی CSV قطعی‌ها': 'Outage CSV export',
    'هشدار قطعی در مرورگر': 'Outage alerts in the browser',
    'زنگوله‌ی هشدار قطعی تلفن‌ها': 'Phone outage alert bell',
    'مشاهده‌ی سطل بازیافت': 'View recycle bin',
    'بازگردانی از سطل بازیافت': 'Restore from the recycle bin',
    'حذف دائمی از سطل بازیافت': 'Delete permanently from the recycle bin',
    'دانلود بکاپ دیتابیس': 'Download database backup',
    'فایل کامل دیتابیس، شامل هش رمزهای عبور': 'The whole database file, including password hashes',
    'مدیریت کاربران': 'Manage users',
    'افزودن، ویرایش، حذف و تعیین دسترسی کاربران': 'Add, edit and delete users and set their permissions',
    'مدیریت نقش‌ها': 'Manage roles',
    'ساخت و ویرایش نقش‌ها و دسترسی‌هایشان': 'Create and edit roles and their permissions',
    'مشاهده‌ی لاگ فعالیت‌ها': 'View activity log',
    'مدیر ارشد': 'Super Admin',
    'همه‌ی دسترسی‌ها، حتی قابلیت‌های آینده. قابل ویرایش یا حذف نیست.': 'Every permission, including future features. Cannot be edited or deleted.',
    'اپراتور': 'Operator',
    'کار روزمره: ویرایش داخلی‌ها، بخش‌ها و پایش PBX؛ بدون عملیات حساس و بدون لاگ‌ها.': 'Day-to-day work: editing extensions, departments and PBX monitoring; no sensitive operations and no logs.',
    'مشاهده‌گر': 'Viewer',
    'فقط مشاهده‌ی داخلی‌ها، وضعیت PBX و قطعی‌ها.': 'View only: extensions, PBX status and outages.',
    # messages
    'دسترسی ندارید': 'Access denied',
    'حساب شما اجازه‌ی دسترسی به این بخش را ندارد. اگر لازم است، با مدیر سیستم تماس بگیرید.': 'Your account is not allowed to open this page. Contact your system administrator if you need it.',
    'حساب شما هنوز به هیچ بخشی از پنل مدیریت دسترسی ندارد. با مدیر سیستم تماس بگیرید.': 'Your account has no access to any part of the admin panel yet. Contact your system administrator.',
    'حساب کاربری شما غیرفعال شده است. با مدیر سیستم تماس بگیرید.': 'Your account has been deactivated. Contact your system administrator.',
    'نام کاربری و نام نمایشی الزامی هستند.': 'Username and display name are required.',
    'نام کاربری باید ۲ تا ۸۰ نویسه از حروف انگلیسی، عدد یا . _ - @ باشد.': 'The username must be 2 to 80 characters: English letters, digits or . _ - @',
    'رمز عبور موقت باید حداقل ۶ کاراکتر باشد.': 'The temporary password must be at least 6 characters.',
    'یک نقش انتخاب کنید.': 'Choose a role.',
    'فقط مدیر ارشد می‌تواند حساب مدیر ارشد بسازد یا تغییر دهد.': 'Only a Super Admin can create or change a Super Admin account.',
    'فقط مدیر ارشد می‌تواند حساب مدیر ارشد را تغییر دهد.': 'Only a Super Admin can change a Super Admin account.',
    'این آخرین مدیر ارشد فعال است؛ نمی‌توان آن را غیرفعال یا تنزل داد.': 'This is the last active Super Admin; it cannot be deactivated or demoted.',
    'این آخرین مدیر ارشد فعال است و قابل حذف نیست.': 'This is the last active Super Admin and cannot be deleted.',
    'این کار روی حساب خودتان مجاز نیست.': 'You cannot do this to your own account.',
    'کاربر جدید افزوده شد. در اولین ورود باید رمز عبور را تغییر دهد.': 'User added. They must change the password at first sign-in.',
    'تغییرات کاربر ذخیره شد.': 'User changes saved.',
    'تغییری برای ذخیره نبود.': 'Nothing to save.',
    'رمز عبور موقت ثبت شد. کاربر در ورود بعدی باید آن را تغییر دهد.': 'Temporary password set. The user must change it at the next sign-in.',
    'کاربر حذف شد.': 'User deleted.',
    'نام نقش الزامی است.': 'The role name is required.',
    'نقشی با این نام وجود دارد.': 'A role with this name already exists.',
    'نقش ذخیره شد.': 'Role saved.',
    'نقش مدیر ارشد همیشه همه‌ی دسترسی‌ها را دارد و قابل ویرایش نیست.': 'The Super Admin role always has every permission and cannot be edited.',
    'نقش‌های سیستمی قابل حذف نیستند.': 'Built-in roles cannot be deleted.',
    'نقش حذف شد.': 'Role deleted.',
    'چه کسی به چه چیزی دسترسی دارد': 'Who has access to what',
    'گزارش دسترسی‌ها (CSV)': 'Access report (CSV)',
    'قفل موقت': 'Temporarily locked',
    'باز کردن قفل': 'Unlock',
    'قفل ورود این حساب باز شد.': 'The account is unlocked.',
    'این حساب به‌دلیل چند تلاش ناموفق پشت‌سرهم موقتاً قفل شده است. چند دقیقه‌ی دیگر دوباره امتحان کنید.': 'This account is temporarily locked after several failed sign-in attempts. Try again in a few minutes.',
    'حساب کاربری شما غیرفعال شده است و از سامانه خارج شدید.': 'Your account has been deactivated and you have been signed out.',
}.items():
    EN.setdefault(norm(_k), _v)
PATTERNS_RBAC = [
    (r'^نمی‌?توانید دسترسی‌?ای بدهید که خودتان ندارید: (.+)$',
     lambda m, T: 'You cannot give access you do not have yourself: ' + ', '.join(T(x) for x in m.group(1).split('، '))),
    (r'^این نقش به (\d+) کاربر داده شده است؛ ابتدا نقش آن‌?ها را عوض کنید\.$',
     lambda m, T: '%s user(s) have this role; change their role first.' % m.group(1)),
]

# Persian strings that JavaScript (static/app.js) needs; sent to the browser in English mode only.
JS_KEYS = ["· آخرین همگامسازی با PBX:", "آخرین بررسی:", "اجازهٔ اعلان داده نشد؛ هشدار فقط داخل همین صفحه نشان داده میشود.", "ادامه میدهید؟", "ارتباط با سرور برقرار نیست — نمایش آخرین وضعیت شناختهشده", "از", "اطلاعات بیشتری ثبت نشده است.", "اعلان برای این سایت در مرورگر مسدود است (قفل کنار آدرس ← Notifications ← Allow). فعلاً هشدار فقط داخل همین صفحه نشان داده میشود.", "انتقال به سطل بازیافت", "این داخلی به سطل بازیافت منتقل شود؟ تا قبل از حذف نهایی میتوانید آن را برگردانید.", "این مرورگر اعلان سیستم را پشتیبانی نمیکند؛ هشدار فقط داخل همین صفحه نشان داده میشود.", "بخش", "بروزرسانی زنده", "بستن", "بله، ادامه بده", "بله، منتقل کن", "تأخیر:", "تأیید عملیات", "تلفن آفلاین", "تلفن آنلاین", "تلفن آنلاین (تأخیر بالا)", "توضیحات", "جزئیات", "حاضر", "خارج از شیفت", "خاموش", "در حال ارسال...", "روشن — اعلان ویندوز", "روشن — فقط داخل همین صفحه", "غیرفعال", "فعال", "محل", "مرورگر روی آدرس http (غیر از localhost) اعلان ویندوز نمیدهد. برای اعلان، سایت را با HTTPS یا از روی localhost باز کنید. فعلاً هشدار فقط داخل همین صفحه نشان داده میشود.", "مورد این صفحه", "نام مسئول", "نمایش", "هشدار قطعی تلفنها (", "هشدار قطعی تلفنها خاموش شد.", "هشدار قطعی تلفنها روشن شد.", "وضعیت تلفن نامشخص", "وضعیت داخلی"]


def js_dictionary():
    return {k: EN[k] for k in JS_KEYS if k in EN}


def _n(m, i=1):
    return m.group(i)


def _plural(n, one, many):
    return one if int(n) == 1 else many


PATTERNS = [
    (r'^قبلاً در ردیف (\d+) آمده است$', lambda m, T: 'Already listed in row %s' % m.group(1)),
    (r'^نوع: (.+)$', lambda m, T: 'Type: ' + T(m.group(1))),
    (r'^MAC فعلی: (.+)$', lambda m, T: 'Current MAC: ' + m.group(1)),
    (r'^(?:(.+) — )?همین MAC روی داخلی (.+) هم هست$',
     lambda m, T: (T(m.group(1)) + ' — ' if m.group(1) else '') + 'The same MAC is also on extension(s) ' + m.group(2).replace('، ', ', ')),
    (r'^اتاق (\S+)$', lambda m, T: 'Room ' + m.group(1)),
    (r'^داخلی (\S+)$', lambda m, T: 'Extension ' + m.group(1)),
    (r'^همگامسازی PBX: (\d+) داخلی جدید$', lambda m, T: 'PBX sync: %s new extension(s)' % m.group(1)),
    (r'^نوع این داخلی «(.+)» است و برای پرسنل نمایش داده نمیشود\. اگر داخلی اداری است، نوع را «پرسنل / اداری» بگذارید\.$',
     lambda m, T: 'This extension\u2019s type is \u201c%s\u201d, so it is not shown to staff. If it is an office extension, set the type to \u201cStaff / office\u201d.' % T(m.group(1))),
    (r'^همین MAC روی داخلی (.+) هم ثبت شده است\. اگر یک تلفن چند خط دارد مشکلی نیست؛ در غیر اینصورت بررسی کنید\.$',
     lambda m, T: 'The same MAC is also registered on extension(s) %s. That is fine if one phone has several lines; otherwise please check.' % m.group(1).replace('، ', ', ')),
    (r'^پاکسازی گروهی \((\d+) داخلی\)$', lambda m, T: 'Bulk clear (%s extensions)' % m.group(1)),
    (r'^(\d+) داخلی به سطل بازیافت منتقل شد\.$', lambda m, T: '%s extension(s) moved to the recycle bin.' % m.group(1)),
    (r'^فعالسازی گروهی \((\d+) داخلی\)$', lambda m, T: 'Bulk activation (%s extensions)' % m.group(1)),
    (r'^(\d+) داخلی فعال شد\.$', lambda m, T: '%s extension(s) activated.' % m.group(1)),
    (r'^غیرفعالسازی گروهی \((\d+) داخلی\)$', lambda m, T: 'Bulk deactivation (%s extensions)' % m.group(1)),
    (r'^(\d+) داخلی غیرفعال شد\.$', lambda m, T: '%s extension(s) deactivated.' % m.group(1)),
    (r'^انتقال گروهی به سطل بازیافت \((\d+) داخلی\)$', lambda m, T: 'Bulk move to the recycle bin (%s extensions)' % m.group(1)),
    (r'^ایمپورت فایل \((\d+) رکورد\)$', lambda m, T: 'File import (%s records)' % m.group(1)),
    (r'^(\d+) داخلی با موفقیت وارد یا بهروزرسانی شد\.$', lambda m, T: '%s extension(s) imported or updated successfully.' % m.group(1)),
    (r'^ایمپورت MAC \((\d+) داخلی\)$', lambda m, T: 'MAC import (%s extensions)' % m.group(1)),
    (r'^MAC برای (\d+) داخلی ثبت شد \((\d+) جدید، (\d+) جایگزین، (\d+) داخلی تازهساخته\)\.(?: مدل تلفن برای (\d+) داخلی ثبت شد\.)?$',
     lambda m, T: 'MAC saved for %s extension(s) (%s new, %s replaced, %s newly created).' % m.group(1, 2, 3, 4)
     + (' Phone model saved for %s extension(s).' % m.group(5) if m.group(5) else '')),
    (r'^همگامسازی انجام شد: (\d+) آنلاین، (\d+) آفلاین، (\d+) نامشخص(?:، (\d+) داخلی جدید)?\.$',
     lambda m, T: 'Sync completed: %s online, %s offline, %s unknown' % m.group(1, 2, 3)
     + (', %s new extension(s)' % m.group(4) if m.group(4) else '') + '.'),
    (r'^همگامسازی ناموفق بود: (.+)$', lambda m, T: 'Sync failed: ' + m.group(1)),
    (r'^داخلی (\S+) به «(.+)» تبدیل شد\.$', lambda m, T: 'Extension %s was changed to \u201c%s\u201d.' % (m.group(1), T(m.group(2)))),
    (r'^تغییر رمز عبور — (.+)$', lambda m, T: 'Password changed \u2014 ' + m.group(1)),
    (r'^ستون (\S+)$', lambda m, T: 'Column ' + m.group(1)),
    (r'^فونت تعیینشده در PDF_FONT_PATH پیدا نشد: (.+)$', lambda m, T: 'The font set in PDF_FONT_PATH was not found: ' + m.group(1)),
]
_COMPILED = [(re.compile(p), fn) for p, fn in PATTERNS + PATTERNS_RBAC]
_CACHE = {}


def translate(text, lang):
    """English text for a Persian UI string; anything unknown is returned unchanged."""
    if lang != 'en' or not text or not _FA.search(text):
        return text
    hit = _CACHE.get(text)
    if hit is not None:
        return hit
    key = norm(text).strip()
    out = EN.get(key)
    if out is None:
        for rx, fn in _COMPILED:
            m = rx.match(key)
            if m:
                out = fn(m, lambda s: translate(s, 'en'))
                break
    if out is None:
        out = text
    if len(_CACHE) < 5000:
        _CACHE[text] = out
    return out


def jalali_year(gy, gm, gd):
    """Gregorian -> Jalali year (for the footer)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
    return jy
