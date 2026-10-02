// メインアプリ制御
(function (global) {
  'use strict';

  var cfg = global.APP_CONFIG;
  var D = global.DateUtil;
  var API = global.API;

  var STATE = {
    employee: null,
    isAdmin: false,
    today: D.todayJST(),
    orders: {},        // { 'YYYY-MM-DD': 'needed'|'not_needed' }
    range: 'week',     // today | week | all
    rangeOffset: 0,    // 週/全期間のページング
    empFilter: 'all',
    view: 'employee',
    adminDate: D.todayJST(),
    adminFilter: 'all',
    adminData: null
  };

  // ---------------------------------------------------------------- DOM
  var $ = function (id) { return document.getElementById(id); };
  var el = {
    splash: $('splash'), splashTitle: $('splash-title'), splashMsg: $('splash-msg'),
    splashDetail: $('splash-detail'), splashRetry: $('splash-retry'), splashSpinner: $('splash-spinner'),
    app: $('app'), tabs: $('bento-tabs'),
    userName: $('user-name'), userToday: $('user-today'),
    viewEmployee: $('view-employee'), viewAdmin: $('view-admin'),
    rangeSeg: $('range-seg'), rangeLabel: $('range-label'),
    btnPrev: $('btn-prev'), btnNext: $('btn-next'),
    dayList: $('day-list'), empEmpty: $('emp-empty'), empFilters: $('emp-filters'),
    listTitle: $('list-title'),
    adminPrev: $('admin-prev'), adminNext: $('admin-next'), adminDateLabel: $('admin-date-label'),
    adminDatePicker: $('admin-date-picker'), adminTodayBtn: $('admin-today-btn'),
    adminSummary: $('admin-summary'), adminTableBody: $('admin-table-body'),
    adminCards: $('admin-cards'), adminFilters: $('admin-filters'),
    toast: $('toast'),
    modalBackdrop: $('modal-backdrop'), modalTitle: $('modal-title'),
    modalMsg: $('modal-msg'), modalOk: $('modal-ok'), modalCancel: $('modal-cancel')
  };

  // ---------------------------------------------------------------- UI ヘルパ
  var toastTimer = null;
  function toast(msg, isError) {
    el.toast.textContent = msg;
    el.toast.className = 'toast show' + (isError ? ' error' : '');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.toast.className = 'toast';
    }, 2600);
  }

  var modalResolve = null;
  function confirmDialog(title, msg, okLabel, danger) {
    return new Promise(function (resolve) {
      el.modalTitle.textContent = title;
      el.modalMsg.textContent = msg;
      el.modalOk.textContent = okLabel || 'OK';
      el.modalOk.className = danger ? 'danger' : 'primary';
      el.modalBackdrop.hidden = false;
      modalResolve = resolve;
    });
  }
  function closeModal(result) {
    el.modalBackdrop.hidden = true;
    if (modalResolve) {
      var r = modalResolve;
      modalResolve = null;
      r(result);
    }
  }
  el.modalOk.addEventListener('click', function () { closeModal(true); });
  el.modalCancel.addEventListener('click', function () { closeModal(false); });
  el.modalBackdrop.addEventListener('click', function (e) {
    if (e.target === el.modalBackdrop) closeModal(false);
  });

  function showSplash(title, msg, detail, showRetry) {
    el.splash.classList.remove('hidden');
    el.app.classList.add('hidden');
    el.splashSpinner.classList.remove('hidden');
    el.splashTitle.textContent = title;
    el.splashMsg.textContent = msg || '';
    el.splashDetail.textContent = detail || '';
    el.splashDetail.classList.toggle('hidden', !detail);
    el.splashRetry.classList.toggle('hidden', !showRetry);
  }

  function showApp() {
    el.splash.classList.add('hidden');
    el.app.classList.remove('hidden');
  }

  // ---------------------------------------------------------------- 認証
  // Resolves once the host page posts credentials. The host may have booted
  // before this listener attached, so the ready message is replayed on request.
  function waitForHostCredentials(timeoutMs) {
    return new Promise(function (resolve, reject) {
      var settled = false;
      function accept(creds) {
        if (settled) return;
        // lineUserId is required downstream (STATE.employee lookup), but silently
        // dropping the payload here produced a bare 15s stall with no diagnostic.
        // Accept whatever the host sent and let the login call report the gap.
        if (!creds || (!creds.lineUserId && !creds.idToken)) return;
        settled = true;
        global.removeEventListener('message', onMessage);
        resolve(creds);
      }
      function onMessage(ev) {
        // Only accept from a parent frame; never from arbitrary origins.
        if (!global.parent || global.parent === global) return;
        if (ev.origin !== global.location.origin && ev.origin !== 'null') return;
        var d = ev.data;
        if (!d || d.type !== 'bento-credentials') return;
        accept(d);
      }
      global.addEventListener('message', onMessage);
      global.parent.postMessage({ type: 'bento-ready' }, global.location.origin);
      setTimeout(function () {
        if (settled) return;
        settled = true;
        global.removeEventListener('message', onMessage);
        reject(new Error('ホストページから LINE 認証情報を受け取れませんでした。タブを開き直してください。'));
      }, timeoutMs || 15000);
    });
  }

  function authenticate() {
    if (cfg.isMockMode) {
      global.__LINE_USER_ID__ = cfg.mockLineUserId;
      // Serve the employee from local fixtures. This path used to call
      // API.login(), which 404s whenever the FastAPI service is not deployed —
      // that turned a UI review into an error screen. Real runs never take it.
      var fixture = (global.__BENTO_MOCK__ || {}).employee;
      if (!fixture) {
        return Promise.reject(new Error('モックデータ（js/mock.js）が読み込まれていません。'));
      }
      global.__SESSION_TOKEN__ = 'mock-token';
      return Promise.resolve(fixture);
    }

    // Embedded mode: the 申請フォーム tab embeds this app in an iframe and posts
    // the ID token over. liff.init can only run once per document and an iframe
    // has no LIFF context of its own, so we never call it here.
    if (cfg.embedded) {
      return waitForHostCredentials().then(function (creds) {
        global.__LINE_USER_ID__ = creds.lineUserId;
        global.__ID_TOKEN__ = creds.idToken || null;
        return API.login(creds.lineUserId, creds.idToken || null);
      }).then(unwrapEmployee);
    }

    if (!cfg.isLiffConfigured) {
      return Promise.reject(new Error(
        'LIFF ID が設定されていません。js/config.js の liffId を設定するか、?liffId=... を付与してください。'
      ));
    }
    if (typeof global.liff === 'undefined') {
      return Promise.reject(new Error('LIFF SDK の読み込みに失敗しました。通信状況をご確認ください。'));
    }

    return global.liff.init({ liffId: cfg.liffId })
      .then(function () {
        return global.liff.isLoggedIn() ? null : global.liff.login();
      })
      .then(function () { return collectLiffIdentity(); })
      .then(unwrapEmployee);
  }

  // liff オブジェクトから userId と生の ID トークンを取り出してバックエンドに送る
  function collectLiffIdentity() {
    var decoded = null;
    try { decoded = global.liff.getDecodedIDToken(); } catch (e) { decoded = null; }
    var userId = null;
    try { userId = global.liff.getContext().userId; } catch (e) { userId = null; }
    if (!userId) throw new Error('LINE ユーザーIDを取得できませんでした。もう一度お試しください。');
    global.__LINE_USER_ID__ = userId;
    // バックエンド側で ID トークン署名検証できるよう、生の JWT も送る
    if (decoded && decoded.jwt) global.__ID_TOKEN__ = decoded.jwt;
    return API.login(userId, global.__ID_TOKEN__ || null);
  }

  function unwrapEmployee(res) {
    if (!res || res.success !== true || !res.employee) {
      var e = new Error((res && res.message) || '認証に失敗しました。');
      if (res && res.error === 'unauthorized_user') e.blocked = true;
      throw e;
    }
    // バックエンドが返すセッショントークンを以降のリクエストで Bearer 認証に使う
    if (res.token) {
      global.__SESSION_TOKEN__ = res.token;
    }
    return res.employee;
  }

  // セッショントークン切れ（401）の再取得
  function withSession(fn) {
    return function () {
      var args = arguments;
      return fn.apply(null, args).catch(function (err) {
        if (err && err.status === 401 && !global.__RETRIED__) {
          global.__RETRIED__ = true;
          return authenticate().then(function (emp) {
            STATE.employee = emp;
            STATE.isAdmin = emp.role === 'admin';
            return fn.apply(null, args);
          }).finally(function () { global.__RETRIED__ = false; });
        }
        throw err;
      });
    };
  }

  // ---------------------------------------------------------------- ヘッダー
  function renderHeader() {
    el.userName.textContent = STATE.employee.name + ' (' + STATE.employee.employee_code + ')';
    var d = STATE.today;
    el.userToday.textContent = '本日 ' + d + ' (' + D.weekdayLabel(d) + ') / JST';
  }

  // ---------------------------------------------------------------- ビュー切替
  function switchView(view) {
    STATE.view = view;
    Array.prototype.forEach.call(el.tabs.children, function (b) {
      b.classList.toggle('active', b.dataset.view === view);
    });
    el.viewEmployee.classList.toggle('hidden', view !== 'employee');
    el.viewAdmin.classList.toggle('hidden', view !== 'admin');
    if (view === 'admin') {
      if (!STATE.adminData) loadAdminOrders();
    }
  }

  // ---------------------------------------------------------------- 従業員: データ取得
  function currentRangeDates() {
    var t = STATE.today;
    if (STATE.range === 'today') {
      return [t];
    }
    if (STATE.range === 'week') {
      var wd = D.weekday(t);           // 0=日
      var monday = D.addDays(t, -((wd + 6) % 7));
      var start = D.addDays(monday, STATE.rangeOffset * 7);
      return D.range(start, 0, 6);
    }
    // all: 「全期間」= 過去 defaultPastDays 日 〜 未来 defaultFutureDays 日 を 3ヶ月ページで切替
    var pageSize = 90;
    var block = Math.floor(STATE.rangeOffset);
    var start, end;
    if (block === 0) {
      start = D.addDays(t, -cfg.defaultPastDays);
      end = D.addDays(t, cfg.defaultFutureDays);
    } else if (block > 0) {
      // 未来方向: [today+futureDays+1, ...] を pageSize ずつ
      start = D.addDays(D.addDays(t, cfg.defaultFutureDays), (block - 1) * pageSize + 1);
      end = D.addDays(start, pageSize - 1);
    } else {
      // 過去方向: today-defaultPastDays より前を pageSize ずつ
      end = D.addDays(D.addDays(t, -cfg.defaultPastDays), block * pageSize);
      start = D.addDays(end, -(pageSize - 1));
    }
    return D.range(start, 0, Math.round((Date.parse(end) - Date.parse(start)) / 86400000));
  }

  function rangeLabelText(dates) {
    return D.formatLabel(dates[0]) + ' 〜 ' + D.formatLabel(dates[dates.length - 1]);
  }

  function loadMyOrders() {
    var dates = currentRangeDates();
    if (cfg.isMockMode) {
      // Fixtures stand in for the API so the UI is reviewable without it.
      var mock = global.__BENTO_MOCK__ || {};
      STATE.orders = {};
      (mock.orders || []).forEach(function (o) {
        if (o && o.date) STATE.orders[o.date] = 'ordered';
      });
      return Promise.resolve();
    }
    return withSession(API.getMyOrders)(dates[0], dates[dates.length - 1])
      .then(function (res) {
        STATE.orders = {};
        (res.orders || []).forEach(function (o) {
          if (o && o.date) STATE.orders[o.date] = o.status;
        });
      });
  }

  function reloadOrders() {
    return loadMyOrders().then(renderEmployee).catch(handleApiError);
  }

  // ---------------------------------------------------------------- 従業員: 描画
  function renderEmployee() {
    var dates = currentRangeDates();
    el.rangeLabel.textContent = rangeLabelText(dates);
    el.listTitle.textContent = STATE.range === 'all'
      ? '日付ごとの注文（過去' + cfg.defaultPastDays + '日分も閲覧できます）'
      : '日付ごとの注文';

    var visible = dates.filter(function (d) {
      var st = STATE.orders[d];
      if (STATE.empFilter === 'needed') return st === 'needed';
      if (STATE.empFilter === 'unregistered') return !st;
      return true;
    });

    el.dayList.innerHTML = '';
    el.empEmpty.style.display = visible.length ? 'none' : 'block';

    visible.forEach(function (date) {
      el.dayList.appendChild(renderDay(date));
    });
  }

  function renderDay(date) {
    var status = STATE.orders[date] || null;
    var past = D.isBefore(date, STATE.today);
    var li = document.createElement('li');
    li.className = 'day' +
      (date === STATE.today ? ' is-today' : '') +
      (past ? ' is-past' : '') +
      (D.isWeekend(date) ? ' is-weekend' : '');

    var label = document.createElement('div');
    label.className = 'day-label';
    var dEl = document.createElement('span');
    dEl.className = 'date';
    dEl.textContent = D.formatShort(date);
    var sEl = document.createElement('span');
    sEl.className = 'sub';
    if (date === STATE.today) {
      sEl.textContent = '本日';
    } else if (past) {
      sEl.textContent = '閲覧のみ';
    } else {
      sEl.textContent = '+' + dayDiff(date) + '日';
    }
    label.appendChild(dEl);
    label.appendChild(sEl);

    var actions = document.createElement('div');
    actions.className = 'day-actions';

    if (past) {
      // 過去日は閲覧のみ（サーバー側でも 400 で拒否される）
      var ro = document.createElement('div');
      ro.className = 'day-readonly';
      ro.textContent = statusLabel(status) + ' / 変更不可';
      actions.appendChild(ro);
    } else {
      actions.appendChild(choiceButton(date, 'needed', '必要', status === 'needed'));
      actions.appendChild(choiceButton(date, 'not_needed', '不要', status === 'not_needed'));
    }

    li.appendChild(label);
    li.appendChild(actions);
    return li;
  }

  function dayDiff(date) {
    var a = D.parse(STATE.today);
    var b = D.parse(date);
    var da = Date.UTC(a.y, a.m - 1, a.d);
    var db = Date.UTC(b.y, b.m - 1, b.d);
    return Math.round((db - da) / 86400000);
  }

  function choiceButton(date, value, text, selected) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'choice ' + value.replace('_', '-') + (selected ? ' selected' : '');
    b.textContent = text;
    b.setAttribute('aria-pressed', selected ? 'true' : 'false');
    b.dataset.date = date;
    b.dataset.value = value;
    b.addEventListener('click', function () {
      onChoiceClick(date, value, b);
    });
    return b;
  }

  function statusLabel(status) {
    if (status === 'needed') return '必要';
    if (status === 'not_needed') return '不要';
    return '未登録';
  }

  // 二重送信防止: 押下と同時に disabled にして同一リクエストの多重送信を防ぐ
  function onChoiceClick(date, value, btn) {
    if (btn.disabled) return;
    var current = STATE.orders[date] || null;

    confirmDialog(
      '注文の確認',
      D.formatLabel(date) + ' のお弁当を「' + (value === 'needed' ? '必要' : '不要') + '」で登録しますか？',
      '登録する'
    ).then(function (ok) {
      if (!ok) return;
      var buttons = el.dayList.querySelectorAll('button[data-date="' + date + '"]');
      Array.prototype.forEach.call(buttons, function (b) {
        b.disabled = true;
        b.classList.add('saving');
      });

      return withSession(API.saveOrder)(date, value)
        .then(function (res) {
          if (!res || res.success !== true) {
            throw new API.ApiError((res && res.message) || '保存に失敗しました。', 0, 'save_failed');
          }
          STATE.orders[date] = value;
          toast('保存しました');
          renderEmployee();
        })
        .catch(function (err) {
          handleApiError(err);
          // 通信エラー時は入力内容（選択状態）を保持したままUIだけ復元する
          renderEmployee();
        })
        .then(function () {
          Array.prototype.forEach.call(buttons, function (b) {
            b.disabled = false;
            b.classList.remove('saving');
          });
        });
    });
  }

  function handleApiError(err) {
    if (err && err.code === 'past_date_modification_not_allowed') {
      toast('過去の日付の注文は変更できません。', true);
      return;
    }
    var msg = err && err.message ? err.message : '通信エラーが発生しました。再度お試しください。';
    toast(msg, true);
  }

  // ---------------------------------------------------------------- 従業員: イベント
  el.rangeSeg.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-range]');
    if (!b) return;
    STATE.range = b.dataset.range;
    STATE.rangeOffset = 0;
    Array.prototype.forEach.call(el.rangeSeg.children, function (c) {
      c.classList.toggle('active', c === b);
    });
    reloadOrders();
  });

  el.btnPrev.addEventListener('click', function () {
    if (STATE.range === 'today') return;
    STATE.rangeOffset -= 1;
    reloadOrders();
  });
  el.btnNext.addEventListener('click', function () {
    if (STATE.range === 'today') return;
    if (STATE.range === 'week') {
      var last = D.addDays(D.addDays(STATE.today, -((D.weekday(STATE.today) + 6) % 7)), STATE.rangeOffset * 7 + 6);
      if (D.compare(last, D.addDays(STATE.today, cfg.defaultFutureDays)) > 0) {
        toast('表示できる将来日は ' + cfg.defaultFutureDays + '日先までです。', true);
        return;
      }
    } else if (STATE.rangeOffset >= 4) {
      // 事前登録の上限は設けないが、誤操作で遠くまで飛べないように1年分はページに留める
      toast('これ以上先のページはありません。', true);
      return;
    }
    STATE.rangeOffset += 1;
    reloadOrders();
  });

  el.empFilters.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-filter]');
    if (!b) return;
    STATE.empFilter = b.dataset.filter;
    Array.prototype.forEach.call(el.empFilters.children, function (c) {
      c.classList.toggle('active', c === b);
    });
    renderEmployee();
  });

  el.tabs.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-view]');
    if (!b) return;
    switchView(b.dataset.view);
  });

  // ---------------------------------------------------------------- 管理者
  function loadAdminOrders() {
    el.adminSummary.innerHTML = '<p class="sub" style="color:var(--muted);font-size:13px;margin:0">読み込み中…</p>';
    return withSession(API.getAdminOrders)(STATE.adminDate, STATE.adminDate)
      .then(function (res) {
        // バックエンドは単日(date=)なら {date, summary, employees_status} を返す
        STATE.adminData = res.days ? res.days[0] : res;
        renderAdmin();
      })
      .catch(function (err) {
        el.adminSummary.innerHTML = '';
        if (err && (err.status === 403)) {
          toast((err.code === 'admin_required' || err.code === 'forbidden')
            ? '管理者権限がありません。'
            : 'この操作は許可されていません。', true);
          switchView('employee');
          return;
        }
        handleApiError(err);
      });
  }

  function renderAdmin() {
    var res = STATE.adminData || {};
    var sum = res.summary || {};
    el.adminDateLabel.textContent = res.date || D.formatLabel(STATE.adminDate);
    el.adminDatePicker.value = STATE.adminDate;

    el.adminSummary.innerHTML = '';
    [
      { label: '必要', num: sum.needed_count || 0, cls: 'needed' },
      { label: '不要', num: sum.not_needed_count || 0, cls: 'not-needed' },
      { label: '未登録', num: sum.unregistered_count || 0, cls: 'unregistered' }
    ].forEach(function (item) {
      var div = document.createElement('div');
      div.className = 'item';
      div.innerHTML = '<div class="num ' + item.cls + '">' + item.num + '</div>' +
        '<div class="label">' + item.label + '</div>';
      el.adminSummary.appendChild(div);
    });

    var list = res.employees_status || [];
    var filtered = list.filter(function (e) {
      if (STATE.adminFilter === 'all') return true;
      return (e.status || 'unregistered') === STATE.adminFilter;
    });

    el.adminTableBody.innerHTML = '';
    el.adminCards.innerHTML = '';
    filtered.forEach(function (e) {
      var st = e.status || 'unregistered';
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="code">' + escapeHtml(e.employee_code || '') + '</td>' +
        '<td>' + escapeHtml(e.name || '') + '</td>' +
        '<td><span class="badge ' + st + '">' + statusLabel(st) + '</span></td>' +
        '<td>' + (e.updated_at ? escapeHtml(String(e.updated_at).slice(0, 16).replace('T', ' ')) : '—') + '</td>';
      el.adminTableBody.appendChild(tr);

      var card = document.createElement('div');
      card.className = 'emp-card';
      card.innerHTML = '<div><div class="nm">' + escapeHtml(e.name || '') + '</div>' +
        '<div class="cd">' + escapeHtml(e.employee_code || '') + '</div></div>' +
        '<span class="badge ' + st + '">' + statusLabel(st) + '</span>';
      el.adminCards.appendChild(card);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function changeAdminDate(next) {
    if (D.compare(next, D.addDays(STATE.today, cfg.defaultFutureDays)) > 0) {
      toast('表示できる将来日は ' + cfg.defaultFutureDays + '日先までです。', true);
      return;
    }
    STATE.adminDate = next;
    loadAdminOrders();
  }

  el.adminPrev.addEventListener('click', function () {
    changeAdminDate(D.addDays(STATE.adminDate, -1));
  });
  el.adminNext.addEventListener('click', function () {
    changeAdminDate(D.addDays(STATE.adminDate, 1));
  });
  el.adminTodayBtn.addEventListener('click', function () {
    changeAdminDate(STATE.today);
  });
  el.adminDatePicker.addEventListener('change', function () {
    if (this.value) changeAdminDate(this.value);
  });
  el.adminFilters.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-filter]');
    if (!b) return;
    STATE.adminFilter = b.dataset.filter;
    Array.prototype.forEach.call(el.adminFilters.children, function (c) {
      c.classList.toggle('active', c === b);
    });
    renderAdmin();
  });

  // ---------------------------------------------------------------- 起動
  //
  // 単独 LIFF として開く場合は自動で起動する。申請フォームにタブとして埋め込まれた
  // 場合はホストが DOM を用意してから BentoApp.start() を呼ぶ（多重起動は必ず弾く）。
  var booted = false;
  function start() {
    if (booted) return Promise.resolve();
    booted = true;
    el.splashRetry.addEventListener('click', function () { global.location.reload(); });
    showSplash('読み込み中…', 'お弁当注文画面を準備しています。');
    return authenticate()
      .then(function (employee) {
        STATE.employee = employee;
        STATE.isAdmin = employee.role === 'admin';
        showApp();
        renderHeader();
        if (STATE.isAdmin) {
          el.tabs.classList.remove('hidden');
        }
        switchView('employee');
        return loadMyOrders();
      })
      .then(function () {
        renderEmployee();
      })
      .catch(function (err) {
        // 403 unauthorized_user は request() 側で ApiError として throw されるので、
        // ブロック画面分岐はここで判定する
        var isBlocked = (err && err.blocked) || (err && err.code === 'unauthorized_user');
        if (isBlocked) {
          showSplash(
            'ご利用いただけません',
            (err && err.message) || 'このLINEアカウントは社員マスタに登録されていません。',
            'このLINEアカウントは社員マスタに登録されていません。\n管理者にお問い合わせください。',
            false
          );
          el.splashTitle.classList.add('block-msg');
        } else {
          var msg = err && err.message ? err.message : '不明なエラーが発生しました。';
          showSplash('読み込みに失敗しました', msg, '', true);
        }
      });
  }

  global.BentoApp = {
    start: start,
    // host-driven re-entry point
    reload: function () { booted = false; return start(); }
  };

  if (!cfg.embedded) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { start(); });
    } else {
      start();
    }
  } else {
    // Embedded in the application form: boot as soon as the host hands over
    // credentials. The host may already be listening, so announce readiness
    // both now and on DOMContentLoaded to cover either boot order.
    //
    // The watchdog below matters: if the host never delivers credentials
    // (LIFF init failed, SDK blocked), start() would never be called and the
    // splash would stay forever with no diagnostic. Surface the failure instead.
    var WATCHDOG_MS = 45000;
    function announceReady() { global.parent.postMessage({ type: 'bento-ready' }, global.location.origin); }
    function showHostAuthFailure(message) {
      showSplash('LINE認証が必要です', message || 'ホストページから認証情報を受け取れませんでした。',
                 '申請フォーム（LINE内）から開き直してください。', true);
    }
    global.addEventListener('message', function (ev) {
      var d = ev.data;
      if (!d) return;
      if (d.type === 'bento-credentials') {
        clearTimeout(hostWatchdog);
        announceReady = function () {};
        if (global.__hostAuthFailed) {
          global.__hostAuthFailed = false;
          showSplash('お弁当注文画面を準備しています..', '認証情報を確認しました。', '', false);
        }
        start();
        return;
      }
      if (d.type === 'bento-auth-error') {
        clearTimeout(hostWatchdog);
        showHostAuthFailure(d.message);
        // Stay recoverable. The host gives up after its own retry budget, but
        // liff.init() can still resolve afterwards (cold SDK load, or a Scope
        // change saved in the console a moment ago). When the real credentials
        // land, clear the failure and boot for real instead of leaving the user
        // stuck on an error that is no longer true.
        global.__hostAuthFailed = true;
      }
    });
    var hostWatchdog = setTimeout(function () {
      showHostAuthFailure('ホストページから LINE 認証情報を受信できませんでした（45秒タイムアウト）。');
    }, WATCHDOG_MS);
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', announceReady);
    } else {
      announceReady();
    }
  }
})(window);