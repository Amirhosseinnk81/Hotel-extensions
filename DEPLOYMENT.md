# راهنمای استقرار روی سرور (Production)

## ۱. آدرس مخفی پنل مدیریت

پنل مدیریت (و صفحه ورود) دیگر روی `/admin` یا `/login` نیست. یک مسیر تصادفی و مخفی
اولین بار که برنامه اجرا شود ساخته و در فایل `.admin_prefix` (کنار `app.py`) ذخیره
می‌شود، و در راه‌اندازی‌های بعدی همان مقدار استفاده می‌شود — یعنی با هر بار ری‌استارت
عوض نمی‌شود.

**آدرس واقعی خودتان را از همان فایل ببینید:**
```
type .admin_prefix        (ویندوز)
cat .admin_prefix         (لینوکس)
```
چیزی شبیه `staff-a8839e10` می‌بینید؛ یعنی آدرس ورود شما می‌شود:
```
http://<آی‌پی یا دامنه سرور>/staff-a8839e10/login
```
این آدرس را برای خودتان جایی امن (پسورد منیجر) ذخیره کنید — هیچ لینکی به آن در
سایت عمومی وجود ندارد و از هیچ‌کجای صفحه اصلی قابل پیدا کردن نیست.

اگر ترجیح می‌دهید خودتان یک آدرس دلخواه و به‌یادماندنی انتخاب کنید (نه رشته‌ی
تصادفی)، قبل از اجرا این متغیر محیطی را ست کنید:
```
set ADMIN_URL_PREFIX=my-secret-panel-2026      (ویندوز - PowerShell: $env:ADMIN_URL_PREFIX="...")
export ADMIN_URL_PREFIX=my-secret-panel-2026   (لینوکس)
```
هر مقداری که در این متغیر بگذارید، دیگر از فایل `.admin_prefix` استفاده نمی‌شود.

**نکته امنیتی:** این فقط یک لایه‌ی «پنهان‌کاری» اضافه‌ست، نه جایگزین امنیت واقعی؛
لاگین با رمز عبور و CSRF و Rate-Limit همچنان خط دفاعی اصلی‌ان. اگر آدرس لو رفت
(مثلاً یک نفر آن را در تاریخچه مرورگر یا اسکرین‌شات دید)، کافیه `.admin_prefix`
را پاک کنید (یا `ADMIN_URL_PREFIX` جدید ست کنید) و برنامه را ری‌استارت کنید تا یک
آدرس تصادفی تازه ساخته شود.

## ۲. اجرا با یک وب‌سرور واقعی (نه سرور توسعه Flask)

سرور توسعه‌ی داخلی Flask (همانی که با `python app.py` بالا می‌آید) برای همین
تست‌های محلی خوبه، ولی برای بار واقعی همه‌ی کلاینت‌های هتل مناسب نیست. یکی از این دو
را نصب کنید:

### اگر سرور ویندوزی است (Waitress — ساده‌ترین گزینه روی ویندوز)
```
pip install waitress
waitress-serve --host=0.0.0.0 --port=8080 --call app:create_app
```

### اگر سرور لینوکسی است (Gunicorn)
```
pip install gunicorn
gunicorn -w 1 -b 0.0.0.0:8080 "app:create_app()"
```
**نکته مهم:** فعلاً حتماً با `-w 1` (یک worker) اجرا کنید. کش آمار داشبورد،
شمارنده‌ی نسخه برای اعلان بروزرسانی زنده، و Rate-Limit لاگین همه در حافظه‌ی همان
پردازش نگه‌داری می‌شن؛ با چند worker هماهنگ نمی‌مونن. برای ترافیک داخلی یک هتل،
یک worker کاملاً کافیه. اگر بعداً ترافیک بالا رفت و چند worker لازم شد، باید این
بخش‌ها به Redis منتقل بشن — لازم شد بگید کمک می‌کنم.

سپس یک ری‌ورس‌پروکسی (IIS با ARR، یا Nginx روی لینوکس) جلوی همین پورت داخلی
بذارید تا هم روی پورت ۸۰/۴۴۳ استاندارد در دسترس باشه، هم بعداً اضافه کردن دامنه و
گواهی HTTPS راحت‌تر باشه.

## ۳. متغیرهای محیطی که باید تنظیم کنید

