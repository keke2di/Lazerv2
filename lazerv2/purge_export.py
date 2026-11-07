# a copy of everything a purge is about to delete, written before it starts
#
# Two files per purge: JSON for keeping or re-processing, and a plain HTML
# page to read it back. Attachments are saved as links only; Discord CDN links
# are signed and stop working after a while, so the file names are kept too.

import datetime
import html
import json
import os

from lazerv2 import fileio
from lazerv2.guard_utils import message_timestamp_ms


def _iso(message_id):
    ms = message_timestamp_ms(message_id)
    if ms is None:
        return ""
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()


def _record(message, channel_name):
    attachments = []
    for item in message.get("attachments") or []:
        if isinstance(item, dict):
            attachments.append({
                "filename": item.get("filename") or "",
                "url": item.get("url") or "",
                "size": item.get("size"),
            })
    return {
        "id": str(message.get("id")),
        "channel_id": str(message.get("channel_id") or ""),
        "channel": channel_name or "",
        "timestamp": message.get("timestamp") or _iso(message.get("id")),
        "content": message.get("content") or "",
        "attachments": attachments,
        "type": message.get("type", 0),
    }


def build(messages, account_id=None, now=None):
    # messages: iterable of (message dict, channel name)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    records = [_record(message, name) for message, name in messages]
    records.sort(key=lambda r: (r["channel"], r["timestamp"]))
    return {
        "exported_at": now.isoformat(),
        "account_id": str(account_id) if account_id else None,
        "count": len(records),
        "messages": records,
    }


def render_html(document):
    rows = []
    current = None
    for record in document["messages"]:
        if record["channel"] != current:
            current = record["channel"]
            rows.append(f"<h2>{html.escape(current or record['channel_id'])}</h2>")
        links = "".join(
            f'<div class="att"><a href="{html.escape(a["url"], quote=True)}">'
            f'{html.escape(a["filename"] or a["url"])}</a></div>'
            for a in record["attachments"])
        rows.append(
            '<div class="msg">'
            f'<div class="ts">{html.escape(record["timestamp"])}</div>'
            f'<div class="body">{html.escape(record["content"])}</div>{links}</div>')
    body = "\n".join(rows) or "<p>No messages.</p>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Lazer backup {html.escape(document["exported_at"][:19])}</title>
<style>
body {{ font: 14px/1.5 system-ui, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 16px; }}
h2 {{ margin-top: 32px; border-bottom: 1px solid #8884; padding-bottom: 4px; }}
.msg {{ padding: 6px 0; border-bottom: 1px solid #8882; }}
.ts {{ color: #888; font-size: 12px; }}
.body {{ white-space: pre-wrap; word-wrap: break-word; }}
.att a {{ font-size: 12px; }}
</style></head><body>
<h1>Lazer backup</h1>
<p>{document["count"]} message(s), exported {html.escape(document["exported_at"])}.
Attachment links expire; the file names are kept.</p>
{body}
</body></html>
"""


def write(directory, messages, account_id=None, now=None):
    # returns (json path, html path); raises OSError if it can't be written
    os.makedirs(directory, exist_ok=True)
    document = build(messages, account_id, now)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(directory, f"lazer-backup-{stamp}")
    json_path, html_path = base + ".json", base + ".html"
    counter = 1
    while os.path.exists(json_path):
        counter += 1
        json_path, html_path = f"{base}-{counter}.json", f"{base}-{counter}.html"
    fileio.atomic_write_text(json_path, json.dumps(document, indent=2, ensure_ascii=False))
    fileio.atomic_write_text(html_path, render_html(document))
    return json_path, html_path
