import Foundation
import CoreAudio

// micactive — prints 1 if any input-capable audio device is currently in use
// (i.e. some process is recording). No TCC permission required.
//
// macOS Dictation turns the input device on while it listens, so this is a
// reliable, permission-free proxy for "dictation is live", no matter whether
// it was started from the phone or from the Mac keyboard.

func hasInputStreams(_ device: AudioDeviceID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams,
        mScope: kAudioObjectPropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(device, &addr, 0, nil, &size) == noErr else { return false }
    return size > 0
}

func isRunningSomewhere(_ device: AudioDeviceID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var value: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(device, &addr, 0, nil, &size, &value) == noErr else { return false }
    return value != 0
}

var addr = AudioObjectPropertyAddress(
    mSelector: kAudioHardwarePropertyDevices,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)

var size: UInt32 = 0
guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else {
    fputs("coreaudio: cannot size device list\n", stderr)
    exit(1)
}

let count = Int(size) / MemoryLayout<AudioDeviceID>.size
var devices = [AudioDeviceID](repeating: 0, count: max(count, 1))
guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &devices) == noErr else {
    fputs("coreaudio: cannot read device list\n", stderr)
    exit(1)
}

var active = false
for device in devices.prefix(count) where hasInputStreams(device) {
    if isRunningSomewhere(device) { active = true; break }
}

print(active ? "1" : "0")