| متغیر | توضیح |
|---|---|
| `PDF_FONT_PATH` | (اختیاری) مسیر یک فونت فارسی TTF. اگر ست نشود، برنامه خودش فونت را پیدا می‌کند: ابتدا فایل‌های `.ttf` داخل پوشهٔ `fonts` کنار `app.py`، بعد Tahoma (ویندوز) یا Vazirmatn / Noto Naskh Arabic / DejaVu Sans (لینوکس). روی سرور لینوکسی بهتر است `Vazirmatn-Regular.ttf` را در پوشهٔ `fonts` بگذارید |
| `TRUSTED_PROXIES` | (اختیاری) آی‌پی پروکسی‌هایی که هدر `X-Forwarded-For` آن‌ها پذیرفته می‌شود، با کاما جدا شده. پیش‌فرض: `127.0.0.1,::1` (Nginx یا IIS روی همان سرور). اگر پروکسی روی ماشین دیگری است، آی‌پی آن را اینجا بگذارید؛ وگرنه محدودیت تلاش لاگین روی آی‌پی پروکسی اعمال می‌شود، نه کاربر. هدر درخواست‌هایی که مستقیم می‌رسند نادیده گرفته می‌شود |
| `SESSION_COOKIE_SECURE` | `1` را وقتی سایت روی HTTPS است ست کنید تا کوکی نشست فقط روی HTTPS ارسال شود. روی HTTP ساده ست نکنید، وگرنه ورود کار نمی‌کند |
| `ADMIN_URL_PREFIX` | (اختیاری) آدرس دلخواه پنل مدیریت به‌جای رشته‌ی تصادفی |
| `SECRET_KEY` | (اختیاری) در صورت عدم تنظیم، خودکار ساخته و در `.secret_key` ذخیره می‌شود. کلیدی که قبلاً در گیت commit شده بود دیگر پذیرفته نمی‌شود و به‌جای آن کلید جدید ساخته می‌شود |
| `SHOW_CONTACT_PUBLICLY` | (اختیاری) `1` = ایمیل و موبایل سازمانی در صفحهٔ عمومی هم نمایش داده شود. پیش‌فرض: فقط برای مدیران واردشده |
| `ROOM_FLOOR_MIN` / `ROOM_FLOOR_MAX` / `ROOM_NUMBER_MAX` | (اختیاری) الگوی شمارهٔ اتاق‌های مهمان: شمارهٔ طبقه + شمارهٔ اتاق. پیش‌فرض `2` / `23` / `27` یعنی ۲۰۱ تا ۲۲۷ برای طبقهٔ ۲ و ۲۳۰۱ تا ۲۳۲۷ برای طبقهٔ ۲۳. فقط برای حدس نوع داخلی‌های جدید استفاده می‌شود |
| `PBX_STATUS_STALE_MINUTES` | (اختیاری) وضعیت تلفنی که قدیمی‌تر از این تعداد دقیقه باشد به‌صورت «نامشخص» نمایش داده می‌شود. پیش‌فرض `10` |
| `PBX_AMI_HOST` | آدرس PBX، مثلاً `192.168.228.2`. اگر این متغیر (یا `PBX_AMI_USER` و `PBX_AMI_SECRET`) ست نباشد، اتصال به ایزابل خاموش است و برنامه مثل قبل کار می‌کند |
| `PBX_AMI_PORT` | (اختیاری) پورت AMI. پیش‌فرض `5038` |
| `PBX_AMI_USER` / `PBX_AMI_SECRET` | کاربر و رمز AMI که در بخش «اتصال به ایزابل» پایین می‌سازید. رمز را فقط همین‌جا بگذارید، نه در کد و نه در گیت |
| `PBX_SYNC_INTERVAL` | (اختیاری) فاصلهٔ همگام‌سازی به ثانیه. پیش‌فرض `30` و حداقل `15`. نام قدیمی `PBX_SYNC_INTERVAL_SECONDS` هم هنوز کار می‌کند. ایزابل هر ۶۰ ثانیه تلفن‌ها را qualify می‌کند، پس مقدار کمتر از ۳۰ فایده‌ای ندارد |
| `PBX_WEB_HOST` / `PBX_WEB_USER` / `PBX_WEB_PASS` | (به‌جای AMI) اتصال از راه وب‌پنل ایزابل، وقتی SSH/روت و دسترسی به `manager.conf` ندارید. مثلاً `https://192.168.1.100`. بخش «اتصال از راه وب‌پنل» پایین را ببینید |
| `PBX_VERIFY_SSL` | (اختیاری) تأیید گواهی HTTPS وب‌پنل. پیش‌فرض `False` چون ایزابل گواهی self-signed دارد |
| `PBX_SOURCE` | (اختیاری) `ami` یا `web`. پیش‌فرض خودکار: اگر AMI تنظیم شده باشد AMI، وگرنه وب‌پنل |
| `PBX_WEB_TIMEOUT` / `PBX_WEB_LOGIN_PATH` / `PBX_WEB_PEERS_PATH` | (اختیاری) زمان انتظار هر درخواست (پیش‌فرض `10`)، آدرس فرم ورود (`/index.php`) و صفحهٔ منبع داخلی‌ها (پیش‌فرض `/index.php?menu=control_panel`؛ برای صفحهٔ قدیمی Asterisk Info مقدار `/index.php?menu=asterisk_info&rawmode=yes`) |
| `PBX_SLOW_MS` | (اختیاری) تلفن آنلاینی که تأخیرش از این مقدار (میلی‌ثانیه) بیشتر یا برابر باشد نشان زرد «تأخیر بالا» می‌گیرد. پیش‌فرض `150` |
| `PBX_AMI_TIMEOUT` | (اختیاری) زمان انتظار برای پاسخ PBX به ثانیه. پیش‌فرض `5` |
| `PBX_UA_BATCH` | (اختیاری) در هر دور User-Agent چند تلفن خوانده شود. پیش‌فرض `60`. دور اول که همه‌چیز تازه است چند دور طول می‌کشد تا مدل همهٔ تلفن‌ها پر شود |
| `PBX_SYNC_AUTOSTART` | (اختیاری) `0` یعنی همگام‌سازی خودکار خاموش باشد و فقط دکمهٔ «همگام‌سازی الان» کار کند. پیش‌فرض `1` |
| `FLASK_DEBUG` | همیشه خالی/`0` بگذارید. `1` فقط برای توسعه‌ی محلی است و در سرور واقعی هرگز نباید فعال باشد (دسترسی به اجرای کد از راه دور در صورت بروز خطا) |

