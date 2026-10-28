import AppKit
import NotifierCore
import Security

/// 서버 주소는 UserDefaults, APP_KEY 는 키체인에 둔다.
enum SettingsStore {
    private static let serverURLKey = "serverURL"

    static var serverURL: String {
        get { UserDefaults.standard.string(forKey: serverURLKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: serverURLKey) }
    }

    static var endpoint: Endpoint? {
        if case .success(let endpoint) = Endpoint.parse(serverURL) { return endpoint }
        return nil
    }

    static var appKey: String? { Keychain.read() }

    @discardableResult
    static func saveAppKey(_ key: String) -> Bool { Keychain.save(key) }
}

enum Keychain {
    private static let service = (Bundle.main.bundleIdentifier ?? "statusboard-notifier") + ".app-key"
    private static let account = "APP_KEY"

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func read() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data, let key = String(data: data, encoding: .utf8), !key.isEmpty
        else { return nil }
        return key
    }

    static func save(_ key: String) -> Bool {
        SecItemDelete(baseQuery as CFDictionary)
        var query = baseQuery
        query[kSecValueData as String] = Data(key.utf8)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }
}

/// "설정…" 대화상자. 취소하면 nil, 저장하면 새로 넣은 키(그대로 두었으면 nil)를 돌려준다.
/// hasStoredKey: 키체인에 키가 이미 있는지 — 여기서 키체인을 다시 읽지 않으려고 부르는 쪽이 알려 준다
enum SettingsDialog {
    struct Result {
        let newKey: String?
    }

    static func run(hasStoredKey: Bool) -> Result? {
        NSApp.activate(ignoringOtherApps: true)
        var message: String?
        while true {
            let alert = NSAlert()
            alert.messageText = "Status Board 설정"
            alert.informativeText = message ?? "서버 주소와 APP_KEY 를 넣으세요. 키는 이 Mac 의 키체인에 저장됩니다."
            alert.addButton(withTitle: "저장")
            alert.addButton(withTitle: "취소")

            let urlField = NSTextField(string: SettingsStore.serverURL)
            urlField.placeholderString = "https://<도메인>"
            let keyField = NSSecureTextField(string: "")
            keyField.placeholderString = hasStoredKey ? "저장된 키 유지 (바꿀 때만 입력)" : "APP_KEY"
            let stack = NSStackView(views: [label("서버 주소"), urlField, label("APP_KEY"), keyField])
            stack.orientation = .vertical
            stack.alignment = .leading
            stack.spacing = 6
            stack.frame = NSRect(x: 0, y: 0, width: 340, height: 104)
            for field in [urlField, keyField] {
                field.widthAnchor.constraint(equalToConstant: 340).isActive = true
            }
            alert.accessoryView = stack
            alert.window.initialFirstResponder = urlField

            guard alert.runModal() == .alertFirstButtonReturn else { return nil }

            switch Endpoint.parse(urlField.stringValue) {
            case .failure(.empty):
                message = "서버 주소를 넣으세요."
                continue
            case .failure(.insecure):
                message = "키가 평문으로 오가지 않도록 https:// 주소만 쓸 수 있습니다 (이 Mac 의 localhost 만 예외)."
                continue
            case .failure(.invalid):
                message = "주소 형식이 맞지 않습니다. 예: https://<도메인>"
                continue
            case .success(let endpoint):
                let key = keyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
                if key.isEmpty && !hasStoredKey {
                    message = "APP_KEY 를 넣으세요."
                    continue
                }
                if !key.isEmpty && !SettingsStore.saveAppKey(key) {
                    message = "키체인에 저장하지 못했습니다. 다시 시도하세요."
                    continue
                }
                SettingsStore.serverURL = endpoint.base.absoluteString
                return Result(newKey: key.isEmpty ? nil : key)
            }
        }
    }

    private static func label(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: NSFont.smallSystemFontSize, weight: .medium)
        return label
    }
}
