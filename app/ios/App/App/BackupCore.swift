// Camera-roll backup for the iPhone app (LOCAL_APP_PLAN.md §13). This file has no Capacitor/UIKit
// dependencies so it can be type-checked on a Mac; PhotoBackupPlugin.swift bridges it to the web app.
//
// - Listing: PhotoKit, with a persistent change token (iOS 16+) so later passes only see what changed.
// - Hashing: the original resource is written to Caches/backup/ (downloading from iCloud if allowed) and
//   SHA-256'd while it streams. The same file is what gets uploaded, so the hash matches.
// - Uploading: a background URLSession, so transfers continue while the app is suspended.

import CryptoKit
import Foundation
import Network
import Photos

enum BackupError: LocalizedError {
    case notFound(String), noResource(String), notPrepared(String)
    var errorDescription: String? {
        switch self {
        case .notFound(let id): return "The item \(id) is no longer on this phone"
        case .noResource(let id): return "No original file for \(id)"
        case .notPrepared(let id): return "\(id) has to be hashed before it's uploaded"
        }
    }
}

struct AssetInfo {
    let id: String, modified: Double, created: Double?, kind: String, filename: String
    let screenshot: Bool, albums: [String]
    var dict: [String: Any] {
        var d: [String: Any] = ["id": id, "modified": modified, "kind": kind, "filename": filename,
                                "screenshot": screenshot, "albums": albums, "bytes": NSNull()]
        d["created"] = created ?? NSNull()
        return d
    }
}

final class BackupCore: NSObject {
    static let shared = BackupCore()
    static let sessionId = "app.photoframing.backup"
    static let liveSuffix = "/live"

    /// Called when an upload finishes (also in the background); the plugin forwards it to the web app.
    var onUploadFinished: (() -> Void)?
    /// The system hands this over in application(_:handleEventsForBackgroundURLSession:completionHandler:).
    var backgroundCompletion: (() -> Void)?

    private let queue = DispatchQueue(label: "app.photoframing.backup.state")
    private var bodies: [Int: Data] = [:]
    private lazy var session: URLSession = {
        let c = URLSessionConfiguration.background(withIdentifier: BackupCore.sessionId)
        c.sessionSendsLaunchEvents = true
        c.isDiscretionary = false
        return URLSession(configuration: c, delegate: self, delegateQueue: nil)
    }()
    private let monitor = NWPathMonitor()
    private var path: NWPath?

    override init() {
        super.init()
        monitor.pathUpdateHandler = { [weak self] p in self?.path = p }
        monitor.start(queue: DispatchQueue(label: "app.photoframing.backup.net"))
    }

    /// Recreate the background session at launch so finished transfers are delivered.
    func resume() { _ = session }

    // MARK: access

    static func access(_ s: PHAuthorizationStatus) -> String {
        switch s {
        case .authorized: return "full"
        case .limited: return "limited"
        case .notDetermined: return "not-determined"
        default: return "denied"
        }
    }

    func access() -> String { BackupCore.access(PHPhotoLibrary.authorizationStatus(for: .readWrite)) }

    func requestAccess(_ done: @escaping (String) -> Void) {
        PHPhotoLibrary.requestAuthorization(for: .readWrite) { done(BackupCore.access($0)) }
    }

    // MARK: listing

