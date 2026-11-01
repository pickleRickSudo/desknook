import Foundation
import CoreGraphics
import ApplicationServices

// Sends Option-Z on the HID event tap, which triggers macOS Dictation
// when the Dictation shortcut is set to ⌥Z.
//
//   dictate                 toggle dictation only
//   dictate --enter         toggle dictation, then press Return
//   dictate --enter --delay 400
//   dictate --dry-run       print what would be sent, touch nothing

guard AXIsProcessTrusted() else {
    FileHandle.standardError.write(Data("accessibility\n".utf8))
    exit(2)
}

let args = CommandLine.arguments
let sendReturn = args.contains("--enter")
let dryRun = args.contains("--dry-run")

let delayMs: UInt32 = {
    if let i = args.firstIndex(of: "--delay"), i + 1 < args.count, let v = UInt32(args[i + 1]) {
        return v
    }
    return 400
}()

if dryRun {
    print("option-z\(sendReturn ? " then return after \(delayMs)ms" : "")")
    exit(0)
}

let source = CGEventSource(stateID: .hidSystemState)
let optionKey: CGKeyCode = 58
let zKey: CGKeyCode = 6
let returnKey: CGKeyCode = 36

func modifier(_ code: CGKeyCode, _ flags: CGEventFlags) {
    let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true)
    event?.type = .flagsChanged
    event?.flags = flags
    event?.post(tap: .cghidEventTap)
}

func key(_ code: CGKeyCode, down: Bool, flags: CGEventFlags) {
    let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: down)
    event?.flags = flags
    event?.post(tap: .cghidEventTap)
}

// Option-Z — start or stop dictation.
modifier(optionKey, .maskAlternate)
usleep(15_000)
key(zKey, down: true, flags: .maskAlternate)
usleep(15_000)
key(zKey, down: false, flags: .maskAlternate)
usleep(15_000)
modifier(optionKey, [])

// Give macOS time to finalise the utterance and insert it into the
// focused field, then submit.
if sendReturn {
    usleep(delayMs * 1000)
    key(returnKey, down: true, flags: [])
    usleep(15_000)
    key(returnKey, down: false, flags: [])
}
