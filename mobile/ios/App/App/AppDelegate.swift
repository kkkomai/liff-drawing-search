import UIKit
import Capacitor

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        // Capacitor's bridge is initialised here. The bridge is what
        // wires the WKWebView up to our BikeLogGPSPlugin and exposes
        // the plugin's methods (requestPermission / startWatching /
        // stopWatching / getCurrentPosition) to JavaScript.
        return true
    }

    // Register the GPS plugin with the Capacitor bridge. Must run
    // before the first WebView is created so JS can call the
    // plugin as soon as the page is ready.
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?,
                     bridgeProxy: CAPBridgeProxy) {
        bridgeProxy.registerPlugin(BikeLogGPSPlugin.self)
    }

    func application(_ application: UIApplication,
                     configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        return UISceneConfiguration(name: "Default Configuration", sessionRole: connectingSceneSession.role)
    }
}
