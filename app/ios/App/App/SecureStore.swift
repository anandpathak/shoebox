// The read-only S3 key for browsing without the Mac, kept in the iPhone Keychain: only on this device
// (never in iCloud Keychain), readable after the first unlock so background work could use it.

import Foundation
import Security

enum SecureStore {
    static let service = "app.shoebox.viewer"
    static let account = "access"

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    @discardableResult
    static func set(_ value: String) -> Bool {
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }

    static func get() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear() { SecItemDelete(query as CFDictionary) }
}
