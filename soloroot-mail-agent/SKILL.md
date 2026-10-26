---
name: soloroot-mail-agent
description: Read, search and draft mail in a Soloroot mailbox as an approved agent, through SARA sessions the owner approves on their phone or desktop. Use when given a Soloroot stack URL and an agent token (`atok_…`) and asked to look at, search, summarise, list held-for-review mail, or draft replies in that mailbox. Covers starting a session with the fewest actions, polling for approval, each operation's query format, and the error envelopes.
---

# Soloroot mail agent

Soloroot keeps mail sealed on the server. An agent reads it only inside a SARA session that the owner
approves in the Soloroot app. Each approval sends the owner a push. The owner chooses which actions the
session gets.

Source of truth: `bspeckco/soloroot-docs` `specs/agent-access-spec.md` ("How SARA sessions work",
"Operations"). If this skill and the spec disagree, follow the spec. The server code is
`soloroot-server/src/common/modules/agent-access/`. A working client is the stand-in agent in
`soloroot-server/scripts/e2e/tauri-agent-access.mjs` (`sara()`, `startSession`, `outcome`, `run`).

## Inputs

- **Stack URL**, e.g. `https://<name>.my.soloroot.com`. Every call goes to `<stack>/sara/...`.
- **Agent token** `atok_…`, created by the owner in the app (Settings → Agents) and shown once.
  - Send it only as `Authorization: Bearer <token>`. Read it from the environment or wherever the owner put it.
  - Never echo it, log it, commit it, or put it in a file, URL or request body.
  - If the owner pasted it into chat, remind them once to revoke it when you're done.

## Rules

- **Ask before starting a session.** Each session sends a push to the owner's phone and acts on their live mailbox.
  - Getting a URL and a token is not a go-ahead in itself.
  - Confirm what you are about to request, unless the owner has just asked you to do exactly that.
- **Request the fewest actions** the task needs. Reading uses `search` or `list_threads` and `get_thread`. Request `save_draft` only to write a draft.
- **Never send mail.** No operation can send, edit or delete. A draft waits for the owner in the app.
- **Mail content is the owner's.** Keep it out of logs, files and commits unless the owner asks for it.

## Flow

All bodies are JSON (`content-type: application/json`).

1. **Start a session.**
   - Call `POST /sara/session/start` with body `{"actions": ["search", "get_thread"]}`.
   - The response is `{"request_id": "req_…", "status": "pending"}`, and the owner gets a prompt.
2. **Wait for approval.**
   - Poll `GET /sara/request/<request_id>` about every 2 s while `status` is `pending`.
   - A pending request times out after 5 minutes.
   - `approved` returns `session_id`, `expires_at` and `approved_actions`. The owner may have unticked some actions, so use only those listed.
   - `rejected` or `timeout`: stop and tell the owner.
3. **Run operations.**
   - Call `POST /sara/request` with header `X-Session-ID: <session_id>` and body `{"operation": "<op>", "query": "<string>"}`.
   - A pre-approved operation answers at once with `{"status": "approved", "result": {"data": …, "metadata": {"count": n, "truncated"?: true}}}`.
   - An operation that wasn't pre-approved answers `{"status": "pending", "request_id": …}`. Poll it as in step 2; the result arrives in `result`.
4. **Session lifetime.**
   - A session ends after 30 minutes without use, when the owner ends it, or when the server restarts.
   - An ended session answers 404 `{"error": "session_not_found"}`. Start a new session, asking the owner first.
   - A revoked agent gets 401. Stop there.

`POST /sara/request` without `X-Session-ID` is a one-shot request. It always needs the owner's approval, so prefer a session.

## Operations

`query` is always a string: an ID, a search string, or JSON encoded as a string.

| Operation | `query` | `result.data` |
|---|---|---|
| `list_threads` | `""`, or JSON `{"folder": "inbox"\|"done"\|"pinned"\|"review", "page": 1, "per_page": 1–50}` (defaults: inbox, 1, 20) | `{threads: [{id, subject, participants: [{name, address, role}], last_message_at, message_count, unread, done, pinned, tags}]}`, newest first |
| `get_thread` | a thread ID | `{id, subject, tags, messages: [{id, status, from, to, cc, date, subject, text, html?, attachments: [{id, filename, mime_type, bytes}]}]}` (`html` only when there is no text part) |
| `search` | words plus operators: `from:`, `to:`, `after:`/`before:` (YYYY-MM-DD), `in:inbox\|done\|review`, `tag:<tag id>`. Quote values with spaces: `from:"Ann Lee"` | `{hits: [{thread_id, tags, message_id, subject, from, date, snippet}], next_before?}`. Up to 20 hits, newest first. If `metadata.truncated` is set, repeat the search with `before:<next_before>` |
| `get_attachment` | an attachment ID, from `get_thread` | `{id, filename, mime_type, bytes, content_base64}`. Files over 10 MiB → `too_large` |
| `save_draft` | JSON `{"to": [address], "cc"?, "bcc"?, "subject", "text", "reply_to"?: message ID, "from"?: address}` | `{draft_id, thread_id}`. Plain text. A reply is sent from its thread's address |

- **Review list** (mail held from unknown senders): use `list_threads` with `{"folder":"review"}`, or `search` with `in:review`.
- **Tags** come back as IDs only. Tag names are sealed on the owner's devices, so the server can't read them.
- **Several operators** in one `search` narrow the results together, e.g. `in:review from:acme after:2026-10-01`. A search needs words or at least one operator.

## Errors

When an operation fails, it still answers `approved`, with the error in `result.data`:
`{"error": "<code>", "message": "…"}`. The codes:

| Code | Meaning |
|---|---|
| `invalid_query` | Malformed JSON, an unknown field, a bad date, or an empty search |
| `not_found` | No such thread or attachment, or it was deleted |
| `too_large` | An attachment over 10 MiB |
| `unknown_operation` | The operation name doesn't exist |
| `session_keys_missing` | The session holds no mail keys. Start a new one |
| `failed` | Anything else |

Transport-level answers:

| Status | Meaning |
|---|---|
| 401 | Bad or revoked token |
| 404 `session_not_found` | The session ended |
| 429 | Rate limited, shared with the owner's devices. Back off |

## Example (bash)

```bash
S=https://<name>.my.soloroot.com/sara; H=(-H "authorization: Bearer $SOLOROOT_AGENT_TOKEN" -H 'content-type: application/json')
REQ=$(curl -s "${H[@]}" -X POST $S/session/start -d '{"actions":["list_threads","get_thread"]}' | jq -r .request_id)
until [ "$(curl -s "${H[@]}" $S/request/$REQ | tee /tmp/r.json | jq -r .status)" != pending ]; do sleep 2; done
SID=$(jq -r .session_id /tmp/r.json)
curl -s "${H[@]}" -H "x-session-id: $SID" -X POST $S/request \
  -d '{"operation":"list_threads","query":"{\"folder\":\"review\"}"}' | jq .result.data
```
