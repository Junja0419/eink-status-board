import Foundation

/// 알림 동작 식별자. "apply:<프리셋 id>" 또는 "dismiss".
public enum NotificationAction: Equatable {
    case apply(presetID: String)
    case dismiss

    public static let dismissIdentifier = "dismiss"
    static let applyPrefix = "apply:"

    public var identifier: String {
        switch self {
        case .apply(let id): return Self.applyPrefix + id
        case .dismiss: return Self.dismissIdentifier
        }
    }

    public init?(identifier: String) {
        if identifier == Self.dismissIdentifier {
            self = .dismiss
        } else if identifier.hasPrefix(Self.applyPrefix), identifier.count > Self.applyPrefix.count {
            self = .apply(presetID: String(identifier.dropFirst(Self.applyPrefix.count)))
        } else {
            return nil
        }
    }
}

/// 재연결 대기 시간. 실패할수록 길어지고, 연결되면 처음으로 돌아간다.
public struct Backoff {
    public static let steps: [TimeInterval] = [1, 2, 5, 10, 30, 60]
    public private(set) var failures = 0

    public init() {}

    public mutating func next() -> TimeInterval {
        defer { failures += 1 }
        return Self.steps[min(failures, Self.steps.count - 1)]
    }

    public mutating func reset() {
        failures = 0
    }
}

/// 이미 알림을 띄운 제안·처리한 제안의 id. 서버는 앱이 다시 접속할 때마다 유효한 제안을 다시 보내므로
/// 같은 제안을 두 번 알리지 않게 한다. 만료가 지난 id 는 지운다. (저장은 호출하는 쪽이 한다)
public struct SeenIDs: Equatable {
    public private(set) var expiries: [String: Date]

    public init(expiries: [String: Date] = [:]) {
        self.expiries = expiries
    }

    public func contains(_ id: String) -> Bool {
        expiries[id] != nil
    }

    public mutating func insert(_ id: String, expiresAt: Date) {
        expiries[id] = expiresAt
    }

    public mutating func prune(now: Date) {
        expiries = expiries.filter { $0.value > now }
    }

    /// UserDefaults 에 넣을 수 있는 형태 (id → 만료 시각의 Unix 초)
    public var plist: [String: Double] {
        expiries.mapValues { $0.timeIntervalSince1970 }
    }

    public init(plist: Any?) {
        let raw = plist as? [String: Double] ?? [:]
        expiries = raw.mapValues { Date(timeIntervalSince1970: $0) }
    }
}

/// /api/suggestions/apply 응답을 사용자에게 보일 문장으로.
public enum ApplyOutcome: Equatable {
    case applied(String)
    case failed(String)

    public static func from(status: Int, body: Data) -> ApplyOutcome {
        let object = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        if (200..<300).contains(status) {
            return .applied(object?["message"] as? String ?? "바꿨습니다.")
        }
        if status == 401 {
            return .failed("키가 맞지 않습니다. 메뉴의 설정에서 APP_KEY 를 확인하세요.")
        }
        if let detail = object?["detail"] as? String {
            return .failed(detail)
        }
        return .failed("서버 오류 (\(status))")
    }
}