## ۴. اجرای همیشگی (سرویس)

طوری تنظیم کنید که بعد از ریست شدن سیستم، برنامه خودکار بالا بیاید:

- **ویندوز:** Windows Service با NSSM و Waitress؛ راهنمای کامل در بخش «۴-ویندوز» پایین.
- **لینوکس:** یک واحد systemd بسازید که همان دستور Gunicorn را اجرا کند و `Restart=always` داشته باشد.

### ۴-ویندوز: راه‌اندازی به‌صورت Windows Service

این روش روی ویندوز ۱۰ با Python 3.14 و سیستم عضو دامین اجرا و آزموده شده و سه چیز را تضمین می‌کند:

- بعد از ری‌استارت، **بدون اینکه کسی لاگین کند** برنامه بالا می‌آید.
- Log off یا Sign out اثری روی آن ندارد (سرویس به نشست کاربر وابسته نیست).
- اگر برنامه کرش کند، NSSM بعد از ۵ ثانیه دوباره اجرایش می‌کند.

همهٔ دستورها در **PowerShell با Run as administrator** اجرا می‌شوند. قبل از شروع، نسخه‌ای را که با `python app.py` اجرا می‌کردید با `Ctrl+C` ببندید؛ دو نسخه روی یک دیتابیس یعنی دو thread همگام‌سازی.

#### ۱. پیش‌نیازها

| مورد | شرط |
|---|---|
| Python | نصب برای **همهٔ کاربران** (`C:\Python314\...` یا `C:\Program Files\...`). مسیر `AppData\Local\Programs\Python` برای حساب SYSTEM قابل‌دسترس نیست و سرویس بالا نمی‌آید. با `python -c "import sys; print(sys.executable)"` مسیر را ببینید |
| پوشهٔ برنامه | خارج از پروفایل کاربر، مثلاً `C:\HotelExtensions`. پوشهٔ Documents ممکن است ریدایرکت‌شده، روی OneDrive یا روی شبکه باشد و SYSTEM به آن دسترسی مطمئن ندارد |
| NSSM | از nssm.cc/download فایل zip را بگیرید و `win64\nssm.exe` را در `C:\Tools` بگذارید (پایین) |

نصب NSSM (یک بار):

```powershell
New-Item -ItemType Directory C:\Tools -Force | Out-Null
Expand-Archive "$env:USERPROFILE\Downloads\nssm-2.24.zip" C:\Tools\nssm-tmp -Force
Copy-Item C:\Tools\nssm-tmp\nssm-2.24\win64\nssm.exe C:\Tools\nssm.exe
Remove-Item C:\Tools\nssm-tmp -Recurse
[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','Machine') + ';C:\Tools', 'Machine')
$env:Path += ';C:\Tools'
nssm version
```

اگر بعداً سرویس با پیام «did not respond in a timely fashion» بالا نیامد، نسخهٔ prerelease NSSM (`2.24-101`) را از همان صفحه بگذارید؛ ۲.۲۴ روی بعضی ویندوزها مشکل شروع سرویس دارد.

#### ۲. کپی برنامه و ساخت محیط پایتون

```powershell
robocopy "<مسیر-پوشهٔ-فعلی-پروژه>" C:\HotelExtensions /E /XD .git .venv venv __pycache__ tmp_imports /XF *.pyc
cd C:\HotelExtensions
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt
New-Item -ItemType Directory logs -Force | Out-Null
.\.venv\Scripts\python.exe -c "import app; app.create_app(); print('OK')"
```

- کد خروجی robocopy بین ۰ تا ۷ یعنی موفق.
- اگر برنامه از قبل کار می‌کرد، `hotel_extensions.db` (داده‌ها)، `.secret_key` (نشست ورود)، `.admin_prefix` (آدرس پنل) و `.env` هم کپی می‌شوند و همه‌چیز مثل قبل می‌ماند. پوشهٔ قدیمی را تا آخر کار نگه دارید.
- **اسم پوشهٔ محیط پایتون:** در این راهنما `.venv` است. اگر محیط را با اسم دیگری ساخته‌اید (مثلاً `venv`)، همان اسم را در همهٔ مسیرها، به‌خصوص در دستور `nssm install` پایین، بگذارید. مسیر اشتباه یعنی سرویس با خطای ۳ بالا نمی‌آید (بخش ۱۱).
- آخرین دستور باید `OK` چاپ کند.

