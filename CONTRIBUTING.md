# Contributing

Small project, small process. Issues and pull requests are both welcome.

## Running it from a checkout

```sh
python3 -m pip install --user psutil
python3 server.py            # http://localhost:7777
```

If you are changing the buttons, you also want the helpers:

```sh
mkdir -p bin
swiftc -O dictate.swift -o bin/dictate
swiftc -O typephrase.swift -o bin/typephrase
```

Rebuilding a helper invalidates its Accessibility grant — remove the old entry
in System Settings → Privacy & Security → Accessibility and re-add the binary.

## Testing a change without typing into your own editor

Both helpers have a dry-run mode, and the endpoints expose it:

```sh
./bin/typephrase phrase.txt --dry-run      # prints the character count
./bin/dictate --dry-run                    # prints what it would send

curl -s -X POST 'localhost:7777/api/phrase?dry=1'
```

Use a throwaway TextEdit window for anything that really types. Never fire
`/api/phrase` or `/api/dictate?enter=1` while a terminal or a chat app with a
draft is frontmost.

To exercise the API without touching the real helpers, run a second server on
another port with a stub `bin/typephrase`:

```sh
PORT=7778 python3 server.py
```

## What gets committed

- Keep `phrase.txt` out of git. It is ignored; do not force-add it.
- Keep `bin/` out of git. It is build output.
- Match the existing style: plain standard-library Python, no build step, one
  HTML file with inline CSS/JS, no framework.

## Pull requests

Describe the behaviour change and how you checked it. Screenshots help for UI
changes — portrait and landscape both, since the layout has separate rules for
short screens.
