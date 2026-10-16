(function () {
  'use strict';

  var THEME_KEY = 'hp_theme';
  var FAV_KEY = 'hp_favorites';
  var ADMIN_VIEW_KEY = 'hp_admin_view';
  var bulkRefresh = null;  // set by initBulkSelect; the admin list calls it after its rows change

  /* ---------------- Theme ---------------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    document.querySelectorAll('.theme-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.themeValue === theme);
    });
  }

  function initTheme() {
    var current = 'light';
    try { current = localStorage.getItem(THEME_KEY) || 'light'; } catch (e) {}
    applyTheme(current);
    document.querySelectorAll('.theme-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        applyTheme(btn.dataset.themeValue);
      });
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
    document.querySelectorAll('.fav-btn').forEach(function (btn) {
      var on = favs.indexOf(btn.dataset.ext) !== -1;
      btn.classList.toggle('active', on);
      btn.textContent = on ? '\u2605' : '\u2606';
    });
  }

  function initFavorites() {
    paintFavorites();
    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.fav-btn') : null;
      if (!btn) return;
      var ext = btn.dataset.ext;
      var list = getFavorites();
      var idx = list.indexOf(ext);
      if (idx === -1) list.push(ext); else list.splice(idx, 1);
      saveFavorites(list);
      paintFavorites();
      applyFavoriteFilter();
    });
  }

  function applyFavoriteFilter() {
    var checkbox = document.getElementById('onlyFavCheckbox');
    if (!checkbox) return;
    var favs = getFavorites();
    var onlyFav = checkbox.checked;
    document.querySelectorAll('.extension-card').forEach(function (card) {
      var wrapper = card.closest('[data-card-wrapper]');
      if (!wrapper) return;
      var ext = card.dataset.ext;
      var show = !onlyFav || favs.indexOf(ext) !== -1;
      wrapper.style.display = show ? '' : 'none';
    });
  }

  function initFavoriteFilterToggle() {
    var checkbox = document.getElementById('onlyFavCheckbox');
    if (!checkbox) return;
    checkbox.addEventListener('change', applyFavoriteFilter);
    applyFavoriteFilter();
  }

  /* ---------------- Copy to clipboard ---------------- */
  function initCopyButtons() {
    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.copy-btn') : null;
      if (!btn) return;
      e.preventDefault();
      var ext = btn.dataset.ext;
      var done = function () {
        var original = btn.textContent;
        btn.textContent = '\u2713';
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
    document.querySelectorAll('.btn-print').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        window.print();
      });
    });
  }

  /* ---------------- Live update polling (lightweight, no WebSocket) ---------------- */
  function initUpdatePolling() {
    var body = document.body;
    var versionUrl = body.dataset.versionUrl;
    var initialVersion = body.dataset.version;
    if (!versionUrl || initialVersion === undefined) return;

    var toast = document.getElementById('updateToast');
    var btn = document.getElementById('updateToastBtn');
    var closeBtn = document.getElementById('updateToastClose');
    if (!toast) return;

    var dismissed = false;
    closeBtn.addEventListener('click', function () {
      dismissed = true;
      toast.hidden = true;
    });
    btn.addEventListener('click', function () {
      window.location.reload();
    });

    setInterval(function () {
      if (dismissed) return;
      fetch(versionUrl).then(function (res) { return res.json(); }).then(function (data) {
        if (String(data.version) !== String(initialVersion)) {
          toast.hidden = false;
        }
      }).catch(function () {});
    }, 20000);
  }

  /* ---------------- Live phone-status badges (polls /api/extensions/status) ---------------- */
  function initPhoneStatusPolling() {
    var url = document.body.dataset.statusUrl;
    if (!url) return;
    if (!document.getElementById('cardsView') && !document.querySelector('tr[data-ext]') && !document.querySelector('.ext-tile')) return;
    var interval = Math.max(15, parseInt(document.body.dataset.statusInterval, 10) || 30) * 1000;
    var STATES = ['online', 'offline', 'unknown'];

    function paint(pill, info) {
      var state = STATES.indexOf(info.status) !== -1 ? info.status : 'unknown';
      pill.className = 'phone-pill ' + state + (info.slow ? ' slow' : '');
      pill.textContent = info.label || '';
    }

    function ensurePill(parent) {
      var pill = parent.querySelector('[data-phone-pill]');
      if (!pill) {
        pill = document.createElement('span');
        pill.setAttribute('data-phone-pill', '');
        parent.textContent = '';
        parent.appendChild(pill);
      }
      return pill;
    }

    function apply(statuses) {
      var cards = document.querySelectorAll('.extension-card');
      var rows = document.querySelectorAll('tr[data-ext]');
      document.querySelectorAll('.ext-tile').forEach(function (tile) {
        var info = statuses[tile.dataset.ext];
        STATES.forEach(function (s) { tile.classList.remove(s); });
        tile.classList.remove('slow');
        if (!info) return;
        tile.classList.add(STATES.indexOf(info.status) !== -1 ? info.status : 'unknown');
        if (info.slow) tile.classList.add('slow');
      });
      cards.forEach(function (card) {
        var info = statuses[card.dataset.ext];
        var pill = card.querySelector('[data-phone-pill]');
        if (!info) {
          if (pill) pill.remove();
          card.dataset.phonestatus = '';
          return;
        }
        if (!pill) {
          var col = card.querySelector('.card-status-col');
          if (!col) return;
          pill = document.createElement('span');
          pill.setAttribute('data-phone-pill', '');
          col.appendChild(pill);
        }
        paint(pill, info);
        card.dataset.phonestatus = info.label || '';
      });
      rows.forEach(function (row) {
        var cell = row.querySelector('[data-phone-cell]');
        if (!cell) return;  // the table has no phone column until the page is reloaded after the first sync
        var info = statuses[row.dataset.ext];
        if (!info) { cell.textContent = '—'; return; }
        paint(ensurePill(cell), info);
      });
    }

    function poll() {
      if (document.hidden) return;
      fetch(url, { credentials: 'same-origin', cache: 'no-store' })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) { if (data && data.statuses) apply(data.statuses); })
        .catch(function () {});  // keep the badges that are showing; try again next round
    }

    setInterval(poll, interval);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  }

  /* ---------------- Extension detail modal ---------------- */
  function initDetailModal() {
    var modalEl = document.getElementById('extensionDetailModal');
    if (!modalEl || typeof bootstrap === 'undefined') return;
    var modal = new bootstrap.Modal(modalEl);
    var fields = ['extension', 'title', 'person', 'department', 'location', 'email', 'mobile', 'hours', 'notes', 'created', 'updated', 'phonestatus'];

    document.addEventListener('click', function (e) {
      var card = e.target.closest ? e.target.closest('.extension-card') : null;
      if (!card) return;
      if (e.target.closest('.fav-btn') || e.target.closest('.copy-btn')) return;
      fields.forEach(function (key) {
        var el = modalEl.querySelector('[data-field="' + key + '"]');
        if (el) el.textContent = card.dataset[key] || '\u2014';
      });
      var presenceEl = modalEl.querySelector('[data-field="presence"]');
      if (presenceEl) presenceEl.textContent = card.dataset.present === '1' ? '\ud83d\udfe2 \u062d\u0627\u0636\u0631' : '\ud83d\udd34 \u062e\u0627\u0631\u062c \u0627\u0632 \u0634\u06cc\u0641\u062a';
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
      after: function () { paintFavorites(); applyFavoriteFilter(); }
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
      var btn = e.target.closest ? e.target.closest('.ext-tile-del') : null;
      if (!btn) return;
      e.preventDefault();
      var form = document.getElementById('tileDeleteForm');
      if (!form || !btn.dataset.deleteUrl) return;
      if (!window.confirm('\u0627\u06cc\u0646 \u062f\u0627\u062e\u0644\u06cc \u0628\u0647 \u0633\u0637\u0644 \u0628\u0627\u0632\u06cc\u0627\u0641\u062a \u0645\u0646\u062a\u0642\u0644 \u0634\u0648\u062f\u061f')) return;
      form.action = btn.dataset.deleteUrl;
      form.submit();
    });

    var form = document.querySelector('form.admin-search-bar');
    var clearBtn = document.getElementById('adminClear');
    function updateClear() {
      if (!form || !clearBtn) return;
      var q = form.elements.q ? form.elements.q.value.trim() : '';
      var kind = form.elements.kind ? form.elements.kind.value : '';
      clearBtn.hidden = !(q || kind);
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
        if (form.elements.kind) form.elements.kind.value = '';
        updateClear();
        if (live) live.schedule(true); else window.location.href = window.location.pathname;
      });
    }
  }

  /* ---------------- Admin: bulk select ---------------- */
  function initBulkSelect() {
    var selectAll = document.getElementById('selectAllRows');
    var countEl = document.getElementById('bulkSelectedCount');
    var bulkForm = document.getElementById('bulkForm');
    if (!selectAll || !bulkForm) return;
    var actionButtons = bulkForm.querySelectorAll('button[type="submit"]');

    function rowCheckboxes() {
      return Array.prototype.slice.call(document.querySelectorAll('.row-select:not(:disabled)'));
    }

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
  var VIEW_KEY = 'hp_view_mode';
  function applyViewMode(mode) {
    var cardsView = document.getElementById('cardsView');
    var tableView = document.getElementById('tableView');
    if (!cardsView || !tableView) return;
    cardsView.style.display = mode === 'table' ? 'none' : '';
    tableView.style.display = mode === 'table' ? '' : 'none';
    document.querySelectorAll('.view-toggle-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.view === mode);
    });
  }

  function initViewToggle() {
    var buttons = document.querySelectorAll('.view-toggle-btn');
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

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initFavorites();
    initFavoriteFilterToggle();
    initCopyButtons();
    initPrintButtons();
    initUpdatePolling();
    initPhoneStatusPolling();
    initDetailModal();
    initBulkSelect();
    initViewToggle();
    initLiveSearch();
    initAdminList();
  });
})();