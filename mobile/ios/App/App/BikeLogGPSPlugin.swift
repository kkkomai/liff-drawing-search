// BikeLogGPSPlugin.swift
// Capacitor plugin that exposes native iOS CoreLocation to the WebView
// running the liff-drawing-search bike log. The plugin's lifecycle is
// driven entirely by the JS bridge in bike/index.html — startWatching
// from JS turns on the OS location manager with background updates
// enabled; stopWatching turns it back off.
//
// The native side keeps a single CLLocationManager and pipes every
// update back to JS via a Capawesome-style event listener. The JS
// side aggregates those events into the same /bike-logs POST flow
// the existing web-only implementation uses, so the rest of the
// app does not need to know whether GPS is coming from the browser
// or from this plugin.
//
// Build integration: this file is compiled by the iOS app target
// (BikeLog) after `npx cap sync` copies the Capacitor project in. The
// plugin must be registered in BikeLog's @main AppDelegate before
// the WebView is created, otherwise the first JS call to start
// watching would arrive before the bridge is wired up.

import Foundation
import CoreLocation
import Capacitor

@objc(BikeLogGPSPlugin)
class BikeLogGPSPlugin: CAPPlugin, CLLocationManagerDelegate {
    // The OS location manager. One instance per app lifetime — the
    // delegate is bound to a single manager, and re-creating it on
    // every start/stop cycle would force the user to re-grant
    // permission on iOS.
    private let manager = CLLocationManager()

    // The most recent position we have delivered to JS. Used to
    // suppress duplicate fires when the device has not moved more
    // than ``retainThresholdMeters`` since the last update.
    private var lastDeliveredLocation: CLLocation?
    private let retainThresholdMeters: CLLocationDistance = 5

    // Track whether we are actively listening. stopWatching is a
    // no-op when this is false, so an extra stop call from JS (e.g.
    // a tab switch) does not accidentally disable future restarts.
    private var isWatching: Bool = false

    // Pending permission requests keyed by JS call id. We resolve
    // them when the user accepts or denies the OS prompt.
    private var pendingPermissionCallId: String?
    private var pendingPositionCallId: String?

    override init() {
        super.init()
        manager.delegate = self
        // Best accuracy for cycling, with continuous updates. The
        // system will throttle this when the device is stationary,
        // so the battery hit is modest once the rider actually moves.
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.activityType = .fitness
        manager.pausesLocationUpdatesAutomatically = false
        manager.distanceFilter = kCLDistanceFilterNone
        manager.allowsBackgroundLocationUpdates = true
        manager.showsBackgroundLocationIndicator = true
    }

    // MARK: - JS-facing methods

    /// Request "always" authorisation so the OS allows background
    /// updates after the user accepts the prompt. Called by JS
    /// right before startWatching.
    @objc func requestPermission(_ call: CAPPluginCall) {
        let status: CLAuthorizationStatus
        if #available(iOS 14.0, *) {
            status = manager.authorizationStatus
        } else {
            status = CLLocationManager.authorizationStatus()
        }
        // iOS cannot ask for "always" directly from "not determined"
        // — the user has to first accept "when in use", and the OS
        // will surface an upgrade prompt the next time the app
        // comes to the foreground.
        if status == .notDetermined {
            manager.requestWhenInUseAuthorization()
        } else if status == .authorizedWhenInUse {
            manager.requestAlwaysAuthorization()
        }
        call.resolve([
            "status": String(statusString(status))
        ])
    }

    /// Begin receiving location updates. The OS may invoke the
    /// delegate's didChangeAuthorization callback as a side effect
    /// of upgrading from when-in-use to always, so we keep that
    /// state machine in sync.
    @objc func startWatching(_ call: CAPPluginCall) {
        let status: CLAuthorizationStatus
        if #available(iOS 14.0, *) {
            status = manager.authorizationStatus
        } else {
            status = CLLocationManager.authorizationStatus()
        }
        if status == .denied || status == .restricted {
            call.reject("location permission denied")
            return
        }
        if status == .authorizedWhenInUse {
            // Upgrade to "always" so background updates keep flowing
            // once the screen locks. The OS surfaces the upgrade
            // prompt the next time the app is foregrounded, so we
            // also start updates now — iOS will keep delivering
            // them as long as the foreground session is active.
            manager.requestAlwaysAuthorization()
        }
        isWatching = true
        manager.startUpdatingLocation()
        call.resolve([
            "status": String(statusString(status))
        ])
    }

    @objc func stopWatching(_ call: CAPPluginCall) {
        if isWatching {
            manager.stopUpdatingLocation()
            isWatching = false
            lastDeliveredLocation = nil
        }
        call.resolve()
    }

    /// One-shot current position. Used by JS to seed the very first
    /// sample right after startWatching, so the polyline is not
    /// empty for the first 3 minutes.
    @objc func getCurrentPosition(_ call: CAPPluginCall) {
        let status: CLAuthorizationStatus
        if #available(iOS 14.0, *) {
            status = manager.authorizationStatus
        } else {
            status = CLLocationManager.authorizationStatus()
        }
        if status == .denied || status == .restricted {
            call.reject("location permission denied")
            return
        }
        pendingPositionCallId = call.callbackId
        manager.requestLocation()
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard isWatching, let loc = locations.last else { return }
        if let prev = lastDeliveredLocation, loc.distance(from: prev) < retainThresholdMeters {
            // Device has not moved enough to bother JS with. iOS
            // already filters at the activity-type level but a
            // second guard here keeps the JS bridge quiet during
            // stops at traffic lights.
            return
        }
        lastDeliveredLocation = loc
        let payload: [String: Any] = [
            "latitude": loc.coordinate.latitude,
            "longitude": loc.coordinate.longitude,
            "accuracy": loc.horizontalAccuracy,
            "timestamp": loc.timestamp.timeIntervalSince1970 * 1000,
            "speed": loc.speed,
            "course": loc.course,
            "altitude": loc.altitude,
        ]
        notifyListeners("gpsPosition", data: payload)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        let code = (error as NSError).code
        // CLError.denied (-1) means the user revoked permission. We
        // also stop watching because subsequent updates will not
        // arrive anyway and the JS side may want to fall back to
        // web Geolocation.
        if code == 1 {
            isWatching = false
        }
        notifyListeners("gpsError", data: [
            "code": code,
            "message": error.localizedDescription,
        ])
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        // Surface the new status to JS so it can adjust the UI
        // (e.g. show a "permission needed" hint).
        let status: CLAuthorizationStatus
        if #available(iOS 14.0, *) {
            status = manager.authorizationStatus
        } else {
            status = CLLocationManager.authorizationStatus()
        }
        notifyListeners("gpsPermission", data: [
            "status": String(statusString(status))
        ])
    }

    private func statusString(_ s: CLAuthorizationStatus) -> String {
        switch s {
        case .notDetermined: return "not_determined"
        case .restricted: return "restricted"
        case .denied: return "denied"
        case .authorizedAlways: return "always"
        case .authorizedWhenInUse: return "when_in_use"
        @unknown default: return "unknown"
        }
    }
}
