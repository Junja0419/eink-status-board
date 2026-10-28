import Foundation

/// 설정에 넣은 서버 주소에서 앱이 쓰는 두 주소를 만든다.
public struct Endpoint: Equatable {
    public let base: URL

    public enum Problem: Error, Equatable {
        case empty
        case invalid
        /// 키가 평문으로 오가므로 http 는 이 Mac 안(localhost)만 허용한다
        case insecure
    }

    /// "https://host", "https://host/", "host" 모두 받는다. 경로·쿼리는 버린다.
    public static func parse(_ raw: String) -> Result<Endpoint, Problem> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return .failure(.empty) }
        let withScheme = trimmed.contains("://") ? trimmed : "https://" + trimmed
        guard var parts = URLComponents(string: withScheme),
              let scheme = parts.scheme?.lowercased(),
              let host = parts.host, !host.isEmpty,
              scheme == "https" || scheme == "http"
        else { return .failure(.invalid) }
        if scheme == "http" && !["localhost", "127.0.0.1", "::1"].contains(host.lowercased()) {
            return .failure(.insecure)
        }
        parts.scheme = scheme
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        parts.user = nil
        parts.password = nil
        guard let url = parts.url else { return .failure(.invalid) }
        return .success(Endpoint(base: url))
    }

    public var socketURL: URL {
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme == "https" ? "wss" : "ws"
        parts.path = "/ws/app"
        return parts.url!
    }

    public var applyURL: URL {
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.path = "/api/suggestions/apply"
        return parts.url!
    }
}
