// グローバル設定（環境変数として置き換える想定）
//
// 優先順位（下位 → 上位）:
//   1. このファイルの既定値（開発用フォールバック）
//   2. window.__BENTO_ENV__ … ビルド/デプロイ時に差し込む環境設定
//   3. URL クエリ（?api=...&liffId=...）… 動作確認時の即席上書き
//   4. localStorage（開発時の毎回クエリ不要化）
//
// 本番では 2 の環境設定を必ず使うこと。クエリと localStorage はブラウザ側の
// 書き込みなので、本番で信用する設定源にしてはいけない。
(function (global) {
  'use strict';

  var params = new URLSearchParams(global.location.search);

  function pick(queryKey, fallback) {
    var v = params.get(queryKey);
    return (v && v.trim()) || fallback;
  }

  // ビルド時に差し込まれる環境設定（未定義なら空オブジェクト）
  var env = global.__BENTO_ENV__ || {};

  // LIFF ID 未設定のプレースホルダ。placeholder を実 LIFF ID のまま，配管し
  // 忘れると LIFF 内で 404 になるだけなので，明确に「未設定」扱いで落とす。
  var PLACEHOLDER_LIFF_ID = 'REPLACE_WITH_LIFF_ID';

  var config = {
    // 実行環境名（dev / staging / prod）
    appEnv: env.appEnv || 'dev',

    // バックエンドAPIのベースURL（末尾スラッシュなし）
    // 既定値は「環境設定 → 同一オリジン」の順。同一オリジン運用なら
    // BENTO_PUBLIC_API_BASE_URL を空にして LIFF ページと API を同じオリジンに
    // 置くと、CORS に依存しない最も単純な構成になる。
    apiBaseUrl: pick('api', env.apiBaseUrl || global.location.origin),

    // LINE Developers コンソールで払い出し
    liffId: pick('liffId', env.liffId || PLACEHOLDER_LIFF_ID),

    // ローカル開発時のバックエンド dev escape hatch（BENTO_DEV_AUTH_TOKEN と一致させる）
    devToken: pick('devToken', env.devToken || ''),

    // 開発用: LIFF外ブラウザで動作確認する場合は ?mockLineUserId=Uxxxx を付与
    mockLineUserId: pick('mockLineUserId', ''),

    // ローカルログ Level（liff.setLogLevel）
    logLevel: pick('log', 'warn'),

    // 画面表示の既定範囲（過去日数 / 未来日数）
    defaultPastDays: parseInt(pick('pastDays', '60'), 10),
    defaultFutureDays: parseInt(pick('futureDays', '30'), 10),

    // 管理者の社員コード（モード切替タブの初期表示判定のフォールバック用）
    adminHint: pick('admin', ''),

    // Set to true when embedded as an iframe by the application-form tab.
    // liff.init is never called in that mode; credentials arrive by postMessage.
    embedded: pick('embedded', '') === '1'
  };

  // ローカルStorageで上書き（開発時に毎回クエリ changing なくて済むように）
  try {
    var saved = global.localStorage.getItem('bento_liff_config');
    if (saved) {
      var parsed = JSON.parse(saved);
      Object.keys(parsed).forEach(function (k) {
        if (parsed[k] !== '' && parsed[k] !== null && parsed[k] !== undefined) {
          config[k] = parsed[k];
        }
      });
    }
  } catch (e) {
    /* localStorage 不可の環境では無視 */
  }

  config.isMockMode = !!config.mockLineUserId;
  config.isLiffConfigured = config.liffId !== PLACEHOLDER_LIFF_ID && config.liffId !== '';
  // 申請フォームが返す LIFF アプリ URL と同じ形式。
  config.liffAppUrl = config.isLiffConfigured
    ? 'https://liff.line.me/' + config.liffId
    : null;

  // バックエンドが持つデプロイ設定（LIFF ID / API URL / 環境名）を取得する。
  // 申請フォームと同じ情報源（/api/config）を見にいくことで，
  // ページとフォームが別々の URL を持ってずれるのを構造的に防ぐ。
  config.fetchServerConfig = function () {
    if (typeof fetch !== 'function') return Promise.reject(new Error('no_fetch'));
    return fetch(config.apiBaseUrl.replace(/\/+$/, '') + '/api/config', {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      mode: 'cors',
      cache: 'no-store'
    }).then(function (res) {
      if (!res.ok) throw new Error('config_http_' + res.status);
      return res.json();
    });
  };

  global.APP_CONFIG = config;
})(window);