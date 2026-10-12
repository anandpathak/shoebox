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
    ]
    private let core = BackupCore.shared
    private let work = DispatchQueue(label: "app.photoframing.backup.work", qos: .utility)

    override public func load() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        core.onUploadFinished = { [weak self] in self?.notifyListeners("uploadFinished", data: [:]) }
        BackgroundBackup.shared.plugin = self
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

    @objc func finishBackground(_ call: CAPPluginCall) {
        BackgroundBackup.shared.finish(success: true)
        call.resolve()
    }

    func runInBackground() { notifyListeners("backgroundRun", data: [:], retainUntilConsumed: true) }
}

/// BGTaskScheduler glue. Identifiers are listed in Info.plist (BGTaskSchedulerPermittedIdentifiers).
final class BackgroundBackup {
    static let shared = BackgroundBackup()
    static let refresh = "app.photoframing.backup.refresh"
    static let processing = "app.photoframing.backup.processing"

    weak var plugin: PhotoBackupPlugin? {
        didSet { if pending != nil { plugin?.runInBackground() } }
    }
    private var pending: BGTask?

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

    private func handle(_ task: BGTask) {
        schedule()
        BackupCore.shared.resume()
        pending = task
        task.expirationHandler = { [weak self] in self?.finish(success: false) }
        // The web app (and its backup engine) loads with the app; it answers with finishBackground().
        plugin?.runInBackground()
    }

    func finish(success: Bool) {
        DispatchQueue.main.async {
            self.pending?.setTaskCompleted(success: success)
            self.pending = nil
        }
    }
}

/// The app's web view, with the local PhotoBackup plugin registered.
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(PhotoBackupPlugin())
    }
}
