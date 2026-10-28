import XCTest
@testable import NotifierCore

/// 실제 서버에 붙는 통합 테스트. 환경 변수가 없으면 건너뛴다.
///   STATUSBOARD_E2E_BASE=http://127.0.0.1:5099 STATUSBOARD_E2E_APP_KEY=… STATUSBOARD_E2E_SUGGEST_KEY=… swift test
/// 서버에는 '회의 중' 을 포함해 프리셋이 둘 이상 있어야 한다.
final class LiveServerTests: XCTestCase {
    private var endpoint: Endpoint!
    private var appKey = ""
    private var suggestKey = ""

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        guard let base = env["STATUSBOARD_E2E_BASE"], let app = env["STATUSBOARD_E2E_APP_KEY"],
              let suggest = env["STATUSBOARD_E2E_SUGGEST_KEY"] else {
            throw XCTSkip("STATUSBOARD_E2E_* 가 없어 실제 서버 테스트를 건너뜁니다")
        }
        guard case .success(let parsed) = Endpoint.parse(base) else { throw XCTSkip("bad STATUSBOARD_E2E_BASE") }
        endpoint = parsed
        appKey = app
        suggestKey = suggest
    }

    private func notify(subject: String) -> XCTestExpectation {
        let done = expectation(description: "notify 200")
        var request = URLRequest(url: endpoint.base.appendingPathComponent("api/suggestions/notify"))
        request.httpMethod = "POST"
        request.setValue(suggestKey, forHTTPHeaderField: "X-API-Key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let until = ISO8601DateFormatter().string(from: Date().addingTimeInterval(3600))
        let start = ISO8601DateFormatter().string(from: Date().addingTimeInterval(300))
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["name": "회의 중", "subject": subject, "start": start, "until": until])
        URLSession.shared.dataTask(with: request) { _, response, _ in
            XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
            done.fulfill()
        }.resume()
        return done
    }

    func testReceiveSuggestionAndApplyAnotherPreset() {
        let socket = SuggestionSocket()
        defer { socket.stop() }
        let connected = expectation(description: "hello")
        connected.assertForOverFulfill = false
        let got = expectation(description: "suggestion")
        let subject = "E2E \(UUID().uuidString.prefix(8))"
        var received: Suggestion?
        socket.onState = { if $0 == .connected { connected.fulfill() } }
        socket.onSuggestion = { suggestion in
            if suggestion.title.hasSuffix(subject) {
                received = suggestion
                got.fulfill()
            }
        }
        socket.configure(endpoint: endpoint, key: appKey)
        wait(for: [connected], timeout: 10)
        wait(for: [notify(subject: subject), got], timeout: 10)

        guard let suggestion = received else { return XCTFail("no suggestion") }
        XCTAssertEqual(suggestion.presetName, "회의 중")
        XCTAssertEqual(suggestion.body, "'회의 중'으로 바꿀까요?")
        XCTAssertNotNil(suggestion.until)
        XCTAssertGreaterThan(suggestion.choices().count, 1)

        let other = suggestion.choices()[1]
        let applied = expectation(description: "applied")
        ApplyClient.apply(endpoint: endpoint, key: appKey, presetID: other.id, until: suggestion.until) { outcome in
            guard case .applied(let message) = outcome else { return XCTFail("\(outcome)") }
            // 서버가 이미 그 프리셋이면 "이미 '…' 상태입니다." — 어느 쪽이든 고른 프리셋을 말한다
            XCTAssertTrue(message.contains("'\(other.name)'"), message)
            XCTAssertTrue(message.contains("바꿨습니다") || message.contains("상태"), message)
            applied.fulfill()
        }
        wait(for: [applied], timeout: 10)
    }

    func testWrongKeyIsRefused() {
        let socket = SuggestionSocket()
        defer { socket.stop() }
        let refused = expectation(description: "refused")
        refused.assertForOverFulfill = false
        socket.onState = { if $0 == .refused { refused.fulfill() } }
        socket.configure(endpoint: endpoint, key: String(repeating: "x", count: 40))
        wait(for: [refused], timeout: 10)

        let failed = expectation(description: "apply refused")
        ApplyClient.apply(endpoint: endpoint, key: suggestKey, presetID: "aaaa1111", until: nil) { outcome in
            guard case .failed(let message) = outcome else { return XCTFail("\(outcome)") }
            XCTAssertTrue(message.contains("APP_KEY"), message)
            failed.fulfill()
        }
        wait(for: [failed], timeout: 10)
    }
}
