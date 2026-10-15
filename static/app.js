(function () {
  'use strict';

  var THEME_KEY = 'hp_theme';
  var FAV_KEY = 'hp_favorites';

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

  function initFavorites() {
    var favs = getFavorites();
    document.querySelectorAll('.fav-btn').forEach(function (btn) {
      var ext = btn.dataset.ext;
      if (favs.indexOf(ext) !== -1) {
        btn.classList.add('active');
        btn.textContent = '★';
      }
      btn.addEventListener('click', function () {
        var list = getFavorites();
        var idx = list.indexOf(ext);
        if (idx === -1) {
          list.push(ext);
          btn.classList.add('active');
          btn.textContent = '★';
        } else {
          list.splice(idx, 1);
          btn.classList.remove('active');
          btn.textContent = '☆';
        }
        saveFavorites(list);
        applyFavoriteFilter();
      });
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
    document.querySelectorAll('.copy-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
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
          try { document.execCommand('copy'); } catch (e) {}
          document.body.removeChild(temp);
          done();
        }
      });
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
    var cards = document.querySelectorAll('.extension-card');
    var rows = document.querySelectorAll('tr[data-ext]');
    if (!cards.length && !rows.length) return;
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

    document.querySelectorAll('.extension-card').forEach(function (card) {
      card.addEventListener('click', function (e) {
        if (e.target.closest('.fav-btn') || e.target.closest('.copy-btn')) return;
        fields.forEach(function (key) {
          var el = modalEl.querySelector('[data-field="' + key + '"]');
          if (el) el.textContent = card.dataset[key] || '—';
        });
        var presenceEl = modalEl.querySelector('[data-field="presence"]');
        if (presenceEl) presenceEl.textContent = card.dataset.present === '1' ? '🟢 حاضر' : '🔴 خارج از شیفت';
        modal.show();
      });
    });
  }

  /* ---------------- Admin: bulk select ---------------- */
  function initBulkSelect() {
    var selectAll = document.getElementById('selectAllRows');
    var countEl = document.getElementById('bulkSelectedCount');
    var bulkForm = document.getElementById('bulkForm');
    if (!selectAll || !bulkForm) return;
    var actionButtons = bulkForm.querySelectorAll('button[type="submit"]');

    function rowCheckboxes() {
      return Array.prototype.slice.call(document.querySelectorAll('.row-select'));
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
  });
})();
