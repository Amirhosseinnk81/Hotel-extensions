(function () {
  'use strict';

  var THEME_KEY = 'hp_theme';
  var FAV_KEY = 'hp_favorites';
  var VIEW_KEY = 'hp_view_mode';
  var STATE_FILTER_KEY = 'hp_status_filter';
  var ADMIN_VIEW_KEY = 'hp_admin_view';
  var bulkRefresh = null;  // set by initBulkSelect; the admin list calls it after its rows change

  /* ---------------- Translation (English UI only; Persian is the source text) ---------------- */
  var I18N = window.HP_I18N || null;
  function tr(text) {
    if (!I18N || !text) return text;
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    var hit = I18N[m[2].replace(/[\u200c\u200e\u200f]/g, '')];
    return hit === undefined ? text : m[1] + hit + m[3];
  }

  /* ---------------- Small helpers ---------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function closest(node, sel) { return node && node.closest ? node.closest(sel) : null; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmtTime(iso) {
    if (!iso) return '';
    var d = new Date(String(iso).replace(/(\.\d{3})\d+/, '$1'));
    if (isNaN(d.getTime())) return '';
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /* ---------------- Toast notifications ---------------- */
  var TOAST_ICONS = { success: '✓', danger: '✕', warning: '!', info: 'i' };
  var TOAST_ALIASES = { error: 'danger', message: 'info', primary: 'info', secondary: 'info' };

  function toast(message, type, opts) {
    var stack = $('#toastStack');
    if (!stack || !message) return null;
    type = TOAST_ALIASES[type] || type || 'info';
    if (!TOAST_ICONS[type]) type = 'info';
    opts = opts || {};
    var duration = opts.duration || (type === 'danger' || type === 'warning' ? 8000 : 4500);

    while (stack.children.length >= 4) removeToast(stack.firstElementChild, true);

    var item = make('div', 'toast-item toast-' + type);
    item.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    item.appendChild(make('span', 'toast-icon', TOAST_ICONS[type]));
    item.appendChild(make('div', 'toast-body', message));
    var close = make('button', 'toast-close', '✕');
    close.type = 'button';
    close.setAttribute('aria-label', tr('بستن'));
    close.addEventListener('click', function () { removeToast(item); });
    item.appendChild(close);

    var timer = null;
    function arm(ms) { clearTimeout(timer); timer = setTimeout(function () { removeToast(item); }, ms); }
    item.addEventListener('mouseenter', function () { clearTimeout(timer); });
    item.addEventListener('mouseleave', function () { arm(2500); });
    item._clear = function () { clearTimeout(timer); };

    stack.appendChild(item);
    requestAnimationFrame(function () { item.classList.add('show'); });
    arm(duration);
    return item;
  }

  function removeToast(item, instant) {
    if (!item || item._removing) return;
    item._removing = true;
    if (item._clear) item._clear();
    if (instant) { item.remove(); return; }
    item.classList.remove('show');
    item.classList.add('leaving');
    setTimeout(function () { item.remove(); }, 220);
  }

  function initFlashToasts() {
    var seeds = $('#flashSeeds');
    if (!seeds) return;
    $$('[data-flash]', seeds).forEach(function (node, i) {
      var text = node.textContent.trim();
      var category = node.getAttribute('data-category');
      setTimeout(function () { toast(text, category); }, i * 120);
    });
    seeds.remove();
  }

  /* ---------------- Confirmation dialog (replaces window.confirm) ---------------- */
  var confirmCtx = null;

  function openConfirm(opts, onOk) {
    var root = $('#confirmDialog');
    if (!root) {
      if (window.confirm(opts.message || tr('ادامه می‌دهید؟'))) onOk();
      return;
    }
    $('#confirmTitle', root).textContent = opts.title || tr('تأیید عملیات');
    $('#confirmMessage', root).textContent = opts.message || '';
    var okBtn = $('[data-modal-confirm]', root);
    okBtn.textContent = opts.okLabel || tr('بله، ادامه بده');
    okBtn.className = 'btn ' + (opts.tone === 'primary' ? 'btn-primary' : 'btn-danger');
    root.setAttribute('data-tone', opts.tone === 'primary' ? 'primary' : 'danger');

    confirmCtx = { root: root, onOk: onOk, returnFocus: document.activeElement };
    root.hidden = false;
    document.documentElement.classList.add('ui-lock');
    requestAnimationFrame(function () {
      root.classList.add('open');
      var cancel = $('[data-modal-cancel]', root);
      if (cancel) cancel.focus();   // the safe choice is focused first
    });
  }

  function closeConfirm(accepted) {
    if (!confirmCtx) return;
    var ctx = confirmCtx;
    confirmCtx = null;
    ctx.root.classList.remove('open');
    document.documentElement.classList.remove('ui-lock');
    setTimeout(function () { ctx.root.hidden = true; }, 160);
    if (ctx.returnFocus && ctx.returnFocus.focus && !accepted) {
      try { ctx.returnFocus.focus(); } catch (e) {}
    }
    if (accepted) ctx.onOk();
  }

  function initConfirm() {
    var root = $('#confirmDialog');
    if (root) {
      root.addEventListener('click', function (e) {
        if (closest(e.target, '[data-modal-confirm]')) closeConfirm(true);
        else if (closest(e.target, '[data-modal-cancel]') || closest(e.target, '[data-modal-close]')) closeConfirm(false);
      });
      root.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { e.preventDefault(); closeConfirm(false); return; }
        if (e.key !== 'Tab') return;
        var focusables = $$('button', root).filter(function (b) { return !b.disabled; });
        if (!focusables.length) return;
        var first = focusables[0], last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      });
    }

    function describe(node) {
      return {
        message: node.getAttribute('data-confirm'),
        title: node.getAttribute('data-confirm-title'),
        okLabel: node.getAttribute('data-confirm-label'),
        tone: node.getAttribute('data-confirm-tone')
      };
    }

    /* Buttons carrying data-confirm (e.g. the bulk "move to trash" button, which has its own formaction) */
    document.addEventListener('click', function (e) {
      var btn = closest(e.target, 'button[data-confirm], input[data-confirm]');
      if (!btn || btn.disabled) return;
      if (btn._confirmed) { btn._confirmed = false; return; }   // second pass, let the click through
      e.preventDefault();
      openConfirm(describe(btn), function () { btn._confirmed = true; btn.click(); });
    });

    /* Whole forms carrying data-confirm (e.g. the per-row delete forms) */
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form || !form.hasAttribute || !form.hasAttribute('data-confirm')) return;
      if (form._confirmed) { form._confirmed = false; return; }
      e.preventDefault();
      openConfirm(describe(form), function () {
        form._confirmed = true;
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      });
    }, true);
  }

  /* ---------------- Loading state on submit buttons ---------------- */
  function startLoading(btn, text) {
    if (!btn || btn.getAttribute('data-loading-active') === '1') return;
    btn.setAttribute('data-loading-active', '1');
    btn._originalHtml = btn.innerHTML;
    btn.classList.add('is-loading');
    btn.innerHTML = '';
    btn.appendChild(make('span', 'btn-spinner'));
    btn.appendChild(make('span', 'btn-loading-text', text));
    btn.setAttribute('aria-busy', 'true');
    // disabled one tick later: a disabled submitter would be left out of the posted form data
    setTimeout(function () { btn.disabled = true; }, 0);
  }

  function stopLoading(btn) {
    if (!btn || btn.getAttribute('data-loading-active') !== '1') return;
    btn.removeAttribute('data-loading-active');
    btn.classList.remove('is-loading');
    btn.removeAttribute('aria-busy');
    if (btn._originalHtml !== undefined) btn.innerHTML = btn._originalHtml;
    btn.disabled = false;
  }

  function initLoadingButtons() {
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form || !form.getAttribute) return;
      var submitter = e.submitter || null;
      var wants = form.hasAttribute('data-loading') || (submitter && submitter.hasAttribute('data-loading-text'));
      if (!wants || e.defaultPrevented) return;
      if (form._submitting) { e.preventDefault(); return; }   // a second click while the first is in flight
      form._submitting = true;
      var btn = submitter && submitter.hasAttribute('data-loading-text') ? submitter
        : form.querySelector('[data-loading-text]') || submitter || form.querySelector('button[type="submit"], button:not([type])');
      var text = (btn && btn.getAttribute('data-loading-text')) || form.getAttribute('data-loading-text') || tr('در حال ارسال...');
      startLoading(btn, text);
    });

    // coming back with the browser's back button must not leave a stuck spinner
    window.addEventListener('pageshow', function (e) {
      if (!e.persisted) return;
      $$('[data-loading-active="1"]').forEach(stopLoading);
      $$('form').forEach(function (f) { f._submitting = false; });
    });
  }

  /* ---------------- Theme ---------------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('data-bs-theme', theme === 'dark' ? 'dark' : 'light');
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    $$('.theme-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.themeValue === theme);
    });
  }

  function initTheme() {
    var current = 'dark';
    try { current = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
    if (['light', 'dark', 'gold'].indexOf(current) === -1) current = 'dark';
    applyTheme(current);
    $$('.theme-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { applyTheme(btn.dataset.themeValue); });
    });
  }

  /* ---------------- Favorites ---------------- */
  function getFavorites() {
    try { return JSON.parse(localStorage.getItem(FAV_KEY) || '[]'); } catch (e) { return []; }
  }
  function saveFavorites(list) {
    try { localStorage.setItem(FAV_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function paintFavorites() {
    var favs = getFavorites();
    $$('.fav-btn').forEach(function (btn) {
      var on = favs.indexOf(btn.dataset.ext) !== -1;
      btn.classList.toggle('active', on);
      btn.textContent = on ? '★' : '☆';
    });
  }

  function initFavorites() {
    paintFavorites();
    document.addEventListener('click', function (e) {
      var btn = closest(e.target, '.fav-btn');
      if (!btn) return;
      var ext = btn.dataset.ext;
      var list = getFavorites();
      var idx = list.indexOf(ext);
      if (idx === -1) list.push(ext); else list.splice(idx, 1);
      saveFavorites(list);
      paintFavorites();
      if (directory.apply) directory.apply();
    });
  }

  /* ---------------- Copy to clipboard ---------------- */
  function initCopyButtons() {
    document.addEventListener('click', function (e) {
      var btn = closest(e.target, '.copy-btn');
      if (!btn) return;
      e.preventDefault();
      var ext = btn.dataset.ext;
      var done = function () {
        var original = btn.textContent;
        btn.textContent = '✓';
        btn.classList.add('copied');
        setTimeout(function () {
          btn.textContent = original;
          btn.classList.remove('copied');
        }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(ext).then(done).catch(done);
      } else {
        var temp = document.createElement('textarea');
        temp.value = ext;
        document.body.appendChild(temp);
        temp.select();
        try { document.execCommand('copy'); } catch (err) {}
        document.body.removeChild(temp);
        done();
      }
    });
  }

  /* ---------------- Print ---------------- */
  function initPrintButtons() {
    $$('.btn-print').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        window.print();
      });
    });
  }

  /* ---------------- Directory: instant search + status chips + favourites ---------------- */
  var directory = { apply: null, reload: null };

  function initDirectoryFilters() {
    var input = $('#instantSearch');
    var chips = $$('.filter-chip');
    var favBox = $('#onlyFavCheckbox');
    var emptyBox = $('#filterEmpty');
    var clearBtn = $('#searchClear');
    directory.apply = function () {};
    directory.reload = function () {};
    if (!$('#cardsView') && !$('#tableView')) return;

    var items = [];
    var phoneFilter = 'all';
    try {
      var saved = sessionStorage.getItem(STATE_FILTER_KEY);
      if (saved === 'online' || saved === 'offline') phoneFilter = saved;
    } catch (e) {}
    if (!chips.length) phoneFilter = 'all';

    // the result lists are replaced by the live search, so the item list is rebuilt after every swap
    function collect() {
      items = [];
      $$('[data-card-wrapper]').forEach(function (wrapper) {
        var card = $('.extension-card', wrapper);
        if (card) items.push({ node: wrapper, data: card, kind: 'card' });
      });
      $$('#tableView tbody tr[data-ext]').forEach(function (row) {
        items.push({ node: row, data: row, kind: 'row' });
      });
    }

    function syncSearchUi() {
      if (!input) return;
      if (clearBtn) clearBtn.hidden = !input.value;
      var iconBox = closest(input, '.input-icon');
      if (iconBox) iconBox.classList.toggle('has-value', !!input.value);
    }

    function apply() {
      var info = $('#filterInfo');   // lives inside the results header, which the live search swaps
      var favs = favBox && favBox.checked ? getFavorites() : null;
      var tally = { all: 0, online: 0, offline: 0 };
      var shownCards = 0, totalCards = 0, shownRows = 0, totalRows = 0;

      items.forEach(function (item) {
        var d = item.data.dataset;
        var base = !favs || favs.indexOf(d.ext) !== -1;
        var phone = d.phoneState || '';
        var show = base && (phoneFilter === 'all' || phone === phoneFilter);

        if (item.kind === 'card') {
          totalCards++;
          if (base) {
            tally.all++;
            if (phone === 'online') tally.online++;
            else if (phone === 'offline') tally.offline++;
          }
          if (show) shownCards++;
        } else {
          totalRows++;
          if (show) shownRows++;
        }
        item.node.classList.toggle('is-filtered', !show);
        if (!show && item.kind === 'row') closeDetailFor(item.node);
      });

      chips.forEach(function (chip) {
        var key = chip.getAttribute('data-filter');
        chip.setAttribute('aria-pressed', String(phoneFilter === key));
        chip.classList.toggle('active', phoneFilter === key);
        var count = $('[data-chip-count]', chip);
        if (count) count.textContent = String(tally[key] != null ? tally[key] : 0);
      });

      var total = totalCards || totalRows;
      var shown = totalCards ? shownCards : shownRows;
      var filtering = phoneFilter !== 'all' || favs !== null;
      if (info) {
        if (filtering && total) {
          info.textContent = tr('نمایش ') + shown + tr(' از ') + total + tr(' مورد این صفحه');
          info.hidden = false;
        } else {
          info.hidden = true;
        }
      }
      if (emptyBox) emptyBox.hidden = !(filtering && total > 0 && shown === 0);
    }

    function clearSearch() {
      if (!input || !input.value) return;
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));   // the live search listens on the form
    }

    if (input) {
      input.addEventListener('input', syncSearchUi);
      input.addEventListener('keydown', function (e) { if (e.key === 'Escape') clearSearch(); });
    }
    if (clearBtn) {
      clearBtn.addEventListener('click', function () { clearSearch(); input.focus(); });
    }
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        phoneFilter = chip.getAttribute('data-filter') || 'all';
        try { sessionStorage.setItem(STATE_FILTER_KEY, phoneFilter); } catch (e) {}
        apply();
      });
    });
    if (favBox) favBox.addEventListener('change', apply);

    // "/" jumps to the search box, like most dashboards
    document.addEventListener('keydown', function (e) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || !input) return;
      var tag = (e.target && e.target.tagName) || '';
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (e.target && e.target.isContentEditable)) return;
      e.preventDefault();
      input.focus();
      input.select();
    });

    directory.apply = apply;
    directory.reload = function () { collect(); syncSearchUi(); apply(); };
    directory.reload();
  }

  /* ---------------- Table rows: secondary columns collapse into an accordion on small screens ---------------- */
  function closeDetailFor(row) {
    var next = row.nextElementSibling;
    if (next && next.classList.contains('row-detail')) next.remove();
    row.classList.remove('is-open');
    var btn = $('.row-toggle', row);
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  function initRowToggles() {
    document.addEventListener('click', function (e) {
      var btn = closest(e.target, '.row-toggle');
      if (!btn) return;
      var row = closest(btn, 'tr');
      if (!row) return;
      var next = row.nextElementSibling;
      if (next && next.classList.contains('row-detail')) { closeDetailFor(row); return; }

      var d = row.dataset;
      var detail = make('tr', 'row-detail');
      var cell = make('td');
      cell.colSpan = row.children.length;
      var list = make('dl', 'row-detail-list');
      function add(label, value) {
        if (!value || value === '—') return;
        var box = make('div', 'row-detail-item');
        box.appendChild(make('dt', null, label));
        box.appendChild(make('dd', null, value));
        list.appendChild(box);
      }
      add(tr('بخش'), d.department);
      add(tr('نام مسئول'), d.person);
      add(tr('محل'), d.location);
      add(tr('وضعیت داخلی'), d.active === '1' ? tr('فعال') : (d.active === '0' ? tr('غیرفعال') : ''));
      add(tr('توضیحات'), d.notes);
      if (!list.children.length) add(tr('جزئیات'), tr('اطلاعات بیشتری ثبت نشده است.'));
      cell.appendChild(list);
      detail.appendChild(cell);
      row.parentNode.insertBefore(detail, row.nextSibling);
      row.classList.add('is-open');
      btn.setAttribute('aria-expanded', 'true');
    });
  }

  /* ---------------- Floating tooltip for status badges ---------------- */
  function initTooltips() {
    var tip = make('div', 'hp-tip');
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;
    document.body.appendChild(tip);
    var current = null;

    function place(target) {
      var r = target.getBoundingClientRect();
      tip.style.left = '0px';
      tip.style.top = '0px';
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var vw = document.documentElement.clientWidth;
      var left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), Math.max(8, vw - tw - 8));
      var top = r.top - th - 8;
      if (top < 8) top = r.bottom + 8;
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }
    function show(target) {
      var text = target.getAttribute('data-tip');
      if (!text) return;
      current = target;
      tip.textContent = text;
      tip.hidden = false;
      place(target);
    }
    function hide() { current = null; tip.hidden = true; }

    document.addEventListener('mouseover', function (e) {
      var t = closest(e.target, '[data-tip]');
      if (t && t !== current) show(t);
    });
    document.addEventListener('mouseout', function (e) {
      var t = closest(e.target, '[data-tip]');
      if (t && !(e.relatedTarget && t.contains(e.relatedTarget))) hide();
    });
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
    directory.refreshTip = function (target) { if (current === target) show(target); };
  }

  /* ---------------- Live update polling (lightweight, no WebSocket) ---------------- */
  function initUpdatePolling() {
    var body = document.body;
    var versionUrl = body.dataset.versionUrl;
    var initialVersion = body.dataset.version;
    if (!versionUrl || initialVersion === undefined) return;

    var toastEl = $('#updateToast');
    var btn = $('#updateToastBtn');
    var closeBtn = $('#updateToastClose');
    if (!toastEl) return;

    var dismissed = false;
    closeBtn.addEventListener('click', function () {
      dismissed = true;
      toastEl.hidden = true;
    });
    btn.addEventListener('click', function () { window.location.reload(); });

    setInterval(function () {
      if (dismissed || document.hidden) return;
      fetch(versionUrl).then(function (res) { return res.json(); }).then(function (data) {
        if (String(data.version) !== String(initialVersion)) toastEl.hidden = false;
      }).catch(function () {});
    }, 20000);
  }

  /* ---------------- Live phone-status badges (polls /api/extensions/status) ---------------- */
  function initPhoneStatusPolling() {
    var url = document.body.dataset.statusUrl;
    if (!url) return;
    var liveBar = $('#liveBar');
    var liveText = $('#liveText');
    var refreshBtn = $('#liveRefresh');
    if (!$('#cardsView') && !$('tr[data-ext]') && !$('.ext-tile') && !liveBar) return;

    // between 20 and 30 seconds, whatever the PBX sync interval is
    var configured = parseInt(document.body.dataset.statusInterval, 10) || 30;
    var interval = Math.min(30, Math.max(20, configured)) * 1000;
    var STATES = ['online', 'offline', 'unknown'];
    var failures = 0;
    var inFlight = false;

    function setLive(mode, text) {
      if (!liveBar) return;
      liveBar.setAttribute('data-state', mode);
      if (liveText) liveText.textContent = text;
    }

    function tipFor(info, data) {
      var lines = [tr(info.label || '')];
      var stamp = fmtTime(info.last_sync || data.last_sync);
      if (stamp) lines.push(tr('آخرین بررسی: ') + stamp);
      if (typeof info.latency_ms === 'number' && info.status === 'online') lines.push(tr('تأخیر: ') + info.latency_ms + ' ms');
      return lines.join('\n');
    }

    function paint(pill, info, data) {
      var state = STATES.indexOf(info.status) !== -1 ? info.status : 'unknown';
      var className = 'phone-pill ' + state + (info.slow ? ' slow' : '');
      var text = tr(info.label || '');
      var tip = tipFor(info, data);
      if (pill.className !== className) pill.className = className;
      if (pill.textContent !== text) pill.textContent = text;
      if (pill.getAttribute('data-tip') !== tip) {
        pill.setAttribute('data-tip', tip);
        if (directory.refreshTip) directory.refreshTip(pill);
      }
      return state;
    }

    function newPill() {
      var pill = document.createElement('span');
      pill.setAttribute('data-phone-pill', '');
      return pill;
    }

    function apply(data) {
      var statuses = data.statuses || {};
      var cards = $$('.extension-card');
      var rows = $$('tr[data-ext]');
      $$('.ext-tile').forEach(function (tile) {
        var info = statuses[tile.dataset.ext];
        STATES.forEach(function (st) { tile.classList.remove(st); });
        tile.classList.remove('slow');
        if (!info) return;
        tile.classList.add(STATES.indexOf(info.status) !== -1 ? info.status : 'unknown');
        if (info.slow) tile.classList.add('slow');
      });
      cards.forEach(function (card) {
        var info = statuses[card.dataset.ext];
        var pill = $('[data-phone-pill]', card);
        if (!info) {
          if (pill) pill.remove();
          card.dataset.phonestatus = '';
          card.dataset.phoneState = '';
          card.dataset.phonecheck = '';
          return;
        }
        if (!pill) {
          var col = $('.card-status-col', card);
          if (!col) return;
          pill = newPill();
          col.appendChild(pill);
        }
        card.dataset.phoneState = paint(pill, info, data);
        card.dataset.phonestatus = tr(info.label || '');
        card.dataset.phonecheck = fmtTime(info.last_sync || data.last_sync);
      });
      rows.forEach(function (row) {
        var cell = $('[data-phone-cell]', row);
        var info = statuses[row.dataset.ext];
        if (!cell) return;  // the table has no phone column until the page is reloaded after the first sync
        if (!info) {
          cell.textContent = '—';
          row.dataset.phoneState = '';
          row.dataset.phonestatus = '';
          return;
        }
        var pill = $('[data-phone-pill]', cell);
        if (!pill) {
          pill = newPill();
          cell.textContent = '';
          cell.appendChild(pill);
        }
        row.dataset.phoneState = paint(pill, info, data);
        row.dataset.phonestatus = tr(info.label || '');
      });
      if (directory.apply) directory.apply();
    }

    function poll(manual) {
      if (document.hidden && !manual) return;
      if (inFlight) return;
      inFlight = true;
      if (refreshBtn) refreshBtn.classList.add('spinning');
      fetch(url, { credentials: 'same-origin', cache: 'no-store' })
        .then(function (res) { if (!res.ok) throw new Error('http ' + res.status); return res.json(); })
        .then(function (data) {
          failures = 0;
          if (data && data.statuses) apply(data);
          var stamp = fmtTime(data && data.last_sync);
          setLive('ok', tr('بروزرسانی زنده') + (stamp ? tr(' · آخرین همگام‌سازی با PBX: ') + stamp : ''));
        })
        .catch(function () {
          failures++;
          // keep the badges that are showing; only tell the user after a second miss
          if (failures >= 2) setLive('error', tr('ارتباط با سرور برقرار نیست — نمایش آخرین وضعیت شناخته‌شده'));
        })
        .then(function () {
          inFlight = false;
          if (refreshBtn) refreshBtn.classList.remove('spinning');
        });
    }

    if (refreshBtn) refreshBtn.addEventListener('click', function () { poll(true); });
    setInterval(poll, interval);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    poll();
  }

  /* ---------------- Extension detail modal ---------------- */
  function initDetailModal() {
    var modalEl = $('#extensionDetailModal');
    if (!modalEl || typeof bootstrap === 'undefined') return;
    var modal = new bootstrap.Modal(modalEl);
    var fields = ['extension', 'title', 'person', 'department', 'location', 'email', 'mobile', 'hours', 'notes', 'created', 'updated', 'phonestatus', 'phonecheck'];

    document.addEventListener('click', function (e) {
      var card = closest(e.target, '.extension-card');
      if (!card) return;
      if (closest(e.target, '.fav-btn') || closest(e.target, '.copy-btn')) return;
      fields.forEach(function (key) {
        var node = $('[data-field="' + key + '"]', modalEl);
        if (node) node.textContent = card.dataset[key] || '—';
      });
      var presenceEl = $('[data-field="presence"]', modalEl);
      if (presenceEl) presenceEl.textContent = card.dataset.present === '1' ? tr('حاضر') : tr('خارج از شیفت');
      modal.show();
    });
  }

  /* ---------------- Live search: results update while typing, no button needed ---------------- */
  // opts: form (selector), loading (selector of the element dimmed meanwhile), marker (selector that must
  // exist in the answer, else it is not the list page), swaps (selectors whose content is replaced), after().
  function setupLiveSearch(opts) {
    var form = document.querySelector(opts.form);
    var host = document.querySelector(opts.loading);
    if (!form || !host || !window.fetch || !window.DOMParser || !window.URLSearchParams) return null;
    var DELAY = 300;  // ms of silence after the last keystroke before searching
    var timer = null, controller = null, seq = 0;

    function buildUrl() {
      var params = new URLSearchParams();
      new FormData(form).forEach(function (value, key) {
        value = String(value).trim();
        if (value !== '') params.append(key, value);
      });
      var qs = params.toString();
      return window.location.pathname + (qs ? '?' + qs : '');
    }

    function copyInner(doc, selector) {
      var target = document.querySelector(selector);
      var source = doc.querySelector(selector);
      if (target && source) target.innerHTML = source.innerHTML;
    }

    function search() {
      var url = buildUrl();
      var mine = ++seq;
      if (controller) controller.abort();
      controller = window.AbortController ? new AbortController() : null;
      host.classList.add('is-loading');
      fetch(url, { credentials: 'same-origin', signal: controller ? controller.signal : undefined })
        .then(function (res) {
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.text();
        })
        .then(function (html) {
          if (mine !== seq) return;  // a newer search is already on its way
          var doc = new DOMParser().parseFromString(html, 'text/html');
          if (!doc.querySelector(opts.marker)) {
            window.location.href = url;  // not the list page (login, error page): do a normal visit
            return;
          }
          opts.swaps.forEach(function (selector) { copyInner(doc, selector); });
          try { history.replaceState(null, '', url); } catch (e) {}
          if (opts.after) opts.after();
          host.classList.remove('is-loading');
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          if (mine === seq) host.classList.remove('is-loading');  // keep the old results on a failure
        });
    }

    function schedule(immediate) {
      clearTimeout(timer);
      timer = setTimeout(search, immediate ? 0 : DELAY);
    }

    form.addEventListener('input', function (e) {
      if (e.target.tagName === 'INPUT' && e.target.name && !e.isComposing) schedule(false);
    });
    form.addEventListener('compositionend', function () { schedule(false); });
    form.addEventListener('change', function (e) {
      if (e.target.tagName === 'SELECT') schedule(true);
    });
    form.addEventListener('submit', function (e) {  // Enter or the button: same search, no page reload
      e.preventDefault();
      schedule(true);
    });
    return { schedule: schedule };
  }

  function initLiveSearch() {
    setupLiveSearch({
      form: 'form.search-card',
      loading: '#cardsView',
      marker: '#tableView',
      swaps: ['#cardsView', '#tableView', '.results-bar > div:first-child', '#paginationHolder'],
      after: function () {
        paintFavorites();
        if (directory.reload) directory.reload();   // chips / favourites / counts for the new rows
      }
    });
  }

  /* ---------------- Admin extension list: compact tiles / table, live search ---------------- */
  function adminViewMode() {
    var mode = 'tiles';
    try { mode = localStorage.getItem(ADMIN_VIEW_KEY) || 'tiles'; } catch (e) {}
    return mode === 'table' ? 'table' : 'tiles';
  }

  // Both views carry the same rows; only the shown one is submitted (its checkboxes stay enabled).
  function applyAdminView(mode) {
    var tiles = document.getElementById('tilesView');
    var table = document.getElementById('adminTableView');
    if (!tiles || !table) return;
    var to = mode === 'table' ? table : tiles;
    var from = mode === 'table' ? tiles : table;
    var ticked = {};
    from.querySelectorAll('.row-select:checked').forEach(function (cb) { ticked[cb.value] = true; });
    to.querySelectorAll('.row-select').forEach(function (cb) { cb.disabled = false; cb.checked = !!ticked[cb.value] || cb.checked; });
    from.querySelectorAll('.row-select').forEach(function (cb) { cb.disabled = true; cb.checked = false; });
    to.style.display = '';
    from.style.display = 'none';
    document.querySelectorAll('.admin-view-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.adminView === mode);
    });
    if (bulkRefresh) bulkRefresh();
  }

  function initAdminList() {
    if (!document.getElementById('adminResults')) return;
    applyAdminView(adminViewMode());
    document.querySelectorAll('.admin-view-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        try { localStorage.setItem(ADMIN_VIEW_KEY, btn.dataset.adminView); } catch (e) {}
        applyAdminView(btn.dataset.adminView);
      });
    });

    // one shared form deletes a tile's extension, so 500+ tiles do not each carry a form
    document.addEventListener('click', function (e) {
      var btn = closest(e.target, '.ext-tile-del');
      if (!btn) return;
      e.preventDefault();
      var form = document.getElementById('tileDeleteForm');
      if (!form || !btn.dataset.deleteUrl) return;
      openConfirm({
        message: tr('این داخلی به سطل بازیافت منتقل شود؟ تا قبل از حذف نهایی می‌توانید آن را برگردانید.'),
        title: tr('انتقال به سطل بازیافت'),
        okLabel: tr('بله، منتقل کن')
      }, function () {
        form.action = btn.dataset.deleteUrl;
        form.submit();
      });
    });

    var form = document.querySelector('form.admin-search-bar');
    var clearBtn = document.getElementById('adminClear');
    function adminFilterFields() {
      return Array.prototype.filter.call(form.elements, function (el) { return el.tagName === 'SELECT'; });
    }
    function updateClear() {
      if (!form || !clearBtn) return;
      var q = form.elements.q ? form.elements.q.value.trim() : '';
      var anyFilter = adminFilterFields().some(function (el) { return el.value; });
      clearBtn.hidden = !(q || anyFilter);
    }
    var live = setupLiveSearch({
      form: 'form.admin-search-bar',
      loading: '#adminResults',
      marker: '#adminResults',
      swaps: ['#adminResults'],
      after: function () { applyAdminView(adminViewMode()); updateClear(); }
    });
    if (form) {
      form.addEventListener('input', updateClear);
      form.addEventListener('change', updateClear);
    }
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        if (form.elements.q) form.elements.q.value = '';
        adminFilterFields().forEach(function (el) { el.value = ''; });
        updateClear();
        if (live) live.schedule(true); else window.location.href = window.location.pathname;
      });
    }
  }

  /* ---------------- Admin: bulk select ---------------- */
  function initBulkSelect() {
    var selectAll = $('#selectAllRows');
    var countEl = $('#bulkSelectedCount');
    var bulkForm = $('#bulkForm');
    if (!selectAll || !bulkForm) return;
    var actionButtons = $$('button[type="submit"]', bulkForm);

    function rowCheckboxes() { return $$('.row-select:not(:disabled)'); }

    function refresh() {
      var checked = rowCheckboxes().filter(function (cb) { return cb.checked; });
      if (countEl) countEl.textContent = checked.length;
      actionButtons.forEach(function (btn) { btn.disabled = checked.length === 0; });
      var all = rowCheckboxes();
      selectAll.checked = all.length > 0 && checked.length === all.length;
    }

    selectAll.addEventListener('change', function () {
      rowCheckboxes().forEach(function (cb) { cb.checked = selectAll.checked; });
      refresh();
    });

    document.addEventListener('change', function (e) {
      if (e.target.classList && e.target.classList.contains('row-select')) refresh();
    });

    bulkRefresh = refresh;
    refresh();
  }

  /* ---------------- Card/table view toggle ---------------- */
  function applyViewMode(mode) {
    var cardsView = $('#cardsView');
    var tableView = $('#tableView');
    if (!cardsView || !tableView) return;
    cardsView.style.display = mode === 'table' ? 'none' : '';
    tableView.style.display = mode === 'table' ? '' : 'none';
    $$('.view-toggle-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.view === mode);
    });
  }

  function initViewToggle() {
    var buttons = $$('.view-toggle-btn');
    if (!buttons.length) return;
    var current = 'cards';
    try { current = localStorage.getItem(VIEW_KEY) || 'cards'; } catch (e) {}
    applyViewMode(current);
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var mode = btn.dataset.view;
        try { localStorage.setItem(VIEW_KEY, mode); } catch (e) {}
        applyViewMode(mode);
      });
    });
  }

  /* ---------------- Outage report page: live filters, ticking durations ---------------- */
  function fmtDuration(total) {
    total = Math.max(0, Math.floor(total));
    var days = Math.floor(total / 86400);
    var rest = total % 86400;
    var clock = pad(Math.floor(rest / 3600)) + ':' + pad(Math.floor((rest % 3600) / 60)) + ':' + pad(rest % 60);
    return days ? days + 'd ' + clock : clock;
  }

  function isoDay(date) {
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
  }

  function initOutagePage() {
    var form = $('form.outage-filters');
    if (!form) return;
    var live = setupLiveSearch({
      form: 'form.outage-filters',
      loading: '#outageResults',
      marker: '#outageResults',
      swaps: ['#outageStats', '#outageResults']
    });

    function markChips() {
      var from = form.elements['from'].value, to = form.elements['to'].value;
      $$('.range-chip', form).forEach(function (chip) {
        var start = new Date();
        start.setDate(start.getDate() - (parseInt(chip.getAttribute('data-range'), 10) || 0));
        chip.classList.toggle('active', from === isoDay(start) && to === isoDay(new Date()));
      });
    }

    form.addEventListener('click', function (e) {
      var chip = closest(e.target, '[data-range]');
      if (!chip) return;
      var start = new Date();
      start.setDate(start.getDate() - (parseInt(chip.getAttribute('data-range'), 10) || 0));
      form.elements['from'].value = isoDay(start);
      form.elements['to'].value = isoDay(new Date());
      markChips();
      if (live) live.schedule(true);
    });
    form.addEventListener('input', markChips);

    var clear = $('#outageClear');
    if (clear) {
      clear.addEventListener('click', function () {
        form.elements['q'].value = '';
        form.elements['from'].value = '';
        form.elements['to'].value = '';
        form.elements['ongoing'].checked = false;
        markChips();
        if (live) live.schedule(true);
      });
    }
    markChips();

    // the duration of an outage that is still going on climbs every second without asking the server
    setInterval(function () {
      $$('[data-since]').forEach(function (cell) {
        var started = Date.parse(String(cell.getAttribute('data-since')).replace(/(\.\d{3})\d+/, '$1'));
        if (isNaN(started)) return;
        var holder = $('bdi', cell) || cell;
        holder.textContent = fmtDuration((Date.now() - started) / 1000);
      });
    }, 1000);

    // new outages and recoveries appear without touching the page
    setInterval(function () {
      if (!document.hidden && live) live.schedule(true);
    }, 30000);
  }

  /* ---------------- Outage alerts: Windows notifications through the browser ---------------- */
  // The server decides what is worth an alert (summaries included); this only shows them.
  // Browsers allow system notifications on HTTPS pages and on localhost only; elsewhere the alert is an in-page toast.
  function initOutageAlerts() {
    var url = document.body.dataset.alertsUrl;
    var btn = $('#alertToggle');
    if (!url || !btn) return;
    var KEY = 'hp_alerts';
    var LAST = 'hp_alerts_last';
    var hasApi = 'Notification' in window;
    var secure = !!window.isSecureContext;
    var timer = null;
    var busy = false;

    function systemReady() { return hasApi && secure && Notification.permission === 'granted'; }
    function isOn() { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } }
    function setOn(on) { try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) {} }
    function getLast() {
      var n = NaN;
      try { n = parseInt(localStorage.getItem(LAST), 10); } catch (e) {}
      return isNaN(n) ? null : n;
    }
    function setLast(n) { try { localStorage.setItem(LAST, String(n)); } catch (e) {} }

    function paint() {
      var on = isOn();
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', String(on));
      var mode = !on ? tr('خاموش') : (systemReady() ? tr('روشن — اعلان ویندوز') : tr('روشن — فقط داخل همین صفحه'));
      btn.title = tr('هشدار قطعی تلفن‌ها (') + mode + ')';
    }

    function show(alert) {
      if (systemReady()) {
        try {
          var note = new Notification(alert.title, { body: alert.body || '', tag: alert.tag, silent: !!alert.quiet, lang: 'fa', dir: 'rtl' });
          note.onclick = function () {
            window.focus();
            note.close();
            if (alert.url && window.location.pathname !== alert.url) window.location.href = alert.url;
          };
          return;
        } catch (e) { /* fall back to the in-page toast */ }
      }
      toast(alert.title + (alert.body ? ' — ' + alert.body : ''), alert.quiet ? 'success' : 'danger', { duration: alert.quiet ? 6000 : 12000 });
    }

    function poll() {
      if (!isOn() || busy) return;
      busy = true;
      var after = getLast();
      fetch(url + (after === null ? '' : '?after=' + after), { credentials: 'same-origin', cache: 'no-store' })
        .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
        .then(function (data) {
          if (!data || data.ok === false) return;
          var seen = getLast();   // another tab may have shown some of these already
          (data.alerts || []).forEach(function (alert) {
            if (seen === null || alert.id > seen) show(alert);
          });
          if (typeof data.latest_id === 'number') setLast(data.latest_id);
        })
        .catch(function () { /* the next round tries again */ })
        .then(function () { busy = false; });
    }

    function start() {
      clearInterval(timer);
      poll();
      timer = setInterval(poll, 15000);
    }

    function turnOn() {
      var finish = function () {
        try { localStorage.removeItem(LAST); } catch (e) {}   // start from "now": no alerts for what happened while it was off
        setOn(true);
        paint();
        start();
      };
      if (!hasApi) {
        toast(tr('این مرورگر اعلان سیستم را پشتیبانی نمی‌کند؛ هشدار فقط داخل همین صفحه نشان داده می‌شود.'), 'warning', { duration: 12000 });
        return finish();
      }
      if (!secure) {
        toast(tr('مرورگر روی آدرس http (غیر از localhost) اعلان ویندوز نمی‌دهد. برای اعلان، سایت را با HTTPS یا از روی localhost باز کنید. فعلاً هشدار فقط داخل همین صفحه نشان داده می‌شود.'), 'warning', { duration: 15000 });
        return finish();
      }
      if (Notification.permission === 'granted') {
        toast(tr('هشدار قطعی تلفن‌ها روشن شد.'), 'success');
        return finish();
      }
      if (Notification.permission === 'denied') {
        toast(tr('اعلان برای این سایت در مرورگر مسدود است (قفل کنار آدرس ← Notifications ← Allow). فعلاً هشدار فقط داخل همین صفحه نشان داده می‌شود.'), 'warning', { duration: 15000 });
        return finish();
      }
      var answered = false;
      var done = function (permission) {
        if (answered) return;
        answered = true;
        if (permission === 'granted') toast(tr('هشدار قطعی تلفن‌ها روشن شد.'), 'success');
        else toast(tr('اجازهٔ اعلان داده نشد؛ هشدار فقط داخل همین صفحه نشان داده می‌شود.'), 'warning', { duration: 12000 });
        finish();
      };
      var request = Notification.requestPermission(done);   // old browsers use the callback, new ones the promise
      if (request && request.then) request.then(done);
    }

    btn.addEventListener('click', function () {
      if (isOn()) {
        setOn(false);
        clearInterval(timer);
        paint();
        toast(tr('هشدار قطعی تلفن‌ها خاموش شد.'), 'info');
      } else {
        turnOn();
      }
    });

    paint();
    if (isOn()) start();
  }

  window.HotelUI = { toast: toast, confirm: openConfirm };

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initConfirm();
    initLoadingButtons();
    initFlashToasts();
    initTooltips();
    initFavorites();
    initDirectoryFilters();
    initRowToggles();
    initCopyButtons();
    initPrintButtons();
    initUpdatePolling();
    initPhoneStatusPolling();
    initDetailModal();
    initBulkSelect();
    initViewToggle();
    initLiveSearch();
    initAdminList();
    initOutagePage();
    initOutageAlerts();
  });
})();
