import AppKit
import NotifierCore
import UserNotifications

/// macOS 알림. 제안마다 카테고리를 등록해 선택지(제안 프리셋 → 다른 프리셋 → 무시)를 붙인다.
/// 동작이 여럿이면 macOS 가 "옵션" 드롭다운으로 묶어 보여 준다.
/// 카테고리 목록은 이 객체가 메인 스레드에서 들고 있다가 통째로 등록한다 — 시스템에서 읽어 와 고쳐 쓰면
/// 제안이 몰려올 때 서로 덮어써 일부 알림의 선택지가 사라진다.
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var onAction: ((Suggestion, NotificationAction) -> Void)?
    var onOpen: ((Suggestion) -> Void)?
    /// 알림 권한을 받은 뒤 (처음 실행에서 권한 전에 못 띄운 제안을 다시 띄우려고)
    var onAuthorized: (() -> Void)?

    private let center = UNUserNotificationCenter.current()
    private var categories: [String: UNNotificationCategory] = [:]   // 제안 id → 카테고리
    private static let categoryPrefix = "suggestion."
    private static let suggestionKey = "suggestion"

    func setUp() {
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            log.info("notification permission: \(granted, privacy: .public) \(String(describing: error), privacy: .public)")
            if granted { DispatchQueue.main.async { self.onAuthorized?() } }
        }
    }

    /// 제안의 선택지를 등록한다. 다시 띄우지 않는 제안도 부른다 — 앱을 다시 켠 뒤에도
    /// 알림 센터에 남은 알림의 옵션이 살아 있게.
    func register(_ suggestion: Suggestion) {
        var actions = suggestion.choices().enumerated().map { index, preset in
            UNNotificationAction(identifier: NotificationAction.apply(presetID: preset.id).identifier,
                                 title: Korean.choiceTitle(preset.name, suggested: index == 0), options: [])
        }
        actions.append(UNNotificationAction(identifier: NotificationAction.dismissIdentifier, title: "무시", options: []))
        categories[suggestion.id] = UNNotificationCategory(identifier: Self.categoryPrefix + suggestion.id, actions: actions,
                                                           intentIdentifiers: [], options: [])
        center.setNotificationCategories(Set(categories.values))
    }

    /// 알림을 띄운다. completion(true) 는 실제로 올라갔을 때 (메인 스레드)
    func show(_ suggestion: Suggestion, completion: @escaping (Bool) -> Void) {
        register(suggestion)
        let content = UNMutableNotificationContent()
        content.title = suggestion.title
        content.body = suggestion.body
        content.sound = .default
        content.categoryIdentifier = Self.categoryPrefix + suggestion.id
        if let data = try? JSONEncoder().encode(suggestion), let json = String(data: data, encoding: .utf8) {
            content.userInfo = [Self.suggestionKey: json]
        }
        let request = UNNotificationRequest(identifier: suggestion.id, content: content, trigger: nil)
        // 카테고리 등록이 반영되기 전에 올리면 선택지 없이 뜰 수 있어 잠깐 기다린다
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [center] in
            center.add(request) { error in
                if let error { log.error("notification failed: \(error.localizedDescription, privacy: .public)") }
                else { log.info("notification shown \(request.identifier, privacy: .public)") }
                DispatchQueue.main.async { completion(error == nil) }
            }
        }
    }

    /// 결과·오류 알림 (선택지 없음)
    func showMessage(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        center.add(UNNotificationRequest(identifier: "message." + UUID().uuidString, content: content, trigger: nil))
    }

    /// 제안 알림을 화면·알림 센터에서 내린다 (선택지는 남겨 둔다 — 메뉴에서 다시 고를 수 있게)
    func dismissNotifications(ids: [String]) {
        guard !ids.isEmpty else { return }
        center.removeDeliveredNotifications(withIdentifiers: ids)
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    /// 끝난 제안: 알림과 선택지를 모두 정리한다
    func forget(ids: [String]) {
        guard !ids.isEmpty else { return }
        dismissNotifications(ids: ids)
        for id in ids { categories[id] = nil }
        center.setNotificationCategories(Set(categories.values))
    }

    /// 이전 실행에서 남아 있던 알림 가운데 만료된 제안을 지운다
    func removeExpiredDelivered(now: Date) {
        center.getDeliveredNotifications { [center] notifications in
            let expired = notifications.compactMap { notification -> String? in
                guard let suggestion = Self.suggestion(in: notification), suggestion.expiresAt <= now else { return nil }
                return notification.request.identifier
            }
            if !expired.isEmpty { center.removeDeliveredNotifications(withIdentifiers: expired) }
        }
    }

    private static func suggestion(in notification: UNNotification) -> Suggestion? {
        guard let json = notification.request.content.userInfo[suggestionKey] as? String else { return nil }
        return try? JSONDecoder().decode(Suggestion.self, from: Data(json.utf8))
    }

    // MARK: UNUserNotificationCenterDelegate

    /// 앱이 앞에 있어도 배너를 보인다
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        defer { completionHandler() }
        guard let suggestion = Self.suggestion(in: response.notification) else { return }
        let identifier = response.actionIdentifier
        DispatchQueue.main.async {
            if identifier == UNNotificationDefaultActionIdentifier {
                self.onOpen?(suggestion)
            } else if let action = NotificationAction(identifier: identifier) {
                self.onAction?(suggestion, action)
            }
        }
    }
}