**اگر نصب بسته‌ها خطا داد:** pip اگر یک بسته را نتواند نصب کند، هیچ‌کدام از بقیه را هم نصب نمی‌کند و بعد `No module named 'flask'` می‌بینید.

| خطا | علت و راه‌حل |
|---|---|
| `Building wheel for python-bidi` ... `link.exe not found` | روی پایتون‌های جدید (مثلاً ۳.۱۴) نسخه‌های قدیمی `python-bidi` فایل آماده ندارند و pip می‌خواهد با Rust بسازد. Visual Studio Build Tools نصب نکنید. این را بزنید: `.\.venv\Scripts\pip install --only-binary :all: python-bidi` و بعد دوباره `pip install -r requirements.txt` |
| `ZoneInfoNotFoundError: 'No time zone found with key Asia/Tehran'` | ویندوز پایگاه منطقهٔ زمانی ندارد: `.\.venv\Scripts\pip install tzdata` |
| `RequestsDependencyWarning: ... chardet ... doesn't match` | فقط هشدار است؛ `.\.venv\Scripts\pip install "chardet<6"` آن را برمی‌دارد |

#### ۳. فایل `.env` و فایل‌های Bootstrap

```powershell
copy .env.example .env
notepad .env
.\.venv\Scripts\python.exe tools\check_env.py
.\.venv\Scripts\python.exe tools\fetch_vendor.py
```

- برنامه `.env` را خودش از کنار `app.py` می‌خواند؛ لازم نیست چیزی در NSSM یا PowerShell ست کنید.
- **متغیری که در خود سیستم یا در NSSM (`AppEnvironmentExtra`) ست شده باشد بر `.env` برتری دارد.** اگر در `.env` رمز را عوض کردید و اثر نکرد، این را بزنید و سرویس را ری‌استارت کنید: `nssm reset HotelExtensions AppEnvironmentExtra`.
- فقط خودتان باید به `.env` دسترسی داشته باشید (رمز ایزابل داخل آن است):
  ```powershell
  icacls C:\HotelExtensions\.env /inheritance:r /grant "SYSTEM:F" "Administrators:F"
  ```
- `fetch_vendor.py` فقط یک بار و روی کامپیوتری که اینترنت دارد لازم است (بخش ۲.۵).

#### ۴. تست دستی (قبل از ساخت سرویس)

```powershell
cd C:\HotelExtensions
.\.venv\Scripts\python.exe -m waitress --host=0.0.0.0 --port=8080 --call app:create_app
```

باید `Serving on http://0.0.0.0:8080` بیاید. بررسی کنید:

1. `http://localhost:8080` باز شود.
2. با آدرس پنل (`type C:\HotelExtensions\.admin_prefix`) وارد شوید.
3. پنل ← «اتصال به PBX» ← «همگام‌سازی با PBX» موفق باشد.

بعد با `Ctrl+C` ببندید. اگر اینجا کار نکرد، سرویس هم کار نمی‌کند؛ اول همین را درست کنید.

#### ۵. ساخت سرویس

مسیرها را **کامل و عینی** بنویسید (متغیر PowerShell برای مسیر استفاده نکنید؛ مقدار خالی یا اشتباه آن بی‌صدا در سرویس ثبت می‌شود):

```powershell
cd C:\HotelExtensions
nssm install HotelExtensions "C:\HotelExtensions\.venv\Scripts\python.exe" "-m waitress --host=0.0.0.0 --port=8080 --call app:create_app"
nssm set HotelExtensions AppDirectory "C:\HotelExtensions"
nssm set HotelExtensions DisplayName "Hotel Extensions"
nssm set HotelExtensions Start SERVICE_DELAYED_AUTO_START
nssm set HotelExtensions AppExit Default Restart
nssm set HotelExtensions AppRestartDelay 5000
nssm set HotelExtensions AppStdout "C:\HotelExtensions\logs\service.log"
nssm set HotelExtensions AppStderr "C:\HotelExtensions\logs\service.log"
nssm set HotelExtensions AppRotateFiles 1
nssm set HotelExtensions AppRotateOnline 1
nssm set HotelExtensions AppRotateBytes 5242880
nssm start HotelExtensions
```

بررسی:

```powershell
nssm get HotelExtensions Application
Test-Path "C:\HotelExtensions\.venv\Scripts\python.exe"      # باید True باشد
Get-Service HotelExtensions                                   # Running
Invoke-WebRequest http://localhost:8080 -UseBasicParsing | Select-Object StatusCode    # 200
Get-Content C:\HotelExtensions\logs\service.log -Tail 20
```

- فقط **یک پردازش Waitress** اجرا کنید (توضیح بخش ۲). `--threads` پیش‌فرض کافی است.
- `FLASK_DEBUG` را هرگز ست نکنید. `SESSION_COOKIE_SECURE` را روی HTTP ساده ست نکنید، وگرنه ورود کار نمی‌کند.

