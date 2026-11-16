# Partner package install verification (HW3 M1, WP1)

Date: 2026-11-15. Run on macOS (aarch64) in a fresh directory outside the
repository, with uv 0.12.22 and CPython 3.14.8 (downloaded by uv). It checks
that the product service's six packages (three contracts, three
implementations) install together, resolve to the expected commits, and
import.

## Result

| Package | Source | Pinned ref | Resolved commit | Result |
| --- | --- | --- | --- | --- |
| `issue-tracker-contract` 1.1.0 | `Tyler-Arciniaga/OSPSD-Team-4`, subdirectory `contract` | tag `issue-tracker-contract-v1.1.0` | `18fb7ae6f9c9cf5dbfed7208114d036a582ba54c` | pass (see note 1) |
| `team-4-issue-tracker-trello` 1.0.0 | `Tyler-Arciniaga/OSPSD-Team-4`, subdirectory `team-4-trello-impl` | tag `team-4-issue-tracker-trello-v1.0.0` | `c88ce2fd287f2524ba09ad5d94faf3983fdac08d` | pass |
| `calendar-contract` 0.1.0 | `Airbus-A330/OSPSD-Calendar` | tag `v0.1.0` (uv override) | `db73e6261a93b0c3cc6aa0a48c4687f742aacc9b` | pass |
| `team5-calendar` 0.1.0 | `juno-junhyuk-lee/OSPSD-Team-5`, subdirectory `packages/team5-calendar` | commit `b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2` (no tag yet) | `b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2` | pass |
| `cloud-storage-api` (reports 0.2.0) | `Dayana20/ospsd26-cloud-storage`, subdirectory `src/cloud_storage_api` | tag `0.3.1` | `664afc92f7cc0b2ee51cde6ddfa088cc3b37206c` | pass (see note 2) |
| `dropbox-client-impl` 0.1.0 | `Shreyas191/OSPSD-Team-3`, subdirectory `src/dropbox_client_impl` | commit `6b5175608416bd5f56793207d968f96b62730d51` (no tag yet) | `6b5175608416bd5f56793207d968f96b62730d51` | pass |
| `cloud-storage-client-api` 0.1.0 (pulled in by Team 3) | `Shreyas191/OSPSD-Team-3`, subdirectory `src/cloud_storage_client_api` | Team 3's own source, same commit | `6b5175608416bd5f56793207d968f96b62730d51` | pass |

Both tag commits for this repository match `git rev-parse <tag>^{commit}`.

## Notes and discrepancies

1. **The tracker contract must be declared as a PEP 508 URL, not through
   `[tool.uv.sources]`.** The brief asks for every contract in both
   `dependencies` and `[tool.uv.sources]`. For `issue-tracker-contract` that
   fails, because `team-4-issue-tracker-trello` declares the same contract as a
   direct URL in its metadata, and uv treats a `tag =` source and a `@<tag>`
   URL as different requirements even when they name the same tag:

   ```
   error: Failed to resolve dependencies for package `team-4-issue-tracker-trello==1.0.0`
     cause: Requirements contain conflicting URLs for package `issue-tracker-contract` in all marker environments:
            - git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@issue-tracker-contract-v1.1.0#subdirectory=contract
            - git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@issue-tracker-contract-v1.1.0#subdirectory=contract
   ```

   Declaring it directly in `dependencies` as
   `issue-tracker-contract @ git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@issue-tracker-contract-v1.1.0#subdirectory=contract`,
   with no `[tool.uv.sources]` entry, resolves. It is still a direct
   dependency pinned to the same immutable tag. The other two contracts work
   through `[tool.uv.sources]`.
2. **Declaring `cloud-storage-api` directly does not conflict** with Team 3's
   own `[tool.uv.sources]` entry (same URL, tag `0.3.1`). At that tag the
   package reports version `0.2.0`, which is expected (its `pyproject.toml`
   was not bumped). Cite the tag and commit, not the version.
3. **The calendar override works.** Team 5's metadata asks for
   `calendar-contract` at `v0.1.0-rc.1`; the override replaces that with
   `v0.1.0`, which resolves to `db73e62`.
4. **Calendar contract rules at `v0.1.0`** (read from the installed source):
   - `validate_write_title` only rejects a title that is empty or whitespace
     only. It sets no length limit and no character rules, so `Focus: <title>`
     needs no truncation for the contract. The product still caps the title
     length to keep calendar entries readable.
   - `validate_time_range` requires RFC 3339 with an explicit offset (`Z` or
     `+HH:MM`) and end strictly after start, compared by instant. Fractional
     seconds are allowed, but the product sends whole seconds.
   - `CreateEvent` and `ReplaceEvent` are strict and forbid extra fields.
   - Team 5 maps an unusable token to `PROVIDER_UNAVAILABLE` with
     `retryable=False` and a message telling the operator to rerun
     `python -m team5_calendar.authorize`.
