import AppKit

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // Dock 아이콘 없이 메뉴 막대에만 (Info.plist 의 LSUIElement 와 같은 뜻)
app.run()