    /// Changes since `token` (base64 of an archived PHPersistentChangeToken); everything when nil or expired.
    func changes(since token: String?) -> [String: Any] {
        let library = PHPhotoLibrary.shared()
        let now = library.currentChangeToken
        let next = (try? NSKeyedArchiver.archivedData(withRootObject: now, requiringSecureCoding: true))?.base64EncodedString() ?? ""
        let albums = albumIndex()
        if let token, let data = Data(base64Encoded: token),
           let old = try? NSKeyedUnarchiver.unarchivedObject(ofClass: PHPersistentChangeToken.self, from: data),
           let changes = try? library.fetchPersistentChanges(since: old) {
            var touched = Set<String>(), deleted = Set<String>()
            for change in changes {
                guard let details = try? change.changeDetails(for: .asset) else { continue }
                touched.formUnion(details.insertedLocalIdentifiers)
                touched.formUnion(details.updatedLocalIdentifiers)
                deleted.formUnion(details.deletedLocalIdentifiers)
            }
            touched.subtract(deleted)
            let fetched = PHAsset.fetchAssets(withLocalIdentifiers: Array(touched), options: nil)
            var assets: [[String: Any]] = []
            fetched.enumerateObjects { a, _, _ in assets += self.describe(a, albums: albums).map(\.dict) }
            let gone = deleted.flatMap { [$0, $0 + BackupCore.liveSuffix] }
            return ["assets": assets, "deleted": gone, "token": next, "full": false]
        }
        let opts = PHFetchOptions()
        opts.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        opts.includeAssetSourceTypes = [.typeUserLibrary, .typeCloudShared, .typeiTunesSynced]
        var assets: [[String: Any]] = []
        PHAsset.fetchAssets(with: opts).enumerateObjects { a, _, _ in
            guard a.mediaType == .image || a.mediaType == .video else { return }
            assets += self.describe(a, albums: albums).map(\.dict)
        }
        return ["assets": assets, "deleted": [], "token": next, "full": true]
    }

    private func albumIndex() -> [String: [String]] {
        var index: [String: [String]] = [:]
        PHAssetCollection.fetchAssetCollections(with: .album, subtype: .any, options: nil).enumerateObjects { c, _, _ in
            guard let title = c.localizedTitle else { return }
            PHAsset.fetchAssets(in: c, options: nil).enumerateObjects { a, _, _ in index[a.localIdentifier, default: []].append(title) }
        }
        return index
    }

    private func describe(_ a: PHAsset, albums: [String: [String]]) -> [AssetInfo] {
        let resources = PHAssetResource.assetResources(for: a)
        let main = resources.first { $0.type == .photo || $0.type == .video } ?? resources.first
        let ms = { (d: Date?) in d.map { $0.timeIntervalSince1970 * 1000 } }
        let modified = ms(a.modificationDate) ?? ms(a.creationDate) ?? 0
        let tags = albums[a.localIdentifier] ?? []
        var out = [AssetInfo(id: a.localIdentifier, modified: modified, created: ms(a.creationDate),
                             kind: a.mediaType == .video ? "video" : "photo",
                             filename: main?.originalFilename ?? "\(a.localIdentifier).jpg",
                             screenshot: a.mediaSubtypes.contains(.photoScreenshot), albums: tags)]
        if a.mediaSubtypes.contains(.photoLive), let video = resources.first(where: { $0.type == .pairedVideo }) {
            out.append(AssetInfo(id: a.localIdentifier + BackupCore.liveSuffix, modified: modified, created: ms(a.creationDate),
                                 kind: "video", filename: video.originalFilename, screenshot: false, albums: tags))
        }
        return out
    }

    // MARK: hashing

    private var folder: URL {
        let f = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("backup", isDirectory: true)
        try? FileManager.default.createDirectory(at: f, withIntermediateDirectories: true)
        return f
    }

    func file(for id: String) -> URL {
        let safe = id.map { $0.isLetter || $0.isNumber || $0 == "-" ? $0 : "_" }
        return folder.appendingPathComponent(String(safe))
    }

    /// Write the original to Caches/backup and hash it. Calls back off the main thread.
    func hash(id: String, allowNetwork: Bool, done: @escaping (Result<(String, Int), Error>) -> Void) {
        let live = id.hasSuffix(BackupCore.liveSuffix)
        let localId = live ? String(id.dropLast(BackupCore.liveSuffix.count)) : id
        guard let asset = PHAsset.fetchAssets(withLocalIdentifiers: [localId], options: nil).firstObject else {
            return done(.failure(BackupError.notFound(id)))
        }
        let resources = PHAssetResource.assetResources(for: asset)
        let wanted: [PHAssetResourceType] = live ? [.pairedVideo] : asset.mediaType == .video ? [.video] : [.photo]
        guard let resource = resources.first(where: { wanted.contains($0.type) }) else {
            return done(.failure(BackupError.noResource(id)))
        }
        let target = file(for: id)
        try? FileManager.default.removeItem(at: target)
        let opts = PHAssetResourceRequestOptions()
        opts.isNetworkAccessAllowed = allowNetwork
        PHAssetResourceManager.default().writeData(for: resource, toFile: target, options: opts) { error in
            if let error { return done(.failure(error)) }
            do { done(.success(try BackupCore.sha256(of: target))) } catch { done(.failure(error)) }
        }
    }

