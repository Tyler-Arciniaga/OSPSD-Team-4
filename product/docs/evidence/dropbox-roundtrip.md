# Dropbox live round trip

Status: NOT RUN YET. No result below is a pass until a human runs the script
against the Dropbox test account and pastes its output here.

## What it checks

`product/scripts/dropbox_roundtrip.py` runs against the provider selected by
`STORAGE_PROVIDER` (default `dropbox_client_impl:DropboxDocumentStorage`,
Team 3 at commit `6b51756`) and writes only under a new folder
`/ospsd-team4-roundtrip-<random>`, deleted at the end:

- `create`, `create` on an existing file (expects `ConflictError`),
  `replace`, `download`, `get_metadata`, `delete`, `download` after delete
  and `replace` on a missing file (both expect `NotFoundError`).
- `create` on a nested path whose parent folders do not exist. This answers
  the open question in the brief: if it fails, the product switches to the
  flat path `/standup-<day>-<assignee>.md` and DESIGN.md 4.2 records it.
- The product's own doc upsert (`StorageAdapter.write_doc`) on a new nested
  path, then again on the same path.

The script prints exception class names and error codes only, never provider
messages. Its logic is tested against the in-memory storage
(`tests/test_dropbox_roundtrip.py`); that proves the script, not Dropbox.

## How to run

From `product/`, with the test account's credentials exported in the shell
(never written to a file in the repo):

```sh
export DROPBOX_APP_KEY=... DROPBOX_APP_SECRET=... DROPBOX_REFRESH_TOKEN=...
uv run python scripts/dropbox_roundtrip.py
```

Exit status 0 means every check passed.

## Result

Date: (to fill in)
Commit: (to fill in)

(paste the script's output here)