5. **The shared calendar conformance suite was not run against Team 5's
   package.** The package exposes only
   `create_calendar_service(*, token_file)`, which talks to Google. Team 5's
   own `tests/test_shared_conformance.py` runs the suite against their
   FastAPI `app` package, using test helpers that exist only in their
   repository (`tests.conformance_support`), and not against the installed
   `team5_calendar` package. There is no controlled provider we can
   select from the package, so we record this as not run instead of faking a
   pass. Their repository's evidence is in `docs/SHARED_CONTRACT_VERIFICATION.md`
   at the pinned commit. Our in-memory fake calendar will run the suite
   (WP4).
6. Both implementation pins are commits on unmerged branches
   (`hw3/calendar-package` and `shreyas-hw3-shared-contract`). They can become
   unreachable if those branches are rebased or deleted. Switch each to an
   immutable tag as soon as one exists.

## Commands

Scratch `pyproject.toml` (in an empty directory outside the repository):

```toml
[project]
name = "wp1-install-check"
version = "0.0.0"
requires-python = ">=3.14"
dependencies = [
    "issue-tracker-contract @ git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@issue-tracker-contract-v1.1.0#subdirectory=contract",
    "team-4-issue-tracker-trello",
    "calendar-contract",
    "team5-calendar",
    "cloud-storage-api",
    "dropbox-client-impl",
]

[tool.uv]
package = false
override-dependencies = [
    "calendar-contract @ git+https://github.com/Airbus-A330/OSPSD-Calendar.git@v0.1.0",
]

[tool.uv.sources]
team-4-issue-tracker-trello = { git = "https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git", tag = "team-4-issue-tracker-trello-v1.0.0", subdirectory = "team-4-trello-impl" }
calendar-contract = { git = "https://github.com/Airbus-A330/OSPSD-Calendar.git", tag = "v0.1.0" }
team5-calendar = { git = "https://github.com/juno-junhyuk-lee/OSPSD-Team-5.git", rev = "b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2", subdirectory = "packages/team5-calendar" }
cloud-storage-api = { git = "https://github.com/Dayana20/ospsd26-cloud-storage.git", tag = "0.3.1", subdirectory = "src/cloud_storage_api" }
dropbox-client-impl = { git = "https://github.com/Shreyas191/OSPSD-Team-3.git", rev = "6b5175608416bd5f56793207d968f96b62730d51", subdirectory = "src/dropbox_client_impl" }
```

```
$ uv sync --python 3.14
Using CPython 3.14.8
Creating virtual environment at: .venv
...
 + issue-tracker-contract==1.1.0 (from git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@18fb7ae6f9c9cf5dbfed7208114d036a582ba54c#subdirectory=contract)
 + team-4-issue-tracker-trello==1.0.0 (from git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@c88ce2fd287f2524ba09ad5d94faf3983fdac08d#subdirectory=team-4-trello-impl)
 + team5-calendar==0.1.0 (from git+https://github.com/juno-junhyuk-lee/OSPSD-Team-5.git@b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2#subdirectory=packages/team5-calendar)
...
```

Resolved git sources in the scratch `uv.lock`:

```
https://github.com/Airbus-A330/OSPSD-Calendar.git?tag=v0.1.0#db73e6261a93b0c3cc6aa0a48c4687f742aacc9b
https://github.com/Dayana20/ospsd26-cloud-storage?subdirectory=src%2Fcloud_storage_api&tag=0.3.1#664afc92f7cc0b2ee51cde6ddfa088cc3b37206c
https://github.com/juno-junhyuk-lee/OSPSD-Team-5.git?subdirectory=packages%2Fteam5-calendar&rev=b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2#b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2
https://github.com/Shreyas191/OSPSD-Team-3.git?subdirectory=src%2Fcloud_storage_client_api&rev=6b5175608416bd5f56793207d968f96b62730d51#6b5175608416bd5f56793207d968f96b62730d51
https://github.com/Shreyas191/OSPSD-Team-3.git?subdirectory=src%2Fdropbox_client_impl&rev=6b5175608416bd5f56793207d968f96b62730d51#6b5175608416bd5f56793207d968f96b62730d51
https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git?subdirectory=contract&rev=issue-tracker-contract-v1.1.0#18fb7ae6f9c9cf5dbfed7208114d036a582ba54c
https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git?subdirectory=team-4-trello-impl&tag=team-4-issue-tracker-trello-v1.0.0#c88ce2fd287f2524ba09ad5d94faf3983fdac08d
```

Import check:

```
$ .venv/bin/python -I -c "import calendar_contract, team5_calendar, team_4_issue_tracker_trello, issue_tracker_contract, cloud_storage_api, dropbox_client_impl; ..."
3.14.8 (main, Oct  1 2026, 20:50:07) [Clang 22.1.3 ]
issubclass Dropbox->DocumentStorage True
Trello isinstance IssueProvider True
calendar-contract 0.1.0
cloud-storage-api 0.2.0
issue-tracker-contract 1.1.0
team-4-issue-tracker-trello 1.0.0
team5-calendar 0.1.0
dropbox-client-impl 0.1.0
```

