import Foundation
import CoreGraphics
import ApplicationServices

// Types the contents of a text file into the frontmost app, one character
// at a time, then presses Return.
//
//   typephrase <file>              type it, then Return
//   typephrase <file> --no-enter   type it, no Return
//   typephrase <file> --delay 80   slower per character
//   typephrase <file> --dry-run    print what would be typed, touch nothing
//
// Exit codes: 2 not trusted, 3 empty file, 64 usage, 66 unreadable.

let args = Array(CommandLine.arguments.dropFirst())
guard let path = args.first, !path.hasPrefix("--") else {
    FileHandle.standardError.write(Data("usage: typephrase <file>\n".utf8))
    exit(64)
}

let dryRun = args.contains("--dry-run")
let wantReturn = !args.contains("--no-enter")

let delayMs: UInt32 = {
    if let i = args.firstIndex(of: "--delay"), i + 1 < args.count, let v = UInt32(args[i + 1]) {
        return v
    }
    return 45
}()

guard let raw = try? String(contentsOfFile: path, encoding: .utf8) else {
    FileHandle.standardError.write(Data("unreadable\n".utf8))
    exit(66)
}

// Newlines would submit mid-phrase, so flatten everything to single spaces.
let text = raw
    .replacingOccurrences(of: "\r\n", with: " ")
    .replacingOccurrences(of: "\n", with: " ")
    .replacingOccurrences(of: "\r", with: " ")
    .split(separator: " ", omittingEmptySubsequences: true)
    .joined(separator: " ")

guard !text.isEmpty else {
    FileHandle.standardError.write(Data("empty\n".utf8))
    exit(3)
}

if dryRun {
    print(String(text.count))
    exit(0)
}

// Only real typing needs the permission, so check after --dry-run.
guard AXIsProcessTrusted() else {
    FileHandle.standardError.write(Data("accessibility\n".utf8))
    exit(2)
}

let source = CGEventSource(stateID: .hidSystemState)

func typeCharacter(_ character: Character) {
    let units = Array(String(character).utf16)

    if let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) {
        down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
        down.post(tap: .cghidEventTap)
    }
    if let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) {
        up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
        up.post(tap: .cghidEventTap)
    }
}

for character in text {
    typeCharacter(character)
    usleep(delayMs * 1000)
}

if wantReturn {
    usleep(120_000)
    if let down = CGEvent(keyboardEventSource: source, virtualKey: 36, keyDown: true) {
        down.post(tap: .cghidEventTap)
    }
    usleep(15_000)
    if let up = CGEvent(keyboardEventSource: source, virtualKey: 36, keyDown: false) {
        up.post(tap: .cghidEventTap)
    }
}

print(String(text.count))
