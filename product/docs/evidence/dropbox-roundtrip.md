# Dropbox live round trip

Status: PASSED on 2026-11-16 (run by Tyler against the Dropbox test account).
Every check passed, and `create` makes missing parent folders, so the
standup doc stays at `/standups/<day>/<assignee>.md`. The flat-path fallback
is not needed.

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

- Date: 2026-11-16
- Product commit: `dbd8247` (branch `feat/ospsd-team4-product-ci`)
- Provider: `dropbox_client_impl:DropboxDocumentStorage`, Team 3 commit
  `6b5175608416bd5f56793207d968f96b62730d51`, with `cloud-storage-api` tag
  `0.3.1`
- Credentials: a refresh token from `python -m dropbox_client_impl login`,
  stored outside the repo, with the app key and secret exported in the shell.
  Paths are relative to the Dropbox app's root.
- Later pin: the product now pins Team 3's tag `v0.3.0` (merge commit
  `fde60d4`). Its `src/dropbox_client_impl` is byte-identical to
  `6b51756`, so this result still applies
  (`partner-installs.md`, Pin update).

Output, unedited:

```text
Run root: `/ospsd-team4-roundtrip-5d7b506b`

| Check | Expected | Result | Pass |
| ----- | -------- | ------ | ---- |
| create | FileInfo for the new file | ok | yes |
| create existing | ConflictError | ConflictError (conflict) | yes |
| replace | FileInfo with the new size | ok | yes |
| download | the replaced bytes | ok | yes |
| get_metadata | size and an ID | ok | yes |
| delete | no error | ok | yes |
| download deleted | NotFoundError | NotFoundError (not_found) | yes |
| replace missing | NotFoundError (the doc upsert relies on it) | NotFoundError (not_found) | yes |
| create nested (parents missing) | FileInfo; parent folders created | ok | yes |
| download nested | the nested bytes | ok | yes |
| doc upsert (new file) | created | ok | yes |
| doc upsert (existing file) | replaced | ok | yes |
| download upserted | the second write's bytes | ok | yes |

Parent folders created by `create`: yes
Cleanup: /ospsd-team4-roundtrip-5d7b506b: deleted; /ospsd-team4-roundtrip-5d7b506b-flat.md: absent
exit=0
```

`-flat.md: absent` is expected: the `delete` check had already removed it.

## What this does and does not show

- Shown: the contract operations the product uses behave as the shared
  contract says on live Dropbox, including the `ConflictError` and
  `NotFoundError` cases the doc upsert depends on, and nested parents are
  created.
- Not shown: the race cases of the upsert (a `ConflictError` from `replace`
  or from `create` caused by a concurrent writer). Those are covered only by
  the fake (`tests/test_storage_adapter.py`).
- Not run: the shared storage conformance suite against Team 3's package.

## Observations about the package (reported, not changed)

1. `dropbox_client_impl.auth` calls `load_dotenv()` when imported. It
   searches upward from the installed package and, in a local checkout,
   finds the repo-root `.env`, so the HW2 Trello variables are loaded into
   the process environment (existing variables are not overwritten, nothing
   is printed). In the image this has no effect: `.env` files are excluded
   by `.dockerignore`.
2. `login` writes the refresh token to `.dropbox_token.json` in the current
   directory unless `DROPBOX_TOKEN_FILE` is set. The repo `.gitignore` now
   ignores `**/.dropbox_token*.json`.
3. Some of its error messages include local paths (for example an unreadable
   token file). The product's adapters never pass provider messages on.
