# Lazer V2

Delete your own Discord messages in bulk, or keep a channel clean automatically
with a background guard that wipes your history when a trigger word appears.

It is a desktop app (PyQt6), it runs on your machine, and it talks to Discord
with your own user token. No bot, no server, nothing leaves your PC except the
requests you asked for.

> **Read this before you use it**
>
> - Automating a user account breaks Discord's Terms of Service. Accounts do get
>   disabled for it. That risk is yours.
> - Deletion is permanent. There is no undo. Preview first, back up by default.
> - `session.lzr` and `session.key` are your account. Treat them like a password.

## What it does

- **Purge by hand.** Pick DMs, channels or an entire server, filter by keyword,
  time range and attachments, then delete in one go. Preview shows the count
  first; a confirm step stands between the scan and the first delete.
- **Conversation Guard.** Watch channels and either delete messages older than a
  TTL, or wipe your whole history there when you post a trigger word.
- **Background daemon.** Close the window with the guard armed and a tray process
  keeps watching, with the same rules and the same watchlist.
- **Search instead of scrolling.** Purging asks Discord for *your* messages
  rather than reading every message in the channel, so it is much faster in busy
  servers. If search is unavailable it falls back to reading history, and the
  result is the same.

## Requirements

- Python 3.9 or newer (developed on 3.14)
- Windows 10/11, or Linux with Qt tray support
- PyQt6 pulls in the Qt runtime, so expect a few hundred MB on disk

## Install

```bash
git clone https://github.com/keke2di/Lazerv2.git
cd Lazerv2
python -m venv .venv
```

Activate the environment, then install the dependencies:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

