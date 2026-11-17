// Claude Lamp menu bar: the Claude logo + one traffic light per Claude agent.
//   yellow = working, green = done (waiting for you), red = needs you (permission)
// Click -> list of agents; pick one to jump straight to that chat.
//
// Battery: no polling. macOS wakes us only when state.json changes. The logo
// spins for ~2s when an agent finishes or needs you, then it's 0% CPU again.
import Cocoa

let home = FileManager.default.homeDirectoryForCurrentUser.path
let dir = ProcessInfo.processInfo.environment["GOVEE_CLAUDE_DIR"] ?? home + "/.config/govee-claude"
let statePath = dir + "/state.json"
let args = CommandLine.arguments
let script = args.count > 1 ? args[1] : home + "/govee-claude/govee_claude.py"
let python = args.count > 2 ? args[2] : "/opt/homebrew/bin/python3"

let forgetAfter: [String: Double] = ["working": 30 * 60, "done": 3 * 3600, "needs": 3 * 3600]
let claudeOrange = NSColor(red: 0.85, green: 0.47, blue: 0.34, alpha: 1)

struct Agent {
    let sid: String, title: String, need: String, link: String, app: String
    let status: String, first: Double, since: Double, last: Double
    var color: NSColor {
        switch status {
        case "done": return NSColor(red: 0.16, green: 0.80, blue: 0.30, alpha: 1)
        case "needs": return NSColor(red: 1.00, green: 0.23, blue: 0.19, alpha: 1)
        default: return NSColor(red: 1.00, green: 0.78, blue: 0.00, alpha: 1)
        }
    }
}

// ---------- drawing ----------

// The real Claude spark, read from the installed Claude app (not copied anywhere), tinted orange.
let logo: NSImage = {
    let size = NSSize(width: 16, height: 16)
    var src: NSImage?
    if let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.anthropic.claudefordesktop") {
        let res = app.appendingPathComponent("Contents/Resources")
        src = NSImage(contentsOf: res.appendingPathComponent("TrayIconTemplate@3x.png"))
            ?? NSImage(contentsOf: res.appendingPathComponent("TrayIconTemplate.png"))
    }
    return NSImage(size: size, flipped: false) { r in
        if let src = src {
            src.draw(in: r)
            claudeOrange.set()
            r.fill(using: .sourceAtop)
        } else {  // fallback: a simple spark
            claudeOrange.setStroke()
            for i in 0..<12 {
                let a = CGFloat(i) * .pi / 6
                let p = NSBezierPath()
                p.lineWidth = 1.6
                p.lineCapStyle = .round
                p.move(to: NSPoint(x: 8 + cos(a) * 1.5, y: 8 + sin(a) * 1.5))
                p.line(to: NSPoint(x: 8 + cos(a) * 7, y: 8 + sin(a) * 7))
                p.stroke()
            }
        }
        return true
    }
}()

func barImage(_ agents: [Agent], spin: CGFloat) -> NSImage {
    let dot: CGFloat = 7, gap: CGFloat = 4, maxDots = 8
    let shown = Array(agents.prefix(maxDots))
    let width = 16 + (shown.isEmpty ? 0 : 6 + CGFloat(shown.count) * dot + CGFloat(shown.count - 1) * gap)
    let img = NSImage(size: NSSize(width: width, height: 18), flipped: false) { _ in
        // logo (rotated while celebrating)
        NSGraphicsContext.current?.saveGraphicsState()
        let t = NSAffineTransform()
        t.translateX(by: 8, yBy: 9)
        t.rotate(byRadians: spin)
        t.translateX(by: -8, yBy: -9)
        t.concat()
        logo.draw(in: NSRect(x: 0, y: 1, width: 16, height: 16))
        NSGraphicsContext.current?.restoreGraphicsState()
        // traffic lights
        var x: CGFloat = 22
        for a in shown {
            a.color.setFill()
            NSBezierPath(ovalIn: NSRect(x: x, y: 9 - dot / 2, width: dot, height: dot)).fill()
            x += dot + gap
        }
        return true
    }
    img.isTemplate = false
    return img
}

func dotImage(_ c: NSColor) -> NSImage {
    let img = NSImage(size: NSSize(width: 10, height: 10), flipped: false) { _ in
        c.setFill()
        NSBezierPath(ovalIn: NSRect(x: 1, y: 1, width: 8, height: 8)).fill()
        return true
    }
    img.isTemplate = false
    return img
}

// ---------- app ----------