    static func sha256(of url: URL) throws -> (String, Int) {
        let h = try FileHandle(forReadingFrom: url)
        defer { try? h.close() }
        var digest = SHA256(), total = 0
        while let chunk = try h.read(upToCount: 1 << 20), !chunk.isEmpty {
            digest.update(data: chunk)
            total += chunk.count
        }
        return (digest.finalize().map { String(format: "%02x", $0) }.joined(), total)
    }

    // MARK: uploads

    private var stateFile: URL { folder.appendingPathComponent("uploads.json") }

    private func loadStates() -> [String: [String: Any]] {
        guard let data = try? Data(contentsOf: stateFile),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] else { return [:] }
        return obj
    }

    private func saveStates(_ s: [String: [String: Any]]) {
        if let data = try? JSONSerialization.data(withJSONObject: s) { try? data.write(to: stateFile, options: .atomic) }
    }

    func upload(assetId: String, url: URL, headers: [String: String], wifiOnly: Bool) throws {
        let source = file(for: assetId)
        guard FileManager.default.fileExists(atPath: source.path) else { throw BackupError.notPrepared(assetId) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.allowsCellularAccess = !wifiOnly
        req.allowsExpensiveNetworkAccess = !wifiOnly
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let task = session.uploadTask(with: req, fromFile: source)
        task.taskDescription = assetId
        queue.sync {
            var s = loadStates()
            s[assetId] = ["assetId": assetId, "state": "queued"]
            saveStates(s)
        }
        task.resume()
    }

    func uploads() -> [[String: Any]] { queue.sync { Array(loadStates().values) } }

    func forget(_ ids: [String]) {
        queue.sync {
            var s = loadStates()
            for id in ids {
                s.removeValue(forKey: id)
                try? FileManager.default.removeItem(at: file(for: id))
            }
            saveStates(s)
        }
    }

    // MARK: conditions

    func conditions(charging: Bool) -> [String: Bool] {
        let p = path ?? monitor.currentPath
        let online = p.status == .satisfied
        return ["online": online, "wifi": online && !p.isExpensive && !p.isConstrained, "charging": charging]
    }
}

/// What the web app last told the native side (origin, device, settings), for passes run while it's closed.
struct PassConfig: Codable {
    var enabled: Bool
    var origin: String
    var deviceId: String
    var wifiOnly: Bool
    var videos: Bool
    var videosWhileCharging: Bool
    var screenshots: Bool
    var from: Double?
    var albums: [String]?
    var auth: String?          // paired over Wi-Fi: the phone's token
}

extension BackupCore {
    private static let configKey = "backup.config", tokenKey = "backup.bgToken"

    func configure(_ config: PassConfig) {
        if let data = try? JSONEncoder().encode(config) { UserDefaults.standard.set(data, forKey: BackupCore.configKey) }
        // Background passes only look at items newer than this; the backlog is the open app's job.
        if UserDefaults.standard.string(forKey: BackupCore.tokenKey) == nil,
           let token = changes(since: nil)["token"] as? String {
            UserDefaults.standard.set(token, forKey: BackupCore.tokenKey)
        }
    }

    func config() -> PassConfig? {
        UserDefaults.standard.data(forKey: BackupCore.configKey).flatMap { try? JSONDecoder().decode(PassConfig.self, from: $0) }
    }

    static func wanted(_ a: [String: Any], _ c: PassConfig) -> Bool {
        if a["kind"] as? String == "video" && !c.videos { return false }
        if a["screenshot"] as? Bool == true && !c.screenshots { return false }
        if let from = c.from, let created = a["created"] as? Double, created < from { return false }
        if let albums = c.albums, !((a["albums"] as? [String]) ?? []).contains(where: albums.contains) { return false }
        return true
    }

