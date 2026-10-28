import XCTest
@testable import NotifierCore

final class MessageTests: XCTestCase {
    let sample = """
    {"type":"suggestion","id":"0123456789abcdef","title":"10:00 주간회의","body":"'회의 중'으로 바꿀까요?",
     "preset_id":"bbbb2222","preset_name":"회의 중","until":"2026-10-29T02:00:00+00:00",
     "presets":[{"id":"aaaa1111","name":"근무 중"},{"id":"bbbb2222","name":"회의 중"},{"id":"cccc3333","name":"외근 중"}],
     "expires_at":"2026-10-29T02:00:00+00:00"}
    """

    func testHello() {
        XCTAssertEqual(MessageDecoder.decode(#"{"type":"hello"}"#), .hello)
    }

    func testSuggestion() throws {
        guard case .suggestion(let s) = MessageDecoder.decode(sample) else { return XCTFail("not decoded") }
        XCTAssertEqual(s.id, "0123456789abcdef")
        XCTAssertEqual(s.title, "10:00 주간회의")
        XCTAssertEqual(s.presetID, "bbbb2222")
        XCTAssertEqual(s.until, "2026-10-29T02:00:00+00:00")
        XCTAssertEqual(s.expiresAt, ISOTime.parse("2026-10-29T11:00:00+09:00"))
        XCTAssertEqual(s.presets.count, 3)
    }

    func testSuggestionWithoutUntil() {
        let text = sample.replacingOccurrences(of: #""until":"2026-10-29T02:00:00+00:00""#, with: #""until":null"#)
        guard case .suggestion(let s) = MessageDecoder.decode(text) else { return XCTFail("not decoded") }
        XCTAssertNil(s.until)
    }

    func testIgnoredMessages() {
        for text in ["", "hello", "[]", "{}", #"{"type":"other"}"#, #"{"type":"suggestion","id":"x"}"#,
                     sample.replacingOccurrences(of: "2026-10-29T02:00:00+00:00\"}", with: "soon\"}")] {
            XCTAssertEqual(MessageDecoder.decode(text), .ignored, text)
        }
    }

    func testISOTimeFormats() {
        let expected = Date(timeIntervalSince1970: 1_793_239_200)   // 2026-10-29T02:00:00Z
        XCTAssertEqual(ISOTime.parse("2026-10-29T02:00:00Z"), expected)
        XCTAssertEqual(ISOTime.parse("2026-10-29T11:00:00+09:00"), expected)
        XCTAssertEqual(ISOTime.parse("2026-10-29T02:00:00.123+00:00")?.timeIntervalSince1970 ?? 0,
                       expected.timeIntervalSince1970 + 0.123, accuracy: 0.001)
        XCTAssertNil(ISOTime.parse("soon"))
    }

    func testChoicesPutSuggestedFirstAndCap() {
        guard case .suggestion(let s) = MessageDecoder.decode(sample) else { return XCTFail("not decoded") }
        XCTAssertEqual(s.choices().map(\.id), ["bbbb2222", "aaaa1111", "cccc3333"])
        XCTAssertEqual(s.choices(limit: 1).map(\.id), ["bbbb2222", "aaaa1111"])
    }
}

final class EndpointTests: XCTestCase {
    func url(_ raw: String) -> String? {
        if case .success(let e) = Endpoint.parse(raw) { return e.base.absoluteString }
        return nil
    }

    func testNormalisation() {
        XCTAssertEqual(url("https://board.example.org"), "https://board.example.org")
        XCTAssertEqual(url("  https://board.example.org/  "), "https://board.example.org")
        XCTAssertEqual(url("board.example.org"), "https://board.example.org")
        XCTAssertEqual(url("HTTPS://board.example.org/admin?x=1#y"), "https://board.example.org")
        XCTAssertEqual(url("http://127.0.0.1:5000"), "http://127.0.0.1:5000")
        XCTAssertEqual(url("http://localhost:5000/"), "http://localhost:5000")
    }

    func testRejected() {
        XCTAssertEqual(Endpoint.parse(""), .failure(.empty))
        XCTAssertEqual(Endpoint.parse("http://board.example.org"), .failure(.insecure))
        XCTAssertEqual(Endpoint.parse("ftp://board.example.org"), .failure(.invalid))
        XCTAssertEqual(Endpoint.parse("https://"), .failure(.invalid))
    }

    func testDerivedURLs() throws {
        guard case .success(let e) = Endpoint.parse("https://board.example.org:8443/x") else { return XCTFail() }
        XCTAssertEqual(e.socketURL.absoluteString, "wss://board.example.org:8443/ws/app")
        XCTAssertEqual(e.applyURL.absoluteString, "https://board.example.org:8443/api/suggestions/apply")
        guard case .success(let local) = Endpoint.parse("http://127.0.0.1:5000") else { return XCTFail() }
        XCTAssertEqual(local.socketURL.absoluteString, "ws://127.0.0.1:5000/ws/app")
    }
}

final class KoreanTests: XCTestCase {
    func testParticle() {
        XCTAssertEqual(Korean.ro("회의 중"), "으로")
        XCTAssertEqual(Korean.ro("휴가"), "로")
        XCTAssertEqual(Korean.ro("서울"), "로")       // ㄹ 받침
        XCTAssertEqual(Korean.ro("점심 식사"), "로")
        XCTAssertEqual(Korean.ro("Busy"), "(으)로")
        XCTAssertEqual(Korean.ro(""), "(으)로")
    }

    func testChoiceTitle() {
        XCTAssertEqual(Korean.choiceTitle("회의 중", suggested: true), "'회의 중'으로 바꾸기")
        XCTAssertEqual(Korean.choiceTitle("근무 중", suggested: false), "근무 중")
    }
}

final class SupportTests: XCTestCase {
    func testActionRoundTrip() {
        XCTAssertEqual(NotificationAction(identifier: NotificationAction.apply(presetID: "bbbb2222").identifier), .apply(presetID: "bbbb2222"))
        XCTAssertEqual(NotificationAction(identifier: "dismiss"), .dismiss)
        XCTAssertNil(NotificationAction(identifier: "apply:"))
        XCTAssertNil(NotificationAction(identifier: "com.apple.UNNotificationDefaultActionIdentifier"))
    }

    func testBackoff() {
        var b = Backoff()
        XCTAssertEqual((0..<8).map { _ in b.next() }, [1, 2, 5, 10, 30, 60, 60, 60])
        b.reset()
        XCTAssertEqual(b.next(), 1)
    }

    func testSeenIDs() {
        let now = Date()
        var seen = SeenIDs()
        seen.insert("old", expiresAt: now.addingTimeInterval(-1))
        seen.insert("live", expiresAt: now.addingTimeInterval(600))
        XCTAssertTrue(seen.contains("old"))
        seen.prune(now: now)
        XCTAssertFalse(seen.contains("old"))
        XCTAssertTrue(seen.contains("live"))
        let restored = SeenIDs(plist: seen.plist)
        XCTAssertTrue(restored.contains("live"))
        XCTAssertEqual(SeenIDs(plist: "junk").expiries, [:])
    }

    func testApplyOutcome() {
        let ok = #"{"message":"'회의 중'으로 바꿨습니다."}"#.data(using: .utf8)!
        XCTAssertEqual(ApplyOutcome.from(status: 200, body: ok), .applied("'회의 중'으로 바꿨습니다."))
        XCTAssertEqual(ApplyOutcome.from(status: 200, body: Data()), .applied("바꿨습니다."))
        let ended = #"{"detail":"이미 끝난 일정입니다."}"#.data(using: .utf8)!
        XCTAssertEqual(ApplyOutcome.from(status: 409, body: ended), .failed("이미 끝난 일정입니다."))
        guard case .failed(let unauthorized) = ApplyOutcome.from(status: 401, body: Data()) else { return XCTFail() }
        XCTAssertTrue(unauthorized.contains("APP_KEY"))
        XCTAssertEqual(ApplyOutcome.from(status: 502, body: Data("<html>".utf8)), .failed("서버 오류 (502)"))
    }
}