final class Bar: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var agents: [Agent] = []
    var all: [Agent] = []
    var lastKey = ""
    var spinTimer: Timer?
    var spinStart = Date.distantPast
    var staleTimer: Timer?
    var watcher: DispatchSourceFileSystemObject?

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.isVisible = false
        item.button?.target = self
        item.button?.action = #selector(clicked)
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
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
        var list: [Agent] = []
        if let data = FileManager.default.contents(atPath: statePath),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let raw = json["agents"] as? [String: [String: Any]] {
            for (sid, a) in raw {
                list.append(Agent(sid: sid,
                                  title: a["title"] as? String ?? "Claude",
                                  need: a["need"] as? String ?? "",
                                  link: a["link"] as? String ?? "",
                                  app: a["app"] as? String ?? "",
                                  status: a["status"] as? String ?? "working",
                                  first: a["first"] as? Double ?? 0,
                                  since: a["since"] as? Double ?? 0,
                                  last: a["last"] as? Double ?? 0))
            }
        }
        all = list.sorted { $0.first < $1.first }   // stable order, like tabs
        refresh()
    }

    func refresh() {
        let now = Date().timeIntervalSince1970
        agents = all.filter { now - $0.last < (forgetAfter[$0.status] ?? 3600) }
        guard !agents.isEmpty else {
            item.isVisible = false
            staleTimer?.invalidate(); staleTimer = nil
            return
        }
        item.isVisible = true
        // Little celebration when something finishes or needs you.
        let key = agents.filter { $0.status != "working" }.map { "\($0.sid)\($0.status)\($0.since)" }.sorted().joined()
        if key != lastKey {
            if agents.contains(where: { $0.status != "working" && now - $0.since < 10 }) { spin() }
            lastKey = key
        }
        let extra = all.count > 8 ? " +\(agents.count - 8)" : ""
        item.button?.title = extra
        item.button?.imagePosition = .imageLeft
        draw()
        item.button?.toolTip = agents.map { "\(label($0.status)) · \($0.title)" }.joined(separator: "\n")
        // Re-check once a minute only so stale agents drop off (very cheap).
        if staleTimer == nil {
            let t = Timer(timeInterval: 60, repeats: true) { [weak self] _ in self?.refresh() }
            t.tolerance = 30
            RunLoop.main.add(t, forMode: .common)
            staleTimer = t
        }
    }

    // "Done 4m ago · <what it said>" (the menu is rebuilt on every click, so it's always current)
    func detailLine(_ a: Agent) -> String {
        let need = a.need.trimmingCharacters(in: .whitespaces)
        let head = "\(label(a.status)) \(ago(a.since))"
        return need.isEmpty || need == "Done" || need == label(a.status) ? head : "\(head) · \(need)"
    }

    func span(_ t: Double) -> String {   // "", "4m", "2h", "1h 5m"
        let m = Int(max(0, Date().timeIntervalSince1970 - t) / 60)
        if m < 1 { return "" }
        if m < 60 { return "\(m)m" }
        return m % 60 == 0 ? "\(m / 60)h" : "\(m / 60)h \(m % 60)m"
    }

    func ago(_ t: Double) -> String {
        let s = span(t)
        return s.isEmpty ? "just now" : s + " ago"
    }

    func label(_ s: String) -> String {
        s == "done" ? "Done" : s == "needs" ? "Needs you" : "Working"
    }

    func spin() {
        spinStart = Date()
        guard spinTimer == nil else { return }
        let t = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.draw() }
        RunLoop.main.add(t, forMode: .common)
        spinTimer = t
    }

    func draw() {
        let elapsed = Date().timeIntervalSince(spinStart)
        let dur = 1.6
        var angle: CGFloat = 0
        if elapsed < dur {
            let p = elapsed / dur
            let eased = 1 - pow(1 - p, 3)             // fast start, soft landing
            angle = -CGFloat(eased) * .pi            // half turn: the spark looks the same, smooth stop
        } else {
            spinTimer?.invalidate(); spinTimer = nil  // back to 0% CPU
        }
        item.button?.image = barImage(agents, spin: angle)
    }

    @objc func clicked() {
        let menu = NSMenu()
        for a in agents {
            let mi = NSMenuItem(title: a.title, action: #selector(pick(_:)), keyEquivalent: "")
            mi.target = self
            mi.representedObject = a.sid
            mi.image = dotImage(a.color)
            let s = NSMutableAttributedString(string: a.title + "\n",
                                              attributes: [.font: NSFont.systemFont(ofSize: 13, weight: .semibold)])
            let detail = a.status == "working" ? (span(a.since).isEmpty ? "Working…" : "Working for \(span(a.since))") : detailLine(a)
            s.append(NSAttributedString(string: detail, attributes: [
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
        guard let sid = sender.representedObject as? String,
              let a = agents.first(where: { $0.sid == sid }) else { return }
        if !a.link.isEmpty, let url = URL(string: a.link) {
            NSWorkspace.shared.open(url)
        } else if !a.app.isEmpty, let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: a.app) {
            NSWorkspace.shared.openApplication(at: app, configuration: .init())
        }
    }

    @objc func clearAll() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script, "clear"]
        try? p.run()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let bar = Bar()
app.delegate = bar
app.run()
