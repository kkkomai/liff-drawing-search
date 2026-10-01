// API client: follows the contracts in chapter 4 of the spec.
(function (global) {
  'use strict';

  var cfg = global.APP_CONFIG;

  function ApiError(message, status, code, payload) {
    this.name = 'ApiError';
    this.message = message || '通信エラーが発生しました。再度お試しください。';
    this.status = status || 0;
    this.code = code || '';
    this.payload = payload || null;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function url(path, params) {
    var qs = [];
    Object.keys(params || {}).forEach(function (k) {
      if (params[k] === undefined || params[k] === null || params[k] === '') return;
      qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
    });
    var base = cfg.apiBaseUrl.replace(/\/+$/, '');
    return base + path + (qs.length ? '?' + qs.join('&') : '');
  }

  function headers(extra) {
    var h = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
    // バックエンドが発行したセッショントークンを優先（Bearer 認証）
    if (global.__SESSION_TOKEN__) {
      h['Authorization'] = 'Bearer ' + global.__SESSION_TOKEN__;
    } else if (global.__ID_TOKEN__) {
      h['Authorization'] = 'Bearer ' + global.__ID_TOKEN__;
    }
    // LIFF 開発時のフォールバック / モックAPI用
    if (global.__LINE_USER_ID__) {
      h['X-Line-User-Id'] = global.__LINE_USER_ID__;
    }
    Object.keys(extra || {}).forEach(function (k) {
      h[k] = extra[k];
    });
    return h;
  }

  function handle(res) {
    return res.text().then(function (text) {
      var json = null;
      if (text) {
        try {
          json = JSON.parse(text);
        } catch (e) {
          json = null;
        }
      }
      if (!res.ok) {
        throw new ApiError(
          (json && json.message) || ('サーバーエラーが発生しました（HTTP ' + res.status + '）。'),
          res.status,
          json && json.error,
          json
        );
      }
      return json;
    });
  }

  function request(method, path, { params, body } = {}) {
    if (typeof fetch !== 'function') {
      return Promise.reject(new ApiError('このブラウザでは fetch を利用できません。', 0, 'no_fetch'));
    }
    var opts = {
      method: method,
      headers: headers(),
      mode: 'cors',
      cache: 'no-store'
    };
    if (body !== undefined && body !== null) {
      opts.body = JSON.stringify(body);
    }
    return fetch(url(path, params), opts).then(handle, function (netErr) {
      throw new ApiError(
        '通信エラーが発生しました。再度お試しください。（' + (netErr && netErr.message ? netErr.message : 'network error') + '）',
        0,
        'network_error',
        null
      );
    });
  }

  var API = {
    ApiError: ApiError,

    // 4.1 認証
    login: function (lineUserId, idToken) {
      var body = { line_user_id: lineUserId };
      if (idToken) {
        body.id_token = idToken;
      } else if (cfg.devToken) {
        // ローカル開発用のバックエンド dev escape hatch
        body.dev_token = cfg.devToken;
      }
      return request('POST', '/api/auth/login', { body: body });
    },

    // 4.2 自分の注文一覧
    getMyOrders: function (startDate, endDate) {
      return request('GET', '/api/orders', {
        params: { start_date: startDate, end_date: endDate }
      });
    },

    // 4.3 Upsert（PUT を使い、POST が必要な環境向けに残す）
    saveOrder: function (date, status) {
      return request('PUT', '/api/orders', { body: { date: date, status: status } });
    },

    // 4.4 管理者一覧（単日は date=、期間は start_date/end_date）
    getAdminOrders: function (startDate, endDate) {
      if (startDate === endDate) {
        return request('GET', '/api/admin/orders', { params: { date: startDate } });
      }
      return request('GET', '/api/admin/orders', {
        params: { start_date: startDate, end_date: endDate }
      });
    }
  };

  global.API = API;
})(window);