    /// A short pass while the app is closed: new items → hash → ask the Mac → queue uploads. The ledger in the
    /// app catches up next time it opens; the Mac's check stops anything being sent twice. Blocks; call off the main thread.
    @discardableResult
    func backgroundPass(charging: Bool, limit: Int = 30, deadline: Date) -> Int {
        guard let c = config(), c.enabled, let origin = URL(string: c.origin), access() != "denied" else { return 0 }
        guard let token = UserDefaults.standard.string(forKey: BackupCore.tokenKey) else { return 0 }  // set by configure()
        let ch = changes(since: token)
        let all = ((ch["assets"] as? [[String: Any]]) ?? []).filter { BackupCore.wanted($0, c) }
        let batch = all.filter { !($0["kind"] as? String == "video" && c.videosWhileCharging && !charging) }.prefix(limit)
        var hashed: [(asset: [String: Any], sha: String)] = []
        for a in batch {
            guard Date() < deadline, let id = a["id"] as? String else { break }
            let sem = DispatchSemaphore(value: 0)
            var sha: String?
            hash(id: id, allowNetwork: !c.wifiOnly || conditions(charging: charging)["wifi"] == true) { r in
                if case .success(let (h, _)) = r { sha = h }
                sem.signal()
            }
            sem.wait()
            if let sha { hashed.append((a, sha)) }
        }
        guard !hashed.isEmpty, let missing = check(origin: origin, auth: c.auth, hashes: hashed.map(\.sha)) else {
            if hashed.count == all.count, let next = ch["token"] as? String { UserDefaults.standard.set(next, forKey: BackupCore.tokenKey) }
            return 0
        }
        var queued = 0, sent = Set<String>()
        for (a, sha) in hashed where missing.contains(sha) && !sent.contains(sha) {
            guard let id = a["id"] as? String else { continue }
            var headers = ["X-Device-Id": c.deviceId, "X-Asset-Id": id, "X-Sha256": sha,
                           "X-Filename": a["filename"] as? String ?? id]
            if let created = a["created"] as? Double { headers["X-Last-Modified"] = String(Int64(created)) }
            if let auth = c.auth { headers["Authorization"] = "Bearer \(auth)" }
            if (try? upload(assetId: id, url: origin.appendingPathComponent("api/uploads/raw"), headers: headers, wifiOnly: c.wifiOnly)) != nil {
                queued += 1
                sent.insert(sha)
            }
        }
        // Advance only when every new item was looked at; otherwise the next pass picks up the rest.
        if hashed.count == all.count, let next = ch["token"] as? String { UserDefaults.standard.set(next, forKey: BackupCore.tokenKey) }
        return queued
    }

    private func check(origin: URL, auth: String?, hashes: [String]) -> Set<String>? {
        var req = URLRequest(url: origin.appendingPathComponent("api/backup/check"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let auth { req.setValue("Bearer \(auth)", forHTTPHeaderField: "Authorization") }
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["hashes": Array(Set(hashes))])
        req.timeoutInterval = 20
        let sem = DispatchSemaphore(value: 0)
        var missing: Set<String>?
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            if (resp as? HTTPURLResponse)?.statusCode == 200, let data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                missing = Set((obj["missing"] as? [String]) ?? [])
            }
            sem.signal()
        }.resume()
        sem.wait()
        return missing
    }
}

extension BackupCore: URLSessionDataDelegate {
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        queue.sync { bodies[dataTask.taskIdentifier, default: Data()].append(data) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let id = task.taskDescription else { return }
        let status = (task.response as? HTTPURLResponse)?.statusCode
        queue.sync {
            let body = bodies.removeValue(forKey: task.taskIdentifier).flatMap { String(data: $0, encoding: .utf8) } ?? ""
            var s = loadStates()
            var entry: [String: Any] = ["assetId": id, "state": error == nil && status != nil ? "done" : "failed", "body": body]
            if let status { entry["status"] = status }
            if let error { entry["error"] = error.localizedDescription }
            s[id] = entry
            saveStates(s)
        }
        if status == 200 { try? FileManager.default.removeItem(at: file(for: id)) }
        onUploadFinished?()
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async {
            self.backgroundCompletion?()
            self.backgroundCompletion = nil
        }
    }
}
