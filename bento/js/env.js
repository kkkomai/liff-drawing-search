// Deployment environment settings for the bento ordering app.
//
// NOTE: this app is served from inside the existing LIFF app's <iframe>, so
// liff.init has already run in the PARENT document and cannot run again here.
// The parent hands the ID token down over postMessage (see the "bento-ready" /
// "bento-credentials" handshake in ../form.html). This file only carries the
// API base URL so the UI knows where to send orders.
//
// Secrets (channel secret / access token / dev token) must NEVER appear here.
window.__BENTO_ENV__ = {
  appEnv: 'prod',
  apiBaseUrl: '',                      // '' => same origin
  liffId: '2011376207-0e7oVWOT',       // parent LIFF app (for display only)
  devToken: ''                         // always empty in production
};