#### ۶. فایروال

فقط شبکهٔ داخلی هتل بتواند به پورت برنامه برسد. شبکه و ماسک را از `Get-NetIPAddress -AddressFamily IPv4` ببینید (مثلاً IP دستگاه `192.168.142.178` با ماسک `/22` یعنی `192.168.140.0/22`):

```powershell
New-NetFirewallRule -DisplayName "HotelExtensions 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Domain,Private -RemoteAddress <شبکه>/<ماسک>
```

اگر کاربران در چند زیرشبکه‌اند، آن‌ها را با کاما بنویسید. بعد از یک سیستم دیگر `http://<IP-این-سیستم>:8080` را باز کنید. اگر باز نشد: `Get-NetTCPConnection -LocalPort 8080 -State Listen` باید `0.0.0.0` نشان دهد؛ و اگر VPN وصل است، یک بار با VPN قطع هم امتحان کنید.

#### ۷. برق، کارت شبکه و Windows Update

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 30
powercfg /hibernate off
```

- `hibernate off` قابلیت Fast Startup را هم خاموش می‌کند که با سرویس‌ها و شبکه مشکل‌ساز است.
- **کارت شبکه:** Device Manager ← Network adapters ← کارت اصلی ← Properties ← Power Management ← تیک «Allow the computer to turn off this device to save power» را بردارید. (دستور `Set-NetAdapterPowerManagement` برای این تیک پارامتری ندارد؛ از همین مسیر گرافیکی انجام دهید.) اگر تب Power Management نبود، درایور این گزینه را ندارد و لازم نیست کاری کنید.
- **Windows Update:** Settings ← Windows Update ← Advanced options ← Active hours را طوری بگذارید که وسط ساعت کار ری‌استارت نشود.

#### ۸. IP ثابت و اسم DNS

اگر IP دستگاه دستی است، از IT بخواهید همان IP را در DHCP برایش **رزرو** کنند (آدرس MAC را با `Get-NetAdapter` ببینید) تا به دستگاه دیگری داده نشود، و یک اسم DNS (مثلاً `extensions`) به آن بدهند. وقتی برنامه به سرور منتقل شد فقط DNS عوض می‌شود و کسی آدرس جدید یاد نمی‌گیرد.

#### ۹. آزمون پذیرش (سه تست)

با سایتِ باز روی یک سیستم دیگر:

1. **ری‌استارت بدون لاگین:** `Restart-Computer`، لاگین نکنید و ۲ تا ۳ دقیقه صبر کنید (Delayed Start). سایت باید از سیستم دیگر باز شود.
2. **Sign out:** وارد شوید و Sign out کنید. سایت باید باز بماند.
3. **قطع کارت شبکه** (حضوری، نه با Remote Desktop؛ نام کارت را از `Get-NetAdapter` بردارید):
   ```powershell
   Disable-NetAdapter -Name "<نام-کارت>" -Confirm:$false
   Start-Sleep 90
   Enable-NetAdapter -Name "<نام-کارت>" -Confirm:$false
   ```
   چند دقیقه بعد `logs\Log.txt` باید یک خط `PBX-LOST` و بعد `PBX-OK` داشته باشد و برای تلفن‌ها قطعی ثبت نشده باشد (بخش ۹). سایت هم بدون دست‌زدن به سرویس برمی‌گردد.

#### ۱۰. نگهداری

| کار | دستور |
|---|---|
| وضعیت، توقف، شروع، ری‌استارت | `nssm status` / `stop` / `start` / `restart HotelExtensions` |
| لاگ سرویس و خطاهای شروع | `C:\HotelExtensions\logs\service.log` و `Get-EventLog -LogName Application -Source nssm -Newest 5` |
| لاگ قطعی تلفن‌ها | `C:\HotelExtensions\logs\Log.txt` |
| تغییر `.env` | ذخیره کنید و `nssm restart HotelExtensions` |
| به‌روزرسانی برنامه | `nssm stop HotelExtensions`؛ فایل‌های جدید را با `robocopy ... /E /XD .git .venv venv __pycache__ tmp_imports` روی `C:\HotelExtensions` بریزید (بدون دست‌زدن به `.db`، `.env`، `.secret_key`، `.admin_prefix`)؛ `.\.venv\Scripts\pip install -r requirements.txt`؛ `nssm start HotelExtensions` |
| حذف سرویس | `nssm stop HotelExtensions` و بعد `nssm remove HotelExtensions confirm` |

**بکاپ شبانه:** در Task Scheduler یک کار روزانه (با حساب SYSTEM) بسازید که این دستور را اجرا کند. مسیر مقصد را روی درایو دیگر یا پوشهٔ شبکه‌ای بگذارید. این روش بکاپ SQLite را حتی وسط نوشتن هم سالم می‌گیرد و به `sqlite3.exe` نیازی ندارد:

```powershell
C:\HotelExtensions\.venv\Scripts\python.exe -c "import sqlite3,datetime as d; s=sqlite3.connect(r'C:\HotelExtensions\hotel_extensions.db'); t=sqlite3.connect(r'D:\Backups\hotel_'+d.date.today().isoformat()+'.db'); s.backup(t); t.close(); s.close()"
```

#### ۱۱. خطایابی

| علامت | علت و راه‌حل |
|---|---|
| پنجرهٔ Services: «Windows could not start ... service-specific error code 3»، و در Event Log: `CreateProcess() failed: The system cannot find the file specified` | مسیر پایتون ثبت‌شده در سرویس وجود ندارد (معمولاً اسم پوشهٔ محیط، `.venv` در برابر `venv`). `nssm get HotelExtensions Application` را ببینید، با `Test-Path` بسنجید و اصلاح کنید: `nssm set HotelExtensions Application "C:\HotelExtensions\.venv\Scripts\python.exe"` و `nssm set HotelExtensions AppParameters "-m waitress --host=0.0.0.0 --port=8080 --call app:create_app"` |
| سرویس Stopped یا Paused می‌شود | `Get-Content logs\service.log -Tail 30`؛ آخر آن خطای پایتون یا Waitress است |
| `Only one usage of each socket address` | پورت ۸۰۸۰ را برنامهٔ دیگری (یا همان نسخهٔ دستی) گرفته: `Get-NetTCPConnection -LocalPort 8080 -State Listen` |
| در دستی کار می‌کند ولی در سرویس به ایزابل وصل نیست | سرویس با حساب SYSTEM اجرا می‌شود و `.env` را از کنار `app.py` می‌خواند؛ مسیر برنامه، دسترسی SYSTEM به `.env` (`icacls`)، و متغیر قدیمی در `nssm get HotelExtensions AppEnvironmentExtra` را بررسی کنید (متغیر سیستم بر `.env` برتری دارد) |
| سایت از سیستم دیگر باز نمی‌شود | بخش ۶: فایروال، `Listen` روی `0.0.0.0`، زیرشبکهٔ `-RemoteAddress`، VPN |
| ساعت‌ها غلط است | `tzdata` نصب باشد و `APP_TIMEZONE` درست (پیش‌فرض `Asia/Tehran`) |
| زنگولهٔ هشدار اعلان ویندوز نمی‌دهد | روی `http://<IP>:8080` مرورگر اعلان سیستم را نمی‌دهد؛ HTTPS یا `localhost` لازم است (بخش ۱۰) |

