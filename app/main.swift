// WAV Joiner - window front end for wav_joiner.py.
// Drop folders or WAV files on the window (or the Dock icon); split takes are
// joined next to the originals and a CSV report is written to each folder.
import AppKit

let moveKey = "MoveSourcesIntoSubfolder"

final class DropView: NSView {
    var onDrop: (([URL]) -> Void)?
    var enabled = true { didSet { needsDisplay = true } }
    private var highlighted = false { didSet { needsDisplay = true } }
    private let label = NSTextField(labelWithString: "")

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
        label.alignment = .center
        label.font = NSFont.systemFont(ofSize: 15, weight: .medium)
        label.textColor = .secondaryLabelColor
        label.translatesAutoresizingMaskIntoConstraints = false
        addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: centerXAnchor),
            label.centerYAnchor.constraint(equalTo: centerYAnchor),
            label.leadingAnchor.constraint(greaterThanOrEqualTo: leadingAnchor, constant: 12),
        ])
        setText("Drop folders or WAV files here")
    }
    required init?(coder: NSCoder) { fatalError() }

    func setText(_ s: String) { label.stringValue = s }

    override func draw(_ dirtyRect: NSRect) {
        let rect = bounds.insetBy(dx: 2, dy: 2)
        let path = NSBezierPath(roundedRect: rect, xRadius: 12, yRadius: 12)
        (highlighted ? NSColor.controlAccentColor.withAlphaComponent(0.12)
                     : NSColor.quaternaryLabelColor.withAlphaComponent(0.06)).setFill()
        path.fill()
        path.lineWidth = 2
        path.setLineDash([8, 5], count: 2, phase: 0)
        (highlighted ? NSColor.controlAccentColor : NSColor.tertiaryLabelColor).setStroke()
        path.stroke()
    }

    private func urls(_ info: NSDraggingInfo) -> [URL] {
        let opts: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        return info.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: opts) as? [URL] ?? []
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard enabled, !urls(sender).isEmpty else { return [] }
        highlighted = true
        return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { highlighted = false }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        highlighted = false
        let u = urls(sender)
        guard enabled, !u.isEmpty else { return false }
        onDrop?(u)
        return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let drop = DropView(frame: .zero)
    let spinner = NSProgressIndicator()
    let moveBox = NSButton(checkboxWithTitle: "Move joined source files into \u{201C}merged_source_files\u{201D}",
                           target: nil, action: nil)
    let chooseButton = NSButton(title: "Choose Folder\u{2026}", target: nil, action: nil)
    let reportButton = NSButton(title: "Open Report", target: nil, action: nil)
    let output = NSTextView()
    var reports: [URL] = []
    var running = false
    var pending: [URL] = []

    func applicationDidFinishLaunching(_ note: Notification) {
        UserDefaults.standard.register(defaults: [moveKey: true])
        buildMenu()
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)
        if !pending.isEmpty { run(pending); pending = [] }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // Files/folders dropped on the Dock or app icon.
    func application(_ application: NSApplication, open urls: [URL]) {
        if window == nil { pending += urls } else { run(urls) }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit WAV Joiner", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let fileItem = NSMenuItem()
        main.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "Choose Folder\u{2026}", action: #selector(choose), keyEquivalent: "o")
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        NSApp.mainMenu = main
    }

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 460),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "WAV Joiner"
        window.minSize = NSSize(width: 420, height: 380)
        window.center()
        window.setFrameAutosaveName("WAVJoinerMain")

        drop.onDrop = { [weak self] urls in self?.run(urls) }

        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false

        moveBox.target = self
        moveBox.action = #selector(toggleMove)
        moveBox.state = UserDefaults.standard.bool(forKey: moveKey) ? .on : .off

        chooseButton.target = self
        chooseButton.action = #selector(choose)
        chooseButton.bezelStyle = .rounded
        reportButton.target = self
        reportButton.action = #selector(openReport)
        reportButton.bezelStyle = .rounded
        reportButton.isEnabled = false

        output.isEditable = false
        output.isRichText = false
        output.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        output.textContainerInset = NSSize(width: 6, height: 6)
        output.string = "Split takes are joined into \u{2026}_joined.wav next to the originals. A CSV report is written to each folder."
        output.textColor = .secondaryLabelColor
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = output
        output.autoresizingMask = [.width]
        output.isVerticallyResizable = true
        output.textContainer?.widthTracksTextView = true

        let buttons = NSStackView(views: [chooseButton, spinner, NSView(), reportButton])
        buttons.orientation = .horizontal
        buttons.spacing = 8

        let stack = NSStackView(views: [drop, moveBox, buttons, scroll])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = NSView()
        window.contentView!.addSubview(stack)
        for v in [drop, buttons, scroll] as [NSView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            v.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -32).isActive = true
        }
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            stack.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
            drop.heightAnchor.constraint(equalToConstant: 160),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 120),
        ])
        window.makeKeyAndOrderFront(nil)
    }

    @objc func toggleMove() {
        UserDefaults.standard.set(moveBox.state == .on, forKey: moveKey)
    }

    @objc func choose() {
        guard !running else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.prompt = "Join"
        panel.message = "Choose folders (or WAV files) to scan for split takes"
        panel.beginSheetModal(for: window) { [weak self] resp in
            if resp == .OK { self?.run(panel.urls) }
        }
    }

    @objc func openReport() {
        for r in reports { NSWorkspace.shared.open(r) }
    }

    func setRunning(_ on: Bool) {
        running = on
        drop.enabled = !on
        chooseButton.isEnabled = !on
        moveBox.isEnabled = !on
        reportButton.isEnabled = !on && !reports.isEmpty
        drop.setText(on ? "Joining\u{2026}" : "Drop folders or WAV files here")
        if on { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
    }

    func run(_ urls: [URL]) {
        guard !running, !urls.isEmpty else { return }
        guard let script = Bundle.main.path(forResource: "wav_joiner", ofType: "py") else {
            show("wav_joiner.py is missing from the app bundle. Rebuild the app.", error: true)
            return
        }
        reports = []
        setRunning(true)
        show("Scanning \(urls.count) item\(urls.count == 1 ? "" : "s")\u{2026}", error: false)

        var args = [script]
        if moveBox.state == .off { args.append("--no-move") }
        args += urls.map { $0.path }

        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            p.arguments = args
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            var text = ""
            var failed = false
            do {
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                text = String(decoding: data, as: UTF8.self)
                failed = p.terminationStatus != 0
            } catch {
                text = "Could not start Python: \(error.localizedDescription)"
                failed = true
            }
            DispatchQueue.main.async { self.finish(text, failed: failed) }
        }
    }

    func finish(_ text: String, failed: Bool) {
        var lines: [String] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("REPORT: ") {
                reports.append(URL(fileURLWithPath: String(line.dropFirst(8))))
            } else {
                lines.append(String(line))
            }
        }
        if !reports.isEmpty {
            lines.append("")
            lines.append("Report\(reports.count == 1 ? "" : "s"):")
            lines += reports.map { "  " + $0.path }
        }
        reportButton.title = reports.count > 1 ? "Open Reports" : "Open Report"
        setRunning(false)
        show(lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines), error: failed)
        if !NSApp.isActive { NSApp.requestUserAttention(.informationalRequest) }
    }

    func show(_ s: String, error: Bool) {
        output.string = s
        output.textColor = error ? .systemRed : .labelColor
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
