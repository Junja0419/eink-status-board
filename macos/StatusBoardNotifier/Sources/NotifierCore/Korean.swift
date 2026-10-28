import Foundation

public enum Korean {
    /// 서버의 _ro() 와 같은 규칙: 받침이 없거나 ㄹ 받침이면 "로", 있으면 "으로", 한글이 아니면 "(으)로".
    public static func ro(_ word: String) -> String {
        guard let last = word.unicodeScalars.last, (0xAC00...0xD7A3).contains(last.value) else { return "(으)로" }
        let final = (last.value - 0xAC00) % 28
        return final == 0 || final == 8 ? "로" : "으로"
    }

    /// 알림 선택지 이름. 제안된 프리셋은 "'회의 중'으로 바꾸기", 나머지는 이름 그대로.
    public static func choiceTitle(_ name: String, suggested: Bool) -> String {
        suggested ? "'\(name)'\(ro(name)) 바꾸기" : name
    }
}
