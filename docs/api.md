# Lobotomy Push API

Push articles, stories, and URLs into your Lobotomy reading list from any application.

---

## Setup

### 1. Generate an API key

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Add it to config.json

```json
{
  "api": {
    "push_key": "your-generated-key-here"
  }
}
```

### 3. Restart the server

```sh
python3 tools/serve.py
```

### 4. Verify

```sh
curl https://your-lobotomy-host/api/status
```

```json
{"ok": true, "version": "1", "push_configured": true}
```

---

## Authentication

Every endpoint except `/api/status` requires a Bearer token in the
`Authorization` header.

```
Authorization: Bearer <your-push-key>
```

---

## Endpoints

### `GET /api/status`

Health check. No authentication required.

**Response**

```json
{
  "ok": true,
  "version": "1",
  "push_configured": true
}
```

---

### `POST /api/push`

Push an article into the reading list inbox. Returns `201 Created` on
success.

**Request body** (JSON)

| Field     | Type     | Required | Description |
|-----------|----------|----------|-------------|
| `url`     | string   | conditionally | URL of the article. If `content` is omitted, Lobotomy fetches the page automatically. |
| `title`   | string   | conditionally | Article title. Auto-extracted from fetched content if omitted. Required when `url` is not provided. |
| `content` | string   | conditionally | Full article body text (plain text or Markdown). If provided alongside `url`, the fetch is skipped. |
| `tags`    | string[] | no | Tag list. Example: `["news", "tech"]` |
| `source`  | string   | no | Identifier for your application. Example: `"TubeNews"`. Defaults to `"external-api"`. |
| `author`  | string   | no | Article author name. |

**Rules:**
- At least one of `url` or `content` is required.
- If `url` is omitted, `title` is required.
- If `url` is provided without `content`, Lobotomy fetches the URL. If the fetch fails, the URL is still saved.

**Successful response** — `201 Created`

```json
{
  "ok": true,
  "duplicate": false,
  "id": "my-article-title",
  "filename": "my-article-title.md",
  "title": "My Article Title",
  "url": "https://example.com/articles/my-article",
  "saved": "2026-05-01"
}
```

**Duplicate response** — `200 OK` (when the same URL already exists in the inbox)

```json
{
  "ok": true,
  "duplicate": true,
  "id": "my-article-title",
  "filename": "my-article-title.md",
  "title": "My Article Title",
  "url": "https://example.com/articles/my-article",
  "saved": "2026-04-28"
}
```

**Error responses**

| Status | `code`           | Cause |
|--------|------------------|-------|
| 400    | `MISSING_FIELDS` | Neither `url` nor `content` given, or `title` missing when required |
| 401    | `UNAUTHORIZED`   | `Authorization` header missing or malformed |
| 403    | `FORBIDDEN`      | API key is wrong |
| 501    | `NOT_CONFIGURED` | `api.push_key` is not set in config.json |

```json
{
  "error": "Provide url, content, or both",
  "code": "MISSING_FIELDS"
}
```

---

### `GET /api/inbox`

List items currently in the reading list inbox.

**Query parameters**

| Param    | Default | Description |
|----------|---------|-------------|
| `limit`  | `20`    | Max items to return. Maximum `100`. |
| `since`  | —       | ISO date (`YYYY-MM-DD`). Return only items saved on or after this date. |
| `source` | —       | Filter to items pushed by a specific source application. |

**Response**

```json
{
  "ok": true,
  "count": 2,
  "items": [
    {
      "id": "my-article-title",
      "filename": "my-article-title.md",
      "title": "My Article Title",
      "url": "https://example.com/articles/my-article",
      "saved": "2026-05-01",
      "source": "TubeNews",
      "author": "Jane Smith",
      "tags": ["news", "tech"]
    },
    {
      "id": "another-article",
      "filename": "another-article.md",
      "title": "Another Article",
      "url": null,
      "saved": "2026-04-30",
      "source": "iOS Shortcut",
      "author": null,
      "tags": []
    }
  ]
}
```