## ۵. بکاپ

از پنل مدیریت (منوی «مدیریت» → «دانلود بکاپ دیتابیس») می‌شه فایل کامل دیتابیس را
دستی گرفت؛ این بکاپ با API خود SQLite گرفته می‌شود و حتی وسط نوشتن هم سالم است. برای خیال راحت، پیشنهاد می‌کنم یک Task Scheduler (ویندوز) یا cron
(لینوکس) بذارید که هر شب از دیتابیس نسخه بگیرد، ترجیحاً با `sqlite3 hotel_extensions.db ".backup 'مسیر-مقصد.db'"`
(کپی ساده‌ی فایل وقتی برنامه در حال نوشتن است ممکن است فایل ناقص بدهد).

## ۶. فایروال

فقط پورتی که ری‌ورس‌پروکسی رویش گوش می‌ده (۸۰ یا ۴۴۳) را برای شبکه‌ی داخلی هتل باز
کنید؛ پورت داخلی Waitress/Gunicorn (مثلاً ۸۰۸۰) نباید مستقیماً از بیرون آن سرور
قابل دسترس باشد.

## ۷. اتصال به ایزابل (PBX)

اتصال فقط‌خواندنی است: برنامه با AMI فقط دو اکشن `SIPpeers` و `SIPshowpeer` را اجرا می‌کند و هیچ‌چیز را در ایزابل تغییر نمی‌دهد. هر دور یک اتصال کوتاه باز می‌کند و می‌بندد. همگام‌سازی در یک thread پس‌زمینه داخل خود برنامه اجرا می‌شود و وضعیتش در پنل مدیریت ← «اتصال به PBX» دیده می‌شود. چون وضعیت داخل حافظهٔ همین پروسه است، **باید با یک worker اجرا شود** (`-w 1` در Gunicorn، یا یک پروسهٔ Waitress).

### ۱. ساخت کاربر AMI مخصوص برنامه

IssabelPBX فایل `manager.conf` را خودش بازنویسی می‌کند. کاربر سفارشی را در `/etc/asterisk/manager_custom.conf` بگذارید (اگر این فایل در `manager.conf` با `#include` نیامده بود، اول آن را بررسی کنید):

```
[hotelapp]
secret = یک-رمز-طولانی-و-تصادفی
deny = 0.0.0.0/0.0.0.0
permit = 192.168.140.1/255.255.255.255
write = reporting
```

