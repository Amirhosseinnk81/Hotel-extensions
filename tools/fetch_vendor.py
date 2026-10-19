"""Download the Bootstrap and font files the pages use into static/vendor/ (needs internet once).

    python tools/fetch_vendor.py

After this the site works without internet access. Until the files are there, the pages load the
same Bootstrap version from the jsDelivr CDN instead (see vendor_asset_url in app.py).
Each download is checked (size and file signature) before it is saved; existing good files are kept.
"""
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
VENDOR = os.path.join(ROOT, 'static', 'vendor')
BOOT = 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/'


def font(pkg, name):
    return f'https://cdn.jsdelivr.net/npm/@fontsource-variable/{pkg}/files/{name}.woff2'


# (path under static/vendor, url, signature the file must start with, minimum size in bytes)
FILES = [
    ('bootstrap.min.css', BOOT + 'css/bootstrap.min.css', b'/*!', 100_000),
    ('bootstrap.rtl.min.css', BOOT + 'css/bootstrap.rtl.min.css', b'/*!', 100_000),
    ('bootstrap.bundle.min.js', BOOT + 'js/bootstrap.bundle.min.js', b'/*!', 50_000),
    ('fonts/vazirmatn-arabic-wght-normal.woff2', font('vazirmatn', 'vazirmatn-arabic-wght-normal'), b'wOF2', 10_000),
    ('fonts/inter-latin-wght-normal.woff2', font('inter', 'inter-latin-wght-normal'), b'wOF2', 10_000),
]


def http_get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Hotel-extensions vendor fetch'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def is_good(data, signature, minimum):
    return len(data) >= minimum and data.startswith(signature)


def fetch_all(vendor_dir=VENDOR, files=FILES, download=http_get, log=print):
    """Returns the names that could not be fetched (empty list = all fine)."""
    failed = []
    for name, url, signature, minimum in files:
        target = os.path.join(vendor_dir, *name.split('/'))
        if os.path.isfile(target):
            with open(target, 'rb') as handle:
                if is_good(handle.read(), signature, minimum):
                    log(f'ok (already there)  {name}')
                    continue
        try:
            data = download(url)
        except Exception as exc:  # network errors of every kind
            log(f'FAILED  {name}: {exc.__class__.__name__}: {str(exc)[:100]}')
            failed.append(name)
            continue
        if not is_good(data, signature, minimum):
            log(f'FAILED  {name}: the download does not look like the expected file ({len(data)} bytes)')
            failed.append(name)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as handle:
            handle.write(data)
        log(f'saved   {name} ({len(data)} bytes)')
    return failed


def main():
    failed = fetch_all()
    if failed:
        print('\nSome files were not saved. The site still works while this computer has internet access (CDN fallback);')
        print('for offline use, run this again from a computer with internet and copy static/vendor/ over.')
        sys.exit(1)
    print('\nAll files are in static/vendor/. Restart the app.')


if __name__ == '__main__':
    main()
