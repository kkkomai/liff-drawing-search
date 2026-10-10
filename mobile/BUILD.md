# BikeLog iOS ビルド手順 (Capacitor)

This directory holds the iOS shell that hosts the existing web app in a
WKWebView and exposes a native CLLocationManager bridge so background
GPS keeps flowing when the screen is locked. Build requires a Mac
with Xcode 15+ and a configured Apple Developer account.

## Prerequisites

- macOS 13+ with Xcode 15+
- Xcode command line tools: `xcode-select --install`
- Node 18+ and npm
- CocoaPods (`sudo gem install cocoapods`)
- Apple Developer account with a configured App ID
  (`jp.kkomai.bikelog`) and provisioning profile
- The `liff-drawing-search.onrender.com` service deployed and reachable

## First-time setup

```bash
cd mobile
npm install
npx cap sync ios          # copies www/ + capacitor.config.json into ios/
npx cap open ios          # opens BikeLog.xcworkspace in Xcode
```

In Xcode:

1. Select the **BikeLog** target, then **Signing & Capabilities**
2. Set the **Team** to your Apple Developer team
3. Confirm the Bundle Identifier is `jp.kkomai.bikelog`
4. Add the **Background Modes** capability and check **Location
   updates** (this is what the `UIBackgroundModes = [location]`
   entry in Info.plist already configures; the UI checkbox just makes
   it visible)
5. Plug in an iPhone running iOS 14+ and select it as the run
   destination

## Daily rebuild

```bash
cd mobile
npm install                                              # if package.json changed
npx cap sync ios                                         # after www/ or config changes
xcodebuild -workspace ios/App/BikeLog.xcworkspace \
  -scheme BikeLog -configuration Debug -sdk iphoneos \
  -destination 'platform=iOS,name=岩野のiPhone' \
  CODE_SIGNING_ALLOWED=NO
```

Or just open Xcode and hit Cmd-R.

## Verifying background GPS

1. Build and run on a physical iPhone (the simulator cannot move
   the user in the real world, so the background path is
   unverifiable there).
2. Tap the GPS start button inside the app. The first time you
   should see the iOS system prompt asking for "Always" location
   access. Approve it.
3. Press the side button to lock the screen. Drive or walk
   somewhere — the polyline in the app should keep growing.
4. Unlock the screen; the new samples will already be on the
   polyline.

If the polyline never extends past the first sample, the
`allowsBackgroundLocationUpdates` flag in `BikeLogGPSPlugin.swift`
is not taking effect — re-check that the Background Modes
capability is enabled in Xcode and that you have a real device
(Background updates are silently denied on the simulator).

## Common issues

- **`npm install` fails with ERESOLVE`**: clear the cache
  (`npm cache clean --force`) and retry; capacitor 6 supports
  Node 18+.
- **Xcode says "No profiles found"**: the Bundle Identifier is
  already taken on the developer account, or the App ID does not
  have the "Background Modes" capability enabled in
  developer.apple.com.
- **Plugin is not registered when the WebView loads**: confirm
  `BikeLogGPSPlugin.swift` and `AppDelegate.swift` are both in
  the "Compile Sources" phase of the BikeLog target. The default
  `npx cap add ios` template only includes `AppDelegate.swift`;
  drag the new plugin file in from Finder onto the project
  navigator.

## Updating the web payload

The web app lives in `../bike/` and is served from
`liff-drawing-search.onrender.com`. Because the WKWebView in this
shell loads the URL directly (not a bundled `www/`), edits to
`bike/index.html` show up on the next app launch without a
Capacitor rebuild. Only `capacitor.config.json` and the native
plugin require a `npx cap sync` cycle.