- `permit` را روی IP **سرور برنامه** بگذارید (اینجا `192.168.140.1`)؛ هیچ دستگاه دیگری نباید بتواند با این کاربر وارد شود.
- خط `read` را ننویسید. برنامه با `Events: off` وارد می‌شود و به هیچ رویدادی نیاز ندارد.
- `write = reporting` کافی است: در سورس Asterisk 18.6.0 هر دو اکشن به `system` **یا** `reporting` نیاز دارند.
- بعد از ذخیرهٔ فایل: `asterisk -rx "manager reload"`.
- در خروجی `manager show settings` دیده شد که AMI روی `0.0.0.0:5038` گوش می‌دهد. پس مطمئن شوید کاربر دیگری با رمز پیش‌فرض یا ضعیف در `manager.conf` / `manager_additional.conf` نیست.

### ۲. فایروال

- در فایروال ایزابل (Security ← Firewall) پورت TCP `5038` را فقط برای `192.168.140.1` باز کنید.
- اگر روی میکروتیک بین VLANها قانون فیلتر دارید، همین مسیر را اجازه بدهید (و بقیه را نه).
- تست از سرور برنامه: `Test-NetConnection 192.168.228.2 -Port 5038` (ویندوز) یا `nc -vz 192.168.228.2 5038` (لینوکس).

### ۳. تنظیم برنامه

متغیرهای `PBX_AMI_HOST`، `PBX_AMI_USER` و `PBX_AMI_SECRET` را (طبق جدول بخش ۳) روی سرور برنامه ست کنید و برنامه را ری‌استارت کنید. بعد در پنل مدیریت ← «اتصال به PBX» دکمهٔ «همگام‌سازی الان» را بزنید.

### رفتار همگام‌سازی

- داخلی‌هایی که روی PBX هستند ولی در دفترچه نیستند خودکار ساخته می‌شوند و **از پرسنل مخفی‌اند**: طبق الگوی شماره «اتاق مهمان»، در غیر این‌صورت «در انتظار بررسی». نوع آن‌ها را از صفحهٔ PBX تأیید کنید.
- داخلی‌های سطل بازیافت دست نمی‌خورند. ترانک‌ها و peerهایی که نامشان عدد نیست نادیده گرفته می‌شوند.
- مدل تلفن و MAC فقط وقتی از User-Agent پر می‌شوند که خالی باشند؛ مقدار ثبت‌شده‌ی خودتان هیچ‌وقت بازنویسی نمی‌شود. اگر MACِ خود تلفن (مثل Htek) با MAC ثبت‌شده فرق داشته باشد، در صفحهٔ PBX هشدار می‌بینید.
- فیلد `updated_at` (آخرین ویرایش) با همگام‌سازی عوض نمی‌شود.
- اگر PBX در دسترس نباشد، آخرین وضعیت می‌ماند، پس از `PBX_STATUS_STALE_MINUTES` دقیقه «نامشخص» نمایش داده می‌شود، و خطا فقط در صفحهٔ PBX دیده می‌شود.


## ۸. اتصال از راه وب‌پنل ایزابل (بدون AMI)

اگر به SSH، روت و فایل‌های Asterisk دسترسی ندارید، برنامه می‌تواند مثل یک مرورگر وارد وب‌پنل ایزابل شود و وضعیت داخلی‌ها را از «Operator Panel» (`menu=control_panel`) بخواند. چیزی روی ایزابل تغییر نمی‌کند؛ فقط یک ورود و خواندن اولین پیام جریان وضعیت (و بعد همان تماس بستن خود پنل).

۱. یک کاربر وب‌پنل بسازید که به ماژول **Operator Panel** دسترسی داشته باشد (ترجیحاً غیر از `admin`).

۲. متغیرها را ست کنید (نمونه در `.env.example`؛ برنامه فقط متغیرهای محیطی را می‌خواند، پس اگر از فایل `.env` استفاده می‌کنید باید ابزار اجرای سرویس آن را لود کند):

```
PBX_WEB_HOST=https://192.168.1.100
PBX_WEB_USER=monitor
PBX_WEB_PASS=...
```

۳. قبل از راه‌اندازی، با ابزار کمکی بسنجید که ورود و خواندن صفحه درست است:

```
python tools/check_issabel_web.py          # نتیجهٔ فهمیده‌شده را چاپ می‌کند
python tools/check_issabel_web.py --raw    # شروع متن صفحه را هم نشان می‌دهد
```

با Operator Panel فقط «ثبت‌شده/ثبت‌نشده» و IP به‌دست می‌آید و تأخیر (ms) در دسترس نیست؛ `rtt_ms` خالی می‌ماند و نشان زرد «تأخیر بالا» نمایش داده نمی‌شود.

اگر ورود جواب داد ولی «no SIP peers found» دیدید (فقط با مسیر قدیمی Asterisk Info)، نسخهٔ ایزابل شما صفحه را در آدرس یا قالب دیگری می‌دهد؛ با `--raw` بیینید صفحه چه می‌گوید و `PBX_WEB_PEERS_PATH` را درست کنید. پارسر خروجی `sip show peers` را هم به‌صورت متن داخل `<pre>` و هم به‌صورت جدول HTML می‌خواند (و `pjsip show endpoints` را در حد وضعیت).

