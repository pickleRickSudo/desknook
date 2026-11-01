# Security

DeskNook is a small tool for a trusted home network. It is **not** hardened for
the open internet, and it deliberately can type into your Mac. Read this before
you put it on a network you do not control.

## The exposure, plainly

The server has **no authentication**. By default it listens on `0.0.0.0:7777`,
which means any device on your local network can:

- read live system stats (hostname, CPU, memory, disks, processes),
- toggle macOS Dictation, and
- make the Mac **type the contents of `phrase.txt` into whatever app is
  frontmost, then press Return**.

That last one is the one to think about. If a terminal is focused, the phrase
runs as a shell command.

## What we recommend

- **Keep it on a trusted LAN.** Your home Wi-Fi is the intended setting.
- **Never port-forward it** or expose it through a tunnel. There is no login
  screen to stop anyone.
- **Bind to loopback** if you only ever look at it from the Mac itself:
  `HOST=127.0.0.1 python3 server.py`
- **Keep `phrase.txt` boring.** Treat it as public text, because one drag sends
  it somewhere. Never put a password, key, or anything sensitive in it.
- **Lock the Mac only if you accept the buttons stop working.** Synthetic
  keystrokes go to the login window when the screen is locked, so a slide while
  locked does nothing useful. Nothing is leaked, but nothing happens either.

## What is already handled

- `phrase.txt` is gitignored, `chmod 600`, and marked `metadata_never_index`
  so Spotlight skips the folder.
- The HTTP server only serves `index.html` and JSON. There is no static file
  route, so `/phrase.txt` and path-traversal attempts return 404.
- The API never returns the phrase text — only its character count.
- The phrase is never written to the log; the log records exit codes and
  lengths only.
- The phrase is passed to the helper as a **path**, not as an argument, so it
  does not appear in `ps`.

## Reporting a problem

Open a regular issue for anything non-sensitive. If you believe you have found
something that could let an unauthenticated user do more than the above, please
use GitHub's private vulnerability reporting on this repository instead of a
public issue.
