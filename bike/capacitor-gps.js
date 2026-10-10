// bike/capacitor-gps.js
// Bridge between the web app and the native iOS GPS plugin (see
// mobile/ios/App/App/BikeLogGPSPlugin.swift). Loaded as a regular
// <script> from bike/index.html so the same code runs in the
// WKWebView and in a normal browser — when Capacitor is not
// present, the helpers fall through to navigator.geolocation.
//
// Lifecycle:
//   - startNativeGps()      → request permission, then startUpdatingLocation
//   - onNativePosition      → bridge event "gpsPosition"
//   - onNativeError         → bridge event "gpsError"
//   - stopNativeGps()       → stopUpdatingLocation
//
// When the OS is in the background (screen locked, app suspended),
// CoreLocation keeps firing didUpdateLocations on its own schedule
// and the bridge event still arrives. That is the whole point of
// running GPS natively — web Geolocation halts the moment the JS
// timer is suspended.

(function () {
  'use strict';

  var active = false;
  var permissionStatus = 'unknown';

  function getCapacitor() {
    // Capacitor exposes its bridge under window.Capacitor.Plugins
    // once the web app is running inside the native shell. When the
    // same code is opened in a normal browser the property is
    // undefined and we fall back to the web APIs.
    if (typeof window === 'undefined') return null;
    var cap = window.Capacitor;
    if (!cap || !cap.Plugins) return null;
    return cap.Plugins;
  }

  function isNative() {
    var cap = getCapacitor();
    return !!(cap && cap.BikeLogGPS);
  }

  async function requestPermission() {
    if (!isNative()) return 'web';
    try {
      var r = await window.Capacitor.Plugins.BikeLogGPS.requestPermission();
      permissionStatus = (r && r.status) || 'unknown';
      return permissionStatus;
    } catch (e) {
      console.warn('requestPermission failed:', e);
      return 'error';
    }
  }

  async function startNativeGps() {
    if (!isNative()) return false;
    if (active) return true;
    var cap = window.Capacitor.Plugins.BikeLogGPS;
    try {
      await cap.startWatching();
      active = true;
      return true;
    } catch (e) {
      console.warn('startWatching failed:', e);
      return false;
    }
  }

  async function stopNativeGps() {
    if (!isNative()) return;
    if (!active) return;
    try {
      await window.Capacitor.Plugins.BikeLogGPS.stopWatching();
    } catch (e) {
      console.warn('stopWatching failed:', e);
    }
    active = false;
  }

  async function getNativeCurrentPosition() {
    if (!isNative()) return null;
    return new Promise(function (resolve) {
      // One-shot current fix used to seed the very first polyline
      // point. The promise resolves on the next gpsPosition event
      // (with a short timeout so a stalled OS doesn't hang the
      // page), or on the first gpsError event.
      var settled = false;
      var timeout = setTimeout(function () {
        if (settled) return;
        settled = true;
        resolve(null);
      }, 4000);
      function onPos(payload) {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve(payload);
      }
      function onErr() {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve(null);
      }
      var cap = window.Capacitor.Plugins;
      cap.BikeLogGPS.addListener('gpsPosition', onPos);
      cap.BikeLogGPS.addListener('gpsError', onErr);
      cap.BikeLogGPS.getCurrentPosition().catch(function (e) {
        onErr(e);
      });
    });
  }

  function onPosition(handler) {
    if (!isNative()) return function () {};
    var cap = window.Capacitor.Plugins.BikeLogGPS;
    var handle = cap.addListener('gpsPosition', handler);
    return function () { handle && handle.remove && handle.remove(); };
  }

  function onError(handler) {
    if (!isNative()) return function () {};
    var cap = window.Capacitor.Plugins.BikeLogGPS;
    var handle = cap.addListener('gpsError', handler);
    return function () { handle && handle.remove && handle.remove(); };
  }

  function onPermissionChange(handler) {
    if (!isNative()) return function () {};
    var cap = window.Capacitor.Plugins.BikeLogGPS;
    var handle = cap.addListener('gpsPermission', handler);
    return function () { handle && handle.remove && handle.remove(); };
  }

  // Public API.
  window.BikeLogGPS = {
    isNative: isNative,
    requestPermission: requestPermission,
    start: startNativeGps,
    stop: stopNativeGps,
    getCurrentPosition: getNativeCurrentPosition,
    onPosition: onPosition,
    onError: onError,
    onPermissionChange: onPermissionChange,
    isActive: function () { return active; },
    getPermissionStatus: function () { return permissionStatus; },
  };
})();
