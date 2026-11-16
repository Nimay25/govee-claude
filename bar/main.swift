// Claude Lamp menu bar: a little orange guy shows up when a Claude chat needs you.
// Click -> jumps straight to that chat. Hidden the rest of the time.
//
// Battery: no polling. macOS wakes us only when state.json changes, and the
// animation timer only runs while something is actually waiting.
import Cocoa

let home = FileManager.default.homeDirectoryForCurrentUser.path
let dir = ProcessInfo.processInfo.environment["GOVEE_CLAUDE_DIR"] ?? home + "/.config/govee-claude"
let statePath = dir + "/state.json"
let args = CommandLine.arguments
let script = args.count > 1 ? args[1] : home + "/govee-claude/govee_claude.py"
let python = args.count > 2 ? args[2] : "/opt/homebrew/bin/python3"

struct Waiting {
    let sid: String, title: String, need: String, link: String, app: String
    let since: Double, red: Bool, quiet: Bool
}

// ---------- the little guy ----------

func drawGuy(color: NSColor, spin: CGFloat, bounce: CGFloat, blink: Bool) -> NSImage {
    let img = NSImage(size: NSSize(width: 22, height: 22), flipped: false) { _ in
        let c = NSPoint(x: 11, y: 10.5 + bounce)
        color.setStroke()
        color.setFill()
        // rays
        for i in 0..<8 {
            let a = CGFloat(i) * .pi / 4 + spin
            let ray = NSBezierPath()
            ray.lineWidth = 1.9
            ray.lineCapStyle = .round
            ray.move(to: NSPoint(x: c.x + cos(a) * 6.9, y: c.y + sin(a) * 6.9))
            ray.line(to: NSPoint(x: c.x + cos(a) * 9.6, y: c.y + sin(a) * 9.6))
            ray.stroke()
        }
        // body
        NSBezierPath(ovalIn: NSRect(x: c.x - 5.4, y: c.y - 5.4, width: 10.8, height: 10.8)).fill()
        // eyes
        NSColor(white: 0.08, alpha: 1).setFill()
        let h: CGFloat = blink ? 0.5 : 2.2
        for dx in [-2.1, 2.1] as [CGFloat] {
            NSBezierPath(ovalIn: NSRect(x: c.x + dx - 0.95, y: c.y + 0.3 - h / 2, width: 1.9, height: h)).fill()
        }
        return true
    }
    img.isTemplate = false
    return img
}

func frames(_ color: NSColor) -> [NSImage] {
    (0..<12).map { i in
        let t = CGFloat(i) / 12
        return drawGuy(color: color, spin: t * .pi / 4,
                       bounce: sin(t * 2 * .pi) * 1.1, blink: i == 7)
    }
}

// ---------- app ----------