Items are ordered newest first by file modification time.

---

### `DELETE /api/inbox/:filename`

Remove an item from the inbox. Only items that have not yet been archived
or ingested into the wiki can be deleted this way.

```
DELETE /api/inbox/my-article-title.md
Authorization: Bearer <push_key>
```

**Success response** — `200 OK`

```json
{
  "ok": true,
  "deleted": "my-article-title.md"
}
```

**Error responses**

| Status | `code`          | Cause |
|--------|-----------------|-------|
| 400    | `INVALID_PATH`  | Filename attempts path traversal |
| 404    | `NOT_FOUND`     | File not in inbox (already archived or never existed) |

---

## Examples

### Push a URL (auto-fetch)

```sh
curl -X POST https://your-lobotomy-host/api/push \
  -H "Authorization: Bearer <push_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/articles/my-article",
    "source": "MyApp",
    "tags": ["news"]
  }'
```

### Push pre-fetched content

Useful when you already have the article text, or when the target site is
paywalled/JavaScript-rendered and auto-fetch would fail.

```sh
curl -X POST https://your-lobotomy-host/api/push \
  -H "Authorization: Bearer <push_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/articles/my-article",
    "title": "My Article Title",
    "content": "Full article text here...",
    "source": "MyApp",
    "author": "Jane Smith",
    "tags": ["news", "tech"]
  }'
```

### Push without a URL

```sh
curl -X POST https://your-lobotomy-host/api/push \
  -H "Authorization: Bearer <push_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Meeting notes 2026-05-01",
    "content": "Key points from today...",
    "source": "Notes App",
    "tags": ["notes", "meetings"]
  }'
```

### List recent inbox items

```sh
curl "https://your-lobotomy-host/api/inbox?limit=10&source=TubeNews" \
  -H "Authorization: Bearer <push_key>"
```

### Delete an item

```sh
curl -X DELETE "https://your-lobotomy-host/api/inbox/my-article-title.md" \
  -H "Authorization: Bearer <push_key>"
```

### Check for duplicate before pushing (JavaScript)

```js
async function lobotomizePush(config, article) {
  const resp = await fetch(`${config.host}/api/push`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${config.key}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      url:     article.url,
      title:   article.title,
      content: article.content,  // omit to let Lobotomy fetch
      source:  config.appName,
      tags:    article.tags,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(`Lobotomy push failed: ${err.error || resp.status}`);
  }

  const data = await resp.json();
  return data;  // { ok, duplicate, id, filename, title, url, saved }
}
```

### Python example

```python
import requests

def lobotomy_push(host, key, url, title=None, content=None,
                  source="my-app", tags=None, author=None):
    payload = {"url": url, "source": source}
    if title:   payload["title"]   = title
    if content: payload["content"] = content
    if tags:    payload["tags"]    = tags
    if author:  payload["author"]  = author

    r = requests.post(
        f"{host}/api/push",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()  # {"ok": True, "duplicate": False, "id": ..., ...}
```

---

## Integration checklist

- [ ] Generate a strong key with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- [ ] Set `api.push_key` in config.json and restart Lobotomy
- [ ] Verify `GET /api/status` returns `"push_configured": true`
- [ ] Test a push with `curl`
- [ ] Store the key as a secret in your integration (never in source code)
- [ ] Include a meaningful `source` field so you can filter by app in `GET /api/inbox`
- [ ] Handle `"duplicate": true` responses gracefully (no re-processing needed)

---

## Notes

- Articles pushed with URL only are fetched immediately by the server. If
  the fetch fails (paywalled site, JS-rendered page, 403), the URL is still
  saved so you can open it later.
- Filenames are derived from the article title, slugified and truncated to
  60 characters. Collisions append a Unix timestamp.
- Pushed items appear in the Lobotomy web interface under **Reading List**
  and can be wikified from there.
- The inbox is a directory of Markdown files at `raw/inbox/`. You can also
  drop files directly into that directory without going through the API.
