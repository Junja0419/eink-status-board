// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "StatusBoardNotifier",
    platforms: [.macOS(.v13)],
    targets: [
        // 서버 메시지 해석·주소·조사·재연결 간격처럼 UI 없이 검증할 수 있는 로직
        .target(name: "NotifierCore"),
        // 메뉴 막대 앱 (AppKit + UserNotifications)
        .executableTarget(name: "StatusBoardNotifier", dependencies: ["NotifierCore"]),
        .testTarget(name: "NotifierCoreTests", dependencies: ["NotifierCore"]),
    ]
)
