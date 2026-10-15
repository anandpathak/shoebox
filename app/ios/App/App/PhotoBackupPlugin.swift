// Bridges BackupCore to the web app as the `PhotoBackup` plugin (web/src/backup/native.ts), and runs the
// background passes iOS schedules (BGAppRefreshTask for a quick check, BGProcessingTask on power + Wi-Fi).

import BackgroundTasks
import Capacitor
import UIKit

@objc(PhotoBackupPlugin)
public class PhotoBackupPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PhotoBackupPlugin"
    public let jsName = "PhotoBackup"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "access", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestAccess", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "changes", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "hash", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "upload", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "uploads", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "forget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "conditions", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "finishBackground", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "configure", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setViewerAccess", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getViewerAccess", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearViewerAccess", returnType: CAPPluginReturnPromise),
    ]
    private let core = BackupCore.shared
    private let work = DispatchQueue(label: "app.shoebox.backup.work", qos: .utility)

    override public func load() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        core.onUploadFinished = { [weak self] in self?.notifyListeners("uploadFinished", data: [:]) }
    }

    @objc func access(_ call: CAPPluginCall) { call.resolve(["access": core.access()]) }

    @objc func requestAccess(_ call: CAPPluginCall) { core.requestAccess { call.resolve(["access": $0]) } }

    @objc func changes(_ call: CAPPluginCall) {
        let token = call.getString("token")
        work.async { call.resolve(self.core.changes(since: token)) }
    }

    @objc func hash(_ call: CAPPluginCall) {
        guard let id = call.getString("id") else { return call.reject("id is required") }
        core.hash(id: id, allowNetwork: call.getBool("allowNetwork") ?? false) { result in
            switch result {
            case .success(let (sha, bytes)): call.resolve(["sha256": sha, "bytes": bytes])
            case .failure(let error): call.reject(error.localizedDescription)
            }
        }
    }

    @objc func upload(_ call: CAPPluginCall) {
        guard let id = call.getString("assetId"), let s = call.getString("url"), let url = URL(string: s) else {
            return call.reject("assetId and url are required")
        }
        let headers = (call.getObject("headers") ?? [:]).compactMapValues { $0 as? String }
        do {
            try core.upload(assetId: id, url: url, headers: headers, wifiOnly: call.getBool("wifiOnly") ?? true)
            call.resolve()
        } catch { call.reject(error.localizedDescription) }
    }

    @objc func uploads(_ call: CAPPluginCall) { call.resolve(["uploads": core.uploads()]) }

    @objc func forget(_ call: CAPPluginCall) {
        core.forget(call.getArray("ids", String.self) ?? [])
        call.resolve()
    }

    @objc func conditions(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            let state = UIDevice.current.batteryState
            call.resolve(self.core.conditions(charging: state == .charging || state == .full))
        }
    }

    @objc func finishBackground(_ call: CAPPluginCall) { call.resolve() }

    // The read-only S3 key for browsing without the Mac (see SecureStore.swift).
    @objc func setViewerAccess(_ call: CAPPluginCall) {
        guard let json = call.getString("json") else { return call.reject("json is required") }
        SecureStore.set(json) ? call.resolve() : call.reject("Couldn't save to the Keychain")
    }

    @objc func getViewerAccess(_ call: CAPPluginCall) {
        if let json = SecureStore.get() { call.resolve(["json": json]) } else { call.resolve(["json": NSNull()]) }
    }

    @objc func clearViewerAccess(_ call: CAPPluginCall) {
        SecureStore.clear()
        call.resolve()
    }

    /// The web app passes its origin, device id and backup settings on every run, for passes made while it's closed.
    @objc func configure(_ call: CAPPluginCall) {
        guard let origin = call.getString("origin"), let device = call.getString("deviceId") else {
            return call.reject("origin and deviceId are required")
        }
        let c = PassConfig(enabled: call.getBool("enabled") ?? false, origin: origin, deviceId: device,
                           wifiOnly: call.getBool("wifiOnly") ?? true, videos: call.getBool("videos") ?? true,
                           videosWhileCharging: call.getBool("videosWhileCharging") ?? true,
                           screenshots: call.getBool("screenshots") ?? false, from: call.getDouble("from"),
                           albums: call.getArray("albums", String.self), auth: call.getString("auth"))
        work.async {
            self.core.configure(c)
            call.resolve()
        }
    }
}

/// BGTaskScheduler glue. Identifiers are listed in Info.plist (BGTaskSchedulerPermittedIdentifiers).
final class BackgroundBackup {
    static let shared = BackgroundBackup()
    static let refresh = "app.shoebox.backup.refresh"
    static let processing = "app.shoebox.backup.processing"


    /// Must run before the app finishes launching.
    func register() {
        for id in [BackgroundBackup.refresh, BackgroundBackup.processing] {
            BGTaskScheduler.shared.register(forTaskWithIdentifier: id, using: nil) { self.handle($0) }
        }
    }

    func schedule() {
        let quick = BGAppRefreshTaskRequest(identifier: BackgroundBackup.refresh)
        quick.earliestBeginDate = Date(timeIntervalSinceNow: 2 * 3600)
        let long = BGProcessingTaskRequest(identifier: BackgroundBackup.processing)
        long.requiresExternalPower = true
        long.requiresNetworkConnectivity = true
        try? BGTaskScheduler.shared.submit(quick)
        try? BGTaskScheduler.shared.submit(long)
    }

    /// Runs the native pass (BackupCore.backgroundPass); uploads it queues carry on in the background session.
    private func handle(_ task: BGTask) {
        schedule()
        BackupCore.shared.resume()
        let long = task is BGProcessingTask
        let deadline = Date(timeIntervalSinceNow: long ? 600 : 25)
        let charging = DispatchQueue.main.sync { () -> Bool in
            UIDevice.current.isBatteryMonitoringEnabled = true
            return UIDevice.current.batteryState == .charging || UIDevice.current.batteryState == .full
        }
        let lock = NSLock()
        var completed = false
        let complete = { (ok: Bool) in
            lock.lock(); defer { lock.unlock() }
            if !completed { completed = true; task.setTaskCompleted(success: ok) }
        }
        task.expirationHandler = { complete(false) }      // the pass also stops itself at `deadline`
        DispatchQueue.global(qos: .utility).async {
            BackupCore.shared.backgroundPass(charging: charging, limit: long ? 500 : 30, deadline: deadline)
            complete(true)
        }
    }
}

/// The app's web view, with the local PhotoBackup plugin registered.
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(PhotoBackupPlugin())
    }
}