```bash
# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

If you prefer a normal install, `pip install .` gives you `lazerv2` and
`lazerv2-guard` commands instead. Running from a checkout needs nothing beyond
the dependencies.

## First run

Paste a user token into the login box. Quotes around a token copied from the
DevTools console are stripped for you, and the token is checked in the
background so the window never freezes.

After that, accounts appear as cards. Click the avatar at the bottom of the rail
to switch accounts without logging in again. Each account keeps its own
monitors; switching loads that account's list.

## Purging messages

1. **Targets.** Tick DMs, channels, or a server row to take everything in it.
   The search box filters the list. A whole-server target covers threads and
   forum posts as well.
2. **Filters.** Keyword chips (whole word by default — `cat` does not match
   `concatenate`), time range, content type, and whether to save a backup.
3. **Preview or Scan & delete.** Preview counts matches and stops. Scan & delete
   counts first, then waits for you to confirm the number. **Cancel** is
   focused, so pressing Enter never deletes anything.

**Stop** ends a purge after the current request. It does not wait out a
rate-limit pause.

With backups on, everything about to be deleted is written to
`Documents\Lazer backups` as JSON plus a readable HTML page *before* the first
delete. If the backup cannot be written, nothing is deleted.

## Conversation Guard

A monitor is a channel plus a trigger word plus a TTL:

- **Trigger.** Post a message matching the trigger and your history in that
  channel is wiped. Matching is whole-word by default; switch the match cell to
  *Contains* if you want substrings.
- **TTL.** Your messages there are deleted once they are older than the TTL.
  Only messages sent after the monitor was armed count.
- **Match.** Per-monitor, editable in the table like the trigger and TTL.

The **Armed / Off** switch above the table controls the whole account. Arming
starts a fresh guard window, so nothing you posted while it was off is touched.

A trigger wipe runs a second pass over the newest messages, so anything posted
while the wipe runs is cleaned up too. **Stop** on a running wipe really stops
it: the channel gets a fresh guard window, and the trigger message that started
the wipe will not fire again.

## Background guard

Closing the window with the guard armed hands off to a tray process:

```bash
python main.py daemon              # tray icon
python main.py daemon --headless   # no tray icon, for terminals
python main.py daemon --autostart  # start with Windows, then exit
python main.py daemon --no-autostart
```

`python -m lazerv2.tray_daemon` does the same thing. Only one GUI and one daemon
can run per profile; a second copy refuses to start. The tray menu can stop and
start monitoring, open the window, or exit.

If the daemon has no usable saved session it waits (and says so in its log)
instead of claiming the guard from the open window.

## Where your data lives

State is kept in your profile, not in the checkout, so the app also works from a
read-only folder. Set `LAZER_DATA_DIR` to point somewhere else.

| File | What it is |
|---|---|
| `session.lzr` | saved accounts, AES-256-GCM encrypted |
| `session.key` | the key, sealed with Windows DPAPI when available |
| `watchlist.json` | monitors and the armed flag, per account |
| `config.json` | theme, last account, last purge, backup setting |
| `daemon.heartbeat` | how the window knows the daemon is alive |
| `gui.lock`, `daemon.lock` | one GUI and one daemon per profile |
| `logs/daemon.log` | daemon log, rotated at 1 MB |
| `avatar_cache/`, `image_cache/` | downloaded avatars and server icons |

On Windows that is `%APPDATA%\LazerV2`; on Linux `$XDG_DATA_HOME/LazerV2`. An
older install's files next to the source are migrated once, then removed.

Backups are the exception: they go to `Documents\Lazer backups`, because that is
where you will look for them. They contain message text, so treat them like the
messages themselves. Attachment links expire; file names are kept.

## How your token is protected

Accounts are encrypted with AES-256-GCM using a random per-profile key. On
Windows the key is sealed with DPAPI (`CryptProtectData`), so copying both files
to another machine or another user account does not help.

What that protects: a `session.lzr` that leaks on its own is useless without the
matching key. What it does not protect: anything already running as you can ask
DPAPI to unseal the key. A logged-in session is a logged-in account.

If the key goes missing or stops unsealing, the old `session.lzr` is renamed to
`session.lzr.broken-<time>` rather than overwritten, and you log in again.

## How it fits together

```
main.py                  launcher: `main.py` or `main.py daemon`
lazerv2/
  app.py                 window <-> login, account switching, single instance
  app_config.py          config.json and the daemon heartbeat
  app_paths.py           profile directory and legacy migration
  globals.py             process context, snowflake helpers
  guard_core.py          the guard engine, shared by window and daemon (no Qt)
  guard_utils.py         snowflakes, TTL math, trigger matching
  message_search.py      find your own messages, with a resume point
  purge_export.py        JSON + HTML backup written before a purge
  fileio.py              atomic writes
  file_lock.py           cross-process lock around the watchlist
  http_client.py         retries, 429 handling, transport errors
  logging_setup.py       rotating log that works under pythonw
  secret_store.py        DPAPI sealing for the session key
  session_store.py       encrypted token storage and caches
  watchlist_store.py     per-account watchlist, versioned, lock-aware
  tray_daemon.py         tray, account handoff, background loop
  api/discord.py         token validation
  ui/                    windows, pages, widgets, theme
  workers/               fetch, delete, gateway, monitor, token check
```

The guard logic lives in `guard_core.py` and has no Qt dependency, which is why
the window and the daemon cannot drift apart.

## Troubleshooting

**The guard never fires.** Check that the guard is *Armed* and the monitor's
match mode is what you expect. The trigger must be posted *after* arming.

**The daemon will not start.** It needs a saved session and at least one
monitor. Read `%APPDATA%\LazerV2\logs\daemon.log`, or run
`python main.py daemon` in a terminal to see the output.

**"Search unavailable — reading history".** Discord's search is not ready for
that channel or is disabled. The purge continues by walking history: slower,
same result.

**"N can't be deleted".** System messages, call notices and archived or locked
threads cannot be deleted by anyone. They are counted and skipped.

**No tray icon on Linux.** Some desktops need an appindicator extension.

## Limits

- It only ever deletes messages *you* wrote.
- One account is monitored at a time. Each account keeps its own list and its
  own armed flag.
- The guard watches channels and DMs, not individual threads. Threads and forum
  posts are reached through a whole-server purge.
- TTL monitors check the newest messages every few seconds; they are not instant.
- Attachment filtering means "has an attachment", not per-file rules.

## License

MIT. See [LICENSE](LICENSE).