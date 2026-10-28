import AppKit
import NotifierCore
import os
import ServiceManagement

/// `log stream --predicate 'subsystem == "io.github.junja0419.statusboard-notifier"'` 로 볼 수 있다
let log = Logger(subsystem: Bundle.main.bundleIdentifier ?? "statusboard-notifier", category: "app")

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let menu = NSMenu()
    private let socket = SuggestionSocket()
    private let notifier = Notifier()
    private var state: SuggestionSocket.State = .notConfigured
    /// 아직 유효하고 처리하지 않은 제안 (오래된 것부터) — 메뉴에 선택지로 보인다
    private var suggestions: [Suggestion] = []
    /// 알림을 이미 띄운 제안 / 고르거나 무시한 제안 (재접속 때 다시 오는 제안을 거른다)
    private var seen = SeenIDs(plist: UserDefaults.standard.object(forKey: "seenSuggestions"))
    private var handled = SeenIDs(plist: UserDefaults.standard.object(forKey: "handledSuggestions"))
    private var pruneTimer: Timer?
    /// 키체인은 읽을 때마다 접근 허용을 물을 수 있어 시작할 때 한 번만 읽고, 설정에서 새로 넣으면 그 값을 쓴다
    private var appKey: String?
    /// 적용 요청을 보내 놓고 답을 기다리는 제안 (두 번 적용하지 않게)
    private var applying: Set<String> = []

    private static let maxMenuSuggestions = 3

    func applicationWillFinishLaunching(_ notification: Notification) {
        // 앱이 꺼져 있을 때 알림 선택지를 눌러 실행돼도 응답을 받도록 가장 먼저 연결한다
        notifier.onAction = { [weak self] suggestion, action in self?.choose(suggestion, action) }
        notifier.onOpen = { [weak self] _ in self?.openMenu() }
        notifier.onAuthorized = { [weak self] in self?.showUnseen() }
        notifier.setUp()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = Self.editMenu()   // 설정 창에서 ⌘V 로 키를 붙여 넣을 수 있게
        menu.delegate = self
        statusItem.menu = menu
        updateIcon()

        socket.onState = { [weak self] state in
            log.info("connection: \(String(describing: state), privacy: .public)")
            self?.state = state
            self?.updateIcon()
        }
        socket.onSuggestion = { [weak self] suggestion in self?.received(suggestion) }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.socket.reconnectNow() }
        pruneTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in self?.prune() }

        prune()
        appKey = SettingsStore.appKey
        if SettingsStore.endpoint == nil || appKey == nil {
            runSettings()
        } else {
            connect()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        socket.stop()
    }

    private func connect() {
        socket.configure(endpoint: SettingsStore.endpoint, key: appKey)
    }

    private func runSettings() {
        guard let result = SettingsDialog.run(hasStoredKey: appKey != nil) else {
            updateIcon()
            return
        }
        if let key = result.newKey { appKey = key }
        connect()
    }

    // MARK: 제안

    private func received(_ suggestion: Suggestion) {
        log.info("suggestion \(suggestion.id, privacy: .public) for preset \(suggestion.presetID, privacy: .public)")
        prune()
        guard suggestion.expiresAt > Date(), !handled.contains(suggestion.id) else { return }
        if !suggestions.contains(where: { $0.id == suggestion.id }) {
            suggestions.append(suggestion)
        }
        notifier.register(suggestion)       // 이미 띄운 알림이라도 옵션이 살아 있게
        if !seen.contains(suggestion.id) { show(suggestion) }
    }

    /// 알림이 실제로 올라간 뒤에만 "띄움" 으로 기록한다 (권한 전에 실패하면 권한을 받은 뒤 다시 띄운다)
    private func show(_ suggestion: Suggestion) {
        notifier.show(suggestion) { [weak self] shown in
            guard let self, shown else { return }
            self.seen.insert(suggestion.id, expiresAt: suggestion.expiresAt)
            self.save()
        }
    }

    private func showUnseen() {
        for suggestion in suggestions where !seen.contains(suggestion.id) { show(suggestion) }
    }

    private func choose(_ suggestion: Suggestion, _ action: NotificationAction) {
        guard !handled.contains(suggestion.id), !applying.contains(suggestion.id) else { return }
        switch action {
        case .dismiss:
            finish(suggestion)
        case .apply(let presetID):
            // 알림은 내리되 제안은 결과가 나올 때까지 메뉴에 남긴다 — 실패하면 메뉴에서 다시 고를 수 있게
            notifier.dismissNotifications(ids: [suggestion.id])
            apply(suggestion, presetID: presetID)
        }
    }

    /// 고르거나 무시한 제안 — 다시 오더라도 알리지 않는다
    private func finish(_ suggestion: Suggestion) {
        handled.insert(suggestion.id, expiresAt: suggestion.expiresAt)
        seen.insert(suggestion.id, expiresAt: suggestion.expiresAt)
        save()
        suggestions.removeAll { $0.id == suggestion.id }
        notifier.forget(ids: [suggestion.id])
    }

    private func apply(_ suggestion: Suggestion, presetID: String) {
        guard let endpoint = SettingsStore.endpoint, let key = appKey else {
            notifier.showMessage(title: "바꾸지 못했습니다", body: "메뉴 막대의 Status Board → 설정에서 서버 주소와 키를 넣으세요.")
            return
        }
        log.info("apply preset \(presetID, privacy: .public)")
        applying.insert(suggestion.id)
        ApplyClient.apply(endpoint: endpoint, key: key, presetID: presetID, until: suggestion.until) { [weak self] outcome in
            guard let self else { return }
            self.applying.remove(suggestion.id)
            switch outcome {
            case .applied(let message):
                log.info("applied")
                self.finish(suggestion)
                self.notifier.showMessage(title: "Status Board", body: message)
            case .failed(let message):
                log.error("apply failed: \(message, privacy: .public)")
                let retry = self.suggestions.contains { $0.id == suggestion.id } ? " 메뉴 막대의 Status Board 에서 다시 고를 수 있습니다." : ""
                self.notifier.showMessage(title: "바꾸지 못했습니다", body: message + retry)
            }
        }
    }

    /// 만료된 제안과 기록을 정리하고, 지난 제안의 알림을 지운다
    private func prune() {
        let now = Date()
        let expired = suggestions.filter { $0.expiresAt <= now }.map(\.id)
        suggestions.removeAll { $0.expiresAt <= now }
        notifier.forget(ids: expired)
        notifier.removeExpiredDelivered(now: now)
        seen.prune(now: now)
        handled.prune(now: now)
        save()
    }

    private func save() {
        UserDefaults.standard.set(seen.plist, forKey: "seenSuggestions")
        UserDefaults.standard.set(handled.plist, forKey: "handledSuggestions")
    }

    // MARK: 메뉴 막대

    private func updateIcon() {
        let connected = state == .connected
        let image = NSImage(systemSymbolName: connected ? "rectangle.inset.filled" : "rectangle.dashed",
                            accessibilityDescription: "Status Board")
        image?.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = "Status Board — " + statusText
    }

    private var statusText: String {
        switch state {
        case .notConfigured: return "서버 주소와 키를 설정하세요"
        case .connecting: return "연결 중…"
        case .connected: return "연결됨"
        case .refused: return "접속 거부 — 설정에서 APP_KEY 를 확인하세요"
        case .waiting(let delay): return "연결 끊김 — \(Int(delay))초 뒤 다시 시도"
        }
    }

    private func openMenu() {
        // 알림 본문을 누르면 같은 선택지를 메뉴로 보여 준다
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.statusItem.button?.performClick(nil) }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        prune()
        menu.removeAllItems()
        menu.addItem(disabled(statusText))
        menu.addItem(.separator())

        if suggestions.isEmpty {
            menu.addItem(disabled("받은 제안 없음"))
        }
        for suggestion in suggestions.suffix(Self.maxMenuSuggestions).reversed() {
            menu.addItem(disabled(suggestion.title))
            for (index, preset) in suggestion.choices().enumerated() {
                let item = NSMenuItem(title: Korean.choiceTitle(preset.name, suggested: index == 0),
                                      action: #selector(chooseFromMenu(_:)), keyEquivalent: "")
                item.target = self
                item.indentationLevel = 1
                item.representedObject = MenuChoice(suggestion: suggestion, action: .apply(presetID: preset.id))
                menu.addItem(item)
            }
            let dismiss = NSMenuItem(title: "무시", action: #selector(chooseFromMenu(_:)), keyEquivalent: "")
            dismiss.target = self
            dismiss.indentationLevel = 1
            dismiss.representedObject = MenuChoice(suggestion: suggestion, action: .dismiss)
            menu.addItem(dismiss)
        }

        menu.addItem(.separator())
        menu.addItem(action("지금 다시 연결", #selector(reconnect)))
        menu.addItem(action("설정…", #selector(openSettings)))
        menu.addItem(action("알림 설정 열기…", #selector(openNotificationSettings)))
        let login = action("로그인 시 실행", #selector(toggleLoginItem))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Status Board 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    @objc private func chooseFromMenu(_ sender: NSMenuItem) {
        guard let choice = sender.representedObject as? MenuChoice else { return }
        choose(choice.suggestion, choice.action)
    }

    @objc private func reconnect() {
        socket.reconnectNow()
    }

    @objc private func openSettings() {
        runSettings()
    }

    @objc private func openNotificationSettings() {
        let id = Bundle.main.bundleIdentifier ?? ""
        if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=\(id)") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func toggleLoginItem() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
            } else {
                try service.register()
                if service.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
            }
        } catch {
            NSApp.activate(ignoringOtherApps: true)
            let alert = NSAlert()
            alert.messageText = "로그인 항목을 바꾸지 못했습니다"
            alert.informativeText = "\(error.localizedDescription)\n앱을 '응용 프로그램' 폴더로 옮긴 뒤 다시 시도하세요."
            alert.runModal()
        }
    }

    private func disabled(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    private func action(_ title: String, _ selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        return item
    }

    /// 메뉴 막대 전용 앱에는 기본 메뉴가 없어 ⌘C·⌘V 가 텍스트 필드에 닿지 않는다
    private static func editMenu() -> NSMenu {
        let main = NSMenu()
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "편집")
        edit.addItem(withTitle: "실행 취소", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "오려두기", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "복사하기", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "붙여넣기", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "전체 선택", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        return main
    }
}

private final class MenuChoice {
    let suggestion: Suggestion
    let action: NotificationAction

    init(suggestion: Suggestion, action: NotificationAction) {
        self.suggestion = suggestion
        self.action = action
    }
}