final class Bar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    var waiting: [Waiting] = []
    var timer: Timer?
    var frame = 0
    var lastKey = ""
    var wiggleUntil = Date.distantPast
    var watcher: DispatchSourceFileSystemObject?
    let gold = frames(NSColor(red: 0.85, green: 0.47, blue: 0.34, alpha: 1)) // Claude orange
    let red = frames(NSColor(red: 1.0, green: 0.23, blue: 0.19, alpha: 1))

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.isVisible = false
        item.button?.target = self
        item.button?.action = #selector(clicked)
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        item.button?.imagePosition = .imageLeft
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        watch()
        reload()
    }

    // Wake only when the folder changes (state.json is replaced atomically).
    func watch() {
        let fd = open(dir, O_EVTONLY)
        guard fd >= 0 else { return }
        let src = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: [.write, .rename, .delete], queue: .main)
        src.setEventHandler { [weak self] in self?.reload() }
        src.setCancelHandler { close(fd) }
        src.resume()
        watcher = src
    }

    func reload() {
        var list: [Waiting] = []
        if let data = FileManager.default.contents(atPath: statePath),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let pending = json["pending"] as? [String: [String: Any]] {
            for (sid, p) in pending {
                list.append(Waiting(sid: sid,
                                    title: p["title"] as? String ?? "Claude",
                                    need: p["need"] as? String ?? "",
                                    link: p["link"] as? String ?? "",
                                    app: p["app"] as? String ?? "",
                                    since: p["since"] as? Double ?? 0,
                                    red: p["red"] as? Bool ?? false,
                                    quiet: p["quiet"] as? Bool ?? false))
            }
        }
        waiting = list.sorted { ($0.red ? 1 : 0, $0.since) > ($1.red ? 1 : 0, $1.since) }
        render()
    }

    func render() {
        guard !waiting.isEmpty else {
            item.isVisible = false
            timer?.invalidate(); timer = nil
            return
        }
        item.isVisible = true
        let text = waiting.count == 1 ? waiting[0].title : "\(waiting.count) chats need you"
        let short = text.count > 26 ? String(text.prefix(25)) + "…" : text
        item.button?.attributedTitle = NSAttributedString(string: " " + short, attributes: [
            .font: NSFont.systemFont(ofSize: 12, weight: .medium)])
        item.button?.toolTip = waiting.map { "\($0.title): \($0.need)" }.joined(separator: "\n")
        // Battery: wiggle for 20s when something new arrives, then sit still.
        // Only an unanswered permission prompt (red) keeps moving.
        let key = waiting.map { "\($0.sid)\($0.red)\($0.since)" }.sorted().joined()
        if key != lastKey { lastKey = key; wiggleUntil = Date().addingTimeInterval(20) }
        let animate = waiting.contains { $0.red } || Date() < wiggleUntil
        if animate && timer == nil {
            let t = Timer(timeInterval: 1.0 / 5, repeats: true) { [weak self] _ in self?.tick() }
            t.tolerance = 0.05
            RunLoop.main.add(t, forMode: .common)
            timer = t
        } else if !animate {
            timer?.invalidate(); timer = nil
        }
        tick()
    }

    func tick() {
        if timer != nil && !waiting.contains(where: { $0.red }) && Date() >= wiggleUntil {
            timer?.invalidate(); timer = nil   // done wiggling: back to zero CPU
        }
        frame += 1
        let set = waiting.contains { $0.red } ? red : gold
        item.button?.image = set[(timer == nil ? 0 : frame) % set.count]
    }

    // One chat waiting -> click goes straight there. More (or right-click) -> pick one.
    @objc func clicked() {
        let right = NSApp.currentEvent?.type == .rightMouseUp
        if waiting.count == 1 && !right {
            go(waiting[0])
            return
        }
        let menu = NSMenu()
        for w in waiting {
            let mi = NSMenuItem(title: w.title, action: #selector(pick(_:)), keyEquivalent: "")
            mi.target = self
            mi.representedObject = w.sid
            let s = NSMutableAttributedString(string: (w.red ? "🔴 " : "🟠 ") + w.title + "\n",
                                              attributes: [.font: NSFont.systemFont(ofSize: 13, weight: .semibold)])
            s.append(NSAttributedString(string: w.need, attributes: [
                .font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor]))
            mi.attributedTitle = s
            menu.addItem(mi)
        }
        menu.addItem(.separator())
        let clear = NSMenuItem(title: "Clear all", action: #selector(clearAll), keyEquivalent: "")
        clear.target = self
        menu.addItem(clear)
        item.menu = menu
        item.button?.performClick(nil)
        item.menu = nil
    }

    @objc func pick(_ sender: NSMenuItem) {
        if let sid = sender.representedObject as? String, let w = waiting.first(where: { $0.sid == sid }) {
            go(w)
        }
    }

    func go(_ w: Waiting) {
        if !w.link.isEmpty, let url = URL(string: w.link) {
            NSWorkspace.shared.open(url)
        } else if !w.app.isEmpty, let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: w.app) {
            NSWorkspace.shared.openApplication(at: app, configuration: .init())
        }
        run(["seen", w.sid])   // you've seen it: lamp goes back to your vibe
    }

    @objc func clearAll() { run(["clear"]) }

    func run(_ a: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script] + a
        try? p.run()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let bar = Bar()
app.delegate = bar
app.run()
