# Inbox Command

Check Ganesha's Pigeon inbox — messages from other SAFE apps or Claude Projects.

## When invoked: `/inbox`

```python
import sys, json, os

# Load .env so pigeon connects to Postgres (not SQLite)
_env = "/mnt/c/Users/Sean/Documents/GitHub/Willow/.env"
with open(_env) as _f:
    for _line in _f:
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k] = _v

sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow")

from core import pigeon

messages = pigeon.get_inbox("ganesha-cli", unread_only=True)

if not messages:
    print("Inbox empty — no unread messages.")
else:
    print(f"=== INBOX ({len(messages)} unread) ===\n")
    for m in messages:
        print(f"[{m['id']}] From: {m['from_app']}")
        print(f"    Subject: {m['subject']}")
        print(f"    Sent: {m['sent_at'][:19]}")
        print(f"    Body: {m['body'][:300]}")
        print()
```

After reading, mark all as read:

```python
count = pigeon.mark_inbox_read("ganesha-cli")
print(f"Marked {count} messages read.")
```

## Send a message to another app

```python
pigeon.send_to_inbox(
    to_app="oakenscroll",          # target app_id
    from_app="ganesha-cli",
    username="Sweet-Pea-Rudi19",
    subject="Session update",
    body="Completed: consent system. Next: inbox skill.",
)
```

Or via Pigeon drop (respects consent gate):

```python
import requests
requests.post("http://localhost:8420/api/pigeon/drop", json={
    "topic": "send",
    "app_id": "ganesha-cli",
    "username": "Sweet-Pea-Rudi19",
    "payload": {
        "to": "oakenscroll",
        "subject": "From Ganesha",
        "body": "Your message here.",
    }
})
```

## Reply to Oakenscroll (Drive publish)

`send_to_inbox()` automatically mirrors to Drive when `to_app` is a cloud app.
Reply lands in: `My Drive/Willow/Nest/inbox/oakenscroll/`

```python
pigeon.send_to_inbox(
    to_app="oakenscroll",
    from_app="ganesha-cli",
    username="Sweet-Pea-Rudi19",
    subject="Re: ping",
    body="Received loud and clear. Drive transport working.",
)
# Also written to: My Drive/Willow/Nest/inbox/oakenscroll/msg_TIMESTAMP_ganesha-cli.json
```

## Notes

- `ganesha-cli` is the registered app_id for this Claude Code session
- Inbox reads from **Postgres** (WILLOW_DB_URL) — .env is loaded automatically
- `unread_only=False` shows last 100 messages
- Consent gate applies to drops; direct `send_to_inbox()` bypasses it (internal use only)
- Cloud apps (oakenscroll): messages automatically mirrored to `My Drive/Willow/Nest/inbox/{app_id}/`
