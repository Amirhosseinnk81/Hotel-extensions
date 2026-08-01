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

  /* ---------------- Extension detail modal ---------------- */
  function initDetailModal() {
    var modalEl = document.getElementById('extensionDetailModal');
    if (!modalEl || typeof bootstrap === 'undefined') return;
    var modal = new bootstrap.Modal(modalEl);
    var fields = ['extension', 'title', 'person', 'department', 'location', 'email', 'mobile', 'hours', 'notes', 'created', 'updated'];

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

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initFavorites();
    initFavoriteFilterToggle();
    initCopyButtons();
    initPrintButtons();
    initUpdatePolling();
    initDetailModal();
  });
})();