نکته‌ها:

- نشست ورود بین دورها نگه داشته می‌شود و اگر منقضی شد خودکار دوباره وارد می‌شود. اگر نام کاربری/رمز رد شد، تا ۵ دقیقه دوباره امتحان نمی‌شود تا حساب قفل نشود.
- وضعیت‌ها همان ستون‌های قبلی دیتابیس‌اند: `reg_status` (online/offline/unknown)، `rtt_ms` (تأخیر) و `status_updated_at` (آخرین استعلام موفق). با این منبع، User-Agent خوانده نمی‌شود، پس مدل و MAC تلفن‌ها دست‌نخورده می‌مانند.
- صفحهٔ خالی یا بدون Peer خطا حساب می‌شود، نه «همهٔ داخلی‌ها رفته‌اند».
- اگر ایزابل در دسترس نباشد برنامه کرش نمی‌کند، خطا در `logs/app.log` و صفحهٔ PBX ثبت می‌شود، و آخرین وضعیت پس از `PBX_STATUS_STALE_MINUTES` دقیقه «نامشخص» می‌شود (زمان آخرین استعلام موفق حفظ می‌ماند).
- نشان‌های وضعیت در صفحهٔ اصلی هر `PBX_SYNC_INTERVAL` ثانیه (حداقل ۱۵) بدون بارگذاری مجدد صفحه تازه می‌شوند. داده از `GET /api/extensions/status` می‌آید: عموم فقط وضعیت داخلی‌های پرسنل را می‌بینند؛ مدیر وارد‌شده تأخیر، IP و اتاق‌ها را هم می‌گیرد.
- `POST /api/pbx/sync-now` (فقط مدیر وارد‌شده، با هدر `X-CSRFToken`) یک دور همگام‌سازی را همین الان اجرا می‌کند و خلاصه را JSON برمی‌گرداند.

---

## ۹. لاگ قطعی تلفن‌ها (`logs/Log.txt`)

وقتی همگام‌سازی ایزابل فعال باشد، برنامه هر قطع و وصل شدن تلفن‌ها را ثبت می‌کند:

- **در دیتابیس** (جدول `PhoneOutage`): هر قطعی یک ردیف دارد: زمان قطع، زمان وصل و مدت. تا وقتی تلفن وصل نشده، ردیف «باز» است.
- **در فایل متنی** `logs/Log.txt` (کنار برنامه؛ با `PBX_OUTAGE_LOG` می‌توان مسیر را عوض کرد). حجم فایل محدود است (۵ مگابایت × ۵ فایل قدیمی). فایل UTF-8 است و با Notepad ویندوز فارسی را درست نشان می‌دهد.

نمونه خطوط:

```
2026-10-16 14:03:22 | OFFLINE  | 255 | 192.168.225.66 | down since 2026-10-16 14:02:52 | پذیرش 3
2026-10-16 14:16:03 | ONLINE   | 255 | 192.168.225.66 | was down 00:13:11 | پذیرش 3
2026-10-16 14:20:10 | PBX-LOST | - | - | cannot read the PBX: ... | -
2026-10-16 14:23:20 | PBX-OK   | - | - | reachable again after 00:03:10 | -
```

زمان‌ها به وقت محلی (`APP_TIMEZONE`) هستند. `STOPPED` یعنی پایش قطعیِ آن تلفن تمام شد بدون اینکه وصل شود (داخلی غیرفعال، در سطل بازیافت یا حذف‌شده از ایزابل).

### قواعد تشخیص

- فقط تلفنی که **قبلاً آنلاین دیده شده** و بعد آفلاین شده «قطعی» حساب می‌شود. تلفنی که از همان ابتدا آفلاین بوده، رکوردی نمی‌سازد.
- تلفن باید `PBX_OUTAGE_CONFIRM_SYNCS` همگام‌سازی پشت‌سرهم (پیش‌فرض ۲، حدود یک دقیقه) آفلاین بماند. قطع‌ووصل کوتاه‌تر هیچ ردی نمی‌گذارد.
- فقط داخلی‌های **فعال** با نوع `PBX_OUTAGE_KINDS` (پیش‌فرض پرسنل و اتاق) پایش می‌شوند.
- اگر ارتباط با خود ایزابل قطع شود، برای تلفن‌ها قطعی ثبت نمی‌شود؛ فقط خط `PBX-LOST` و بعداً `PBX-OK` نوشته می‌شود.
- اگر برنامه بیش از `PBX_STATUS_STALE_MINUTES` (پیش‌فرض ۱۰ دقیقه) خاموش یا بی‌ارتباط بوده باشد، وضعیت قبلی معتبر نیست و قطعی‌ای که در این فاصله رخ داده ثبت نمی‌شود.
- «آفلاین» یعنی تلفن روی ایزابل ثبت‌نام‌نشده است. اگر برق تلفن برود ولی ایزابل هنوز ثبت‌نامش را نگه داشته باشد، قطعی با همان تأخیر دیده می‌شود.