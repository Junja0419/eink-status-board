import Foundation

/// 알림에서 고른 프리셋을 서버에 적용한다 (POST /api/suggestions/apply, APP_KEY).
public enum ApplyClient {
    public static func request(endpoint: Endpoint, key: String, presetID: String, until: String?) -> URLRequest {
        var request = URLRequest(url: endpoint.applyURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue(key, forHTTPHeaderField: "X-API-Key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["preset_id": presetID, "until": until.map { $0 as Any } ?? NSNull()]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        return request
    }

    /// completion 은 메인 스레드에서 부른다
    public static func apply(endpoint: Endpoint, key: String, presetID: String, until: String?,
                             session: URLSession = .shared, completion: @escaping (ApplyOutcome) -> Void) {
        let request = request(endpoint: endpoint, key: key, presetID: presetID, until: until)
        session.dataTask(with: request) { data, response, error in
            let outcome: ApplyOutcome
            if let error {
                outcome = .failed("서버에 연결하지 못했습니다. (\(error.localizedDescription))")
            } else {
                outcome = .from(status: (response as? HTTPURLResponse)?.statusCode ?? 0, body: data ?? Data())
            }
            DispatchQueue.main.async { completion(outcome) }
        }.resume()
    }
}
