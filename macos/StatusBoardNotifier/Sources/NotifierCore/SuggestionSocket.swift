import Foundation

/// /ws/app 연결. 끊기면 Backoff 간격으로 다시 붙고, 25초마다 ping 을 보내 죽은 연결을 찾아낸다.
/// 모든 콜백은 메인 스레드에서 부른다.
public final class SuggestionSocket {
    public enum State: Equatable {
        case notConfigured
        case connecting
        case connected
        /// 핸드셰이크가 403 — 키가 틀렸거나 서버의 앱 연결 수가 가득 찼다
        case refused
        case waiting(TimeInterval)
    }

    public var onState: ((State) -> Void)?
    public var onSuggestion: ((Suggestion) -> Void)?

    public init() {}

    private static let pingInterval: TimeInterval = 25
    private static let pongTimeout: TimeInterval = 10
    private static let refusedRetry: TimeInterval = 60
    /// 업그레이드 뒤 이 안에 hello 가 없으면 죽은 연결로 본다
    private static let helloTimeout: TimeInterval = 15
    /// 이만큼 붙어 있어야 재연결 간격을 처음으로 되돌린다 (붙자마자 끊기는 서버에 1초 간격으로 매달리지 않게)
    private static let stableAfter: TimeInterval = 30

    private let session = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var generation = 0          // 지난 연결의 늦은 콜백을 무시하기 위한 번호
    private var backoff = Backoff()
    private var retryTimer: Timer?
    private var pingTimer: Timer?
    private var pongTimer: Timer?
    private var helloTimer: Timer?
    private var stableTimer: Timer?
    private var endpoint: Endpoint?
    private var key: String?

    public func configure(endpoint: Endpoint?, key: String?) {
        self.endpoint = endpoint
        self.key = key
        reconnectNow()
    }

    /// 잠자기에서 깼거나 사용자가 "지금 다시 연결" 을 눌렀을 때
    public func reconnectNow() {
        backoff.reset()
        connect()
    }

    /// 연결을 끊고 다시 붙지 않는다
    public func stop() {
        teardown()
    }

    private func connect() {
        teardown()
        guard let endpoint, let key, !key.isEmpty else {
            onState?(.notConfigured)
            return
        }
        var request = URLRequest(url: endpoint.socketURL)
        request.setValue(key, forHTTPHeaderField: "X-API-Key")
        request.timeoutInterval = 15
        let task = session.webSocketTask(with: request)
        self.task = task
        let current = generation
        onState?(.connecting)
        task.resume()
        receive(task, current)
        helloTimer = Timer.scheduledTimer(withTimeInterval: Self.helloTimeout, repeats: false) { [weak self] _ in
            guard let self, current == self.generation else { return }
            self.failed(task)
        }
    }

    private func receive(_ task: URLSessionWebSocketTask, _ current: Int) {
        task.receive { [weak self] result in
            DispatchQueue.main.async {
                guard let self, current == self.generation else { return }
                switch result {
                case .success(.string(let text)):
                    self.handle(text)
                    self.receive(task, current)
                case .success:
                    self.receive(task, current)
                case .failure:
                    self.failed(task)
                }
            }
        }
    }

    private func handle(_ text: String) {
        switch MessageDecoder.decode(text) {
        case .hello:
            helloTimer?.invalidate()
            onState?(.connected)
            startPinging()
            stableTimer = Timer.scheduledTimer(withTimeInterval: Self.stableAfter, repeats: false) { [weak self] _ in
                self?.backoff.reset()
            }
        case .suggestion(let suggestion):
            onSuggestion?(suggestion)
        case .ignored:
            break
        }
    }

    private func failed(_ task: URLSessionWebSocketTask) {
        let refused = (task.response as? HTTPURLResponse)?.statusCode == 403
        teardown()
        let delay = refused ? Self.refusedRetry : backoff.next()
        onState?(refused ? .refused : .waiting(delay))
        retryTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            self?.connect()
        }
    }

    private func startPinging() {
        pingTimer?.invalidate()
        let current = generation
        pingTimer = Timer.scheduledTimer(withTimeInterval: Self.pingInterval, repeats: true) { [weak self] _ in
            guard let self, current == self.generation, let task = self.task else { return }
            self.pongTimer?.invalidate()
            self.pongTimer = Timer.scheduledTimer(withTimeInterval: Self.pongTimeout, repeats: false) { [weak self] _ in
                guard let self, current == self.generation else { return }
                self.failed(task)       // 답이 없으면 연결이 죽은 것으로 본다
            }
            task.sendPing { [weak self] error in
                DispatchQueue.main.async {
                    guard let self, current == self.generation else { return }
                    self.pongTimer?.invalidate()
                    if error != nil { self.failed(task) }
                }
            }
        }
    }

    private func teardown() {
        generation += 1
        retryTimer?.invalidate()
        pingTimer?.invalidate()
        pongTimer?.invalidate()
        helloTimer?.invalidate()
        stableTimer?.invalidate()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }
}