## Product lockfile (WP2)

`product/pyproject.toml` declares the same pins (the tracker contract as a
PEP 508 URL, the rest through `[tool.uv.sources]`, plus the calendar
override). Its `product/uv.lock` was checked by copying `pyproject.toml`,
`uv.lock`, `README.md` and `src/` to an empty directory outside the
repository and installing exactly what the lock records, as the image does:

```
$ uv sync --frozen --no-dev --python 3.14
Using CPython 3.14.8
 + calendar-contract==0.1.0 (from git+https://github.com/Airbus-A330/OSPSD-Calendar.git@db73e6261a93b0c3cc6aa0a48c4687f742aacc9b)
 + cloud-storage-api==0.2.0 (from git+https://github.com/Dayana20/ospsd26-cloud-storage@664afc92f7cc0b2ee51cde6ddfa088cc3b37206c#subdirectory=src/cloud_storage_api)
 + cloud-storage-client-api==0.1.0 (from git+https://github.com/Shreyas191/OSPSD-Team-3.git@6b5175608416bd5f56793207d968f96b62730d51#subdirectory=src/cloud_storage_client_api)
 + dropbox-client-impl==0.1.0 (from git+https://github.com/Shreyas191/OSPSD-Team-3.git@6b5175608416bd5f56793207d968f96b62730d51#subdirectory=src/dropbox_client_impl)
 + issue-tracker-contract==1.1.0 (from git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@18fb7ae6f9c9cf5dbfed7208114d036a582ba54c#subdirectory=contract)
 + ospsd-team4-product==0.1.0 (from file:///...)
 + team-4-issue-tracker-trello==1.0.0 (from git+https://github.com/Tyler-Arciniaga/OSPSD-Team-4.git@c88ce2fd287f2524ba09ad5d94faf3983fdac08d#subdirectory=team-4-trello-impl)
 + team5-calendar==0.1.0 (from git+https://github.com/juno-junhyuk-lee/OSPSD-Team-5.git@b1d6e7fa2bea4cfbab16d6e5a195a2a445885ba2#subdirectory=packages/team5-calendar)

$ .venv/bin/python -I -c "import calendar_contract, team5_calendar, team_4_issue_tracker_trello, issue_tracker_contract, cloud_storage_api, dropbox_client_impl, ospsd_team4_product.main; ..."
imports ok
cloud-storage-api reports 0.2.0
```

`calendar-contract` resolves to `db73e62` and `cloud-storage-api` to
`664afc9` (reporting `0.2.0`, expected), the same commits as the scratch
check above.

The product test `tests/test_config.py::test_real_partner_import_paths_resolve`
also checks that the deployed import paths
(`team_4_issue_tracker_trello:TrelloIssueProvider`,
`team5_calendar:create_calendar_service`,
`dropbox_client_impl:DropboxDocumentStorage`) resolve, and that Team 5's
factory makes `CALENDAR_TOKEN_FILE` required.

## Image build (WP2)

Built locally on 2026-11-15 with Docker 28.0.4 from the uncommitted working
tree on `feat/ospsd-team4-product`. `GIT_SHA` was the branch base,
`c88ce2f`. The release pipeline will rebuild from an exact commit in WP7.

```
$ docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t ospsd-team4-product:wp2 product
...
#22 naming to docker.io/library/ospsd-team4-product:wp2 done
```

Checks run against that image:

| Check | Result |
| --- | --- |
| Start with no configuration | exits 3, logs `{"event": "startup_config_invalid", "message": "Missing required configuration: TRACKER_PROVIDER"}` |
| Start with Team 5's calendar but no `CALENDAR_TOKEN_FILE` | exits 3, `Missing required configuration: CALENDAR_TOKEN_FILE` |
| Start with the deployed import paths and a placeholder token file mounted read-only at `/run/secrets/token.json` | `GET /healthz` returns `{"status":"ok"}`; `GET /readyz` returns 200 `{"status":"ready","checks":{"config":"ok"},"commit":"c88ce2fd287f2524ba09ad5d94faf3983fdac08d"}` |
| Partner imports and time zone data inside the image | `import ospsd_team4_product, team5_calendar, dropbox_client_impl, team_4_issue_tracker_trello` and `ZoneInfo("America/New_York")` succeed |
| Runs as an unprivileged user | uid `10001` |
| No build tools in the runtime image | `git` and `uv` absent |
| Image size | about 98 MB |

Startup takes a few seconds (importing the Google and Dropbox SDKs) before
uvicorn accepts connections. Health checks in WP7 need to allow for that.
No provider is constructed or contacted at startup, so the placeholder token
was never read.
