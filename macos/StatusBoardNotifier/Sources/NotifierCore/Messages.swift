import Foundation

/// 제안에 딸려 오는 프리셋 목록의 한 항목 (Admin 순서).
public struct PresetRef: Codable, Equatable, Sendable {
    public let id: String
    public let name: String

    public init(id: String, name: String) {
        self.id = id
        self.name = name
    }
}

/// 서버가 /ws/app 으로 보내는 일정 제안. 문장(title·body)은 서버가 만든다.
public struct Suggestion: Codable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let body: String
    public let presetID: String
    public let presetName: String
    /// 적용할 때 그대로 돌려보내는 일정 종료 시각 (없으면 복귀 예약 없이 적용)
    public let until: String?
    public let expiresAt: Date
    public let presets: [PresetRef]

    enum CodingKeys: String, CodingKey {
        case id, title, body, until, presets
        case presetID = "preset_id"
        case presetName = "preset_name"
        case expiresAt = "expires_at"
    }

    public init(id: String, title: String, body: String, presetID: String, presetName: String,
                until: String?, expiresAt: Date, presets: [PresetRef]) {
        self.id = id
        self.title = title
        self.body = body
        self.presetID = presetID
        self.presetName = presetName
        self.until = until
        self.expiresAt = expiresAt
        self.presets = presets
    }

    /// 알림 선택지 순서: 제안된 프리셋 → 나머지 프리셋(Admin 순서, 최대 limit 개).
    /// 기본 8 — 제안 프리셋과 "무시" 를 더해 알림 동작이 10개를 넘지 않게 한다.
    public func choices(limit: Int = 8) -> [PresetRef] {
        let suggested = PresetRef(id: presetID, name: presetName)
        let others = presets.filter { $0.id != presetID }.prefix(limit)
        return [suggested] + others
    }
}

public enum ServerMessage: Equatable {
    case hello
    case suggestion(Suggestion)
    /// 모르는 type 이나 깨진 메시지 — 연결은 유지하고 무시한다
    case ignored
}

public enum MessageDecoder {
    public static func decode(_ text: String) -> ServerMessage {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String
        else { return .ignored }
        switch type {
        case "hello":
            return .hello
        case "suggestion":
            guard let suggestion = try? decoder.decode(Suggestion.self, from: data) else { return .ignored }
            return .suggestion(suggestion)
        default:
            return .ignored
        }
    }

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { source in
            let container = try source.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = ISOTime.parse(text) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "not ISO 8601: \(text)")
            }
            return date
        }
        return decoder
    }()
}

public enum ISOTime {
    /// "2026-10-29T05:00:00+00:00", "…Z", 소수 초가 붙은 형식을 모두 읽는다.
    public static func parse(_ text: String) -> Date? {
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let date = plain.date(from: text) { return date }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: text)
    }
}
