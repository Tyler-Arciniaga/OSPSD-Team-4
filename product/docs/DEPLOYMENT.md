# Deploying the product service

How `ospsd-team4-product` gets from a commit on `main` to a running Render
service, and the one-time setup only a human can do. The pipeline is a copy
of the HW2 service's (`docs/DEPLOYMENT.md`), parametrized for the product.
It reuses the HW2 release tooling and never touches the HW2 service, its
Terraform state or its GitHub environments.

## Overview

| Piece | Product | HW2 counterpart |
| --- | --- | --- |
| Image | `ghcr.io/tyler-arciniaga/ospsd-team4-product`, built from `product/` | `ospsd-team4-issue-service` |
| Checks | `.github/workflows/ci-product.yml` | `ci.yml` |
| Release | `.github/workflows/release-product.yml` | `release.yml` |
| Image check before deploy | `product/scripts/disposable_env.sh` | `scripts/disposable_env.sh` |
| Smoke check after deploy | `product/scripts/smoke_check.py` | `scripts/smoke_check.sh` |
| Terraform | `product/infra/`, HCP workspace `ospsd-team4-product-render` | `infra/`, `ospsd-team4-render` |
| Terraform workflows | `product-terraform-plan.yml`, `product-terraform-apply.yml` | `terraform-plan.yml`, `terraform-apply.yml` |
| GitHub environments | `product-production`, `product-disposable`, `product-infrastructure` | `render-*` |
| Concurrency group | `render-product-<target>` | `render-<target>` |

Release stages: `resolve`, `ci` (product checks on the exact commit),
`build` (push once, then check that image by digest in a disposable
container), `deploy` (Render runs `<image>@<digest>`, `/readyz` must report
the commit), `smoke` (`POST /v1/focus-day` against the deployed service),
`record` (a GitHub Deployment, `success` only if the smoke check passed).

The release runs on pushes to `main` that change `product/` or the product
workflows. A push that changes neither starts no product release. Every
merge to `main` still starts the HW2 release, which redeploys the unchanged
HW2 image (accepted).

### Production and disposable

| | Production (`ospsd-team4-product`) | Disposable (`ospsd-team4-product-disposable`) |
| --- | --- | --- |
| Providers | Our Trello package, Team 5's calendar, Team 3's Dropbox storage | The in-memory fakes, through the same `*_PROVIDER` variables |
| Credentials | The linked environment group | None; not linked to the group |
| Smoke check | A fully booked day: reads the tracker, writes `/standups/2000-01-03-release-smoke-<commit>-<UTC time>.md` (a new doc each run, since an existing doc stops a run, F5), creates no calendar event | A full run, the same request again (must be 412 at `check_doc`), and a replay of the first |
| Created by | Always | `disposable` ticked on the apply workflow |

The production smoke check cannot exercise the calendar without creating an
event the service has no way to remove, so it books no block. The calendar
is checked live only by a real request.

Each production smoke run leaves one small doc in `/standups`
(`2000-01-03-release-smoke-*.md`). They are harmless; remove them by hand
from the Dropbox test account when they pile up.

### Configuration and secrets

| Value | Kind | Where it is set |
| --- | --- | --- |
| `TRACKER_PROVIDER`, `CALENDAR_PROVIDER`, `STORAGE_PROVIDER`, `CALENDAR_TOKEN_FILE`, `DEFAULT_TIMEZONE` | Configuration | `product/infra/main.tf` |
| `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_LIST_ID` | Secret | Product Render environment group, by hand |
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` | Secret | Product Render environment group, by hand |
| `google-calendar-token.json` (Team 5's OAuth token) | Secret file | Product Render environment group, by hand; mounted at `/etc/secrets/google-calendar-token.json` |
| `STATE_DB_URL` (production) | Secret | Product Render environment group, by hand: the state database's Internal Database URL ([State database](#state-database)) |
| `STATE_DB_URL` (disposable) | Configuration | `product/infra/main.tf`: `memory://`, the in-memory store |
| `PRODUCT_SECRETS_ENV_GROUP_ID` | Configuration | GitHub repository variable |
| `RENDER_SERVICE_ID`, `SERVICE_URL` | Configuration | Variables of `product-production` and `product-disposable` |
| `RENDER_API_KEY`, `TF_API_TOKEN`, `RENDER_OWNER_ID` | Secret / configuration | GitHub, as for HW2 |

No credential is in the repository, the image or Terraform state. The token
file is mounted at run time, never baked into the image (`.dockerignore`
excludes every `*.json`, and the disposable check fails if a credential file
is found in the image).

## One-time setup (human only)

Do these in order. Nothing here can be automated: each needs an account
owner or a repository admin.

### 0. Before merging

- [ ] Confirm with course staff that adding product workflows under
      `.github/workflows/` and redeploying the unchanged HW2 service on merge
      is acceptable (brief section 2G). This PR also makes two small,
      backward-compatible edits to HW2 tooling: `scripts/release.py` accepts
      the product environment names, and `scripts/build_image.sh` takes an
      optional build context.

### 1. HCP Terraform

- [ ] In the organization `ospsd-team-4`, create the workspace
      `ospsd-team4-product-render` with the **CLI-driven workflow**.
- [ ] **Settings, General, Execution Mode: Local** (as for HW2).

### 2. Google Calendar test account (Team 5's calendar)

- [ ] Create a dedicated Google test account. Do not use a personal one.
- [ ] In Google Cloud, create a project, enable the **Google Calendar API**,
      configure the OAuth consent screen in **Testing** and add the test
      account as a test user.
- [ ] Create an OAuth client of type **Desktop app** and download its JSON.
      Keep it outside the repository.
- [ ] From `product/`, write the token file outside the repository:

  ```sh
  uv run python -m team5_calendar.authorize \
    --client-secrets ~/.config/ospsd-team4/google-client.json \
    --token-file ~/.config/ospsd-team4/google-calendar-token.json
  ```

- [ ] Testing-mode grants can expire (Google documents 7 days for refresh
      tokens of apps in Testing). Re-authorize and replace the secret file
      shortly before any demo or grading date. An expired token shows up as
      `service_not_configured` on the `create_events` step.

### 3. Dropbox (Team 3's storage)

Already done for the live round trip
(`product/docs/evidence/dropbox-roundtrip.md`): the app key and secret, and
the refresh token in `~/.config/ospsd-team4/dropbox_token.json`. Copy the
token without printing it: `jq -r .refresh_token ~/.config/ospsd-team4/dropbox_token.json | pbcopy`.

- [ ] Create the doc folder `standups` at the root of the Dropbox app, the
      root the service's paths are relative to (for an app-folder app,
      `Apps/<app name>/standups`), in the Dropbox web UI. Every doc is
      written flat into it, as `/standups/<YYYY-MM-DD>-<assignee>.md`. The
      storage contract does not promise that `create` makes missing
      folders, so the service does not rely on it: if the folder is
      missing, `write_standup_doc` fails with 503 `service_not_configured`
      after the events are booked. Create the folder, then replay the
      request.

### 4. Trello

- [ ] Create a separate Trello list for the product (owner decision): the
      service plans every issue in it. The HW2 test account's key and a
      30-day token can be reused (`docs/DEPLOYMENT.md`, prerequisites).

### 5. Render environment group

- [ ] **Dashboard, Environment Groups, New**: `ospsd-team4-product-secrets`.
- [ ] Add the six variables: `TRELLO_API_KEY`, `TRELLO_TOKEN`,
      `TRELLO_LIST_ID` (the product list), `DROPBOX_APP_KEY`,
      `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`.
- [ ] Add a **secret file** named `google-calendar-token.json` with the token
      file's contents.
- [ ] Create the state database and add `STATE_DB_URL`
      ([State database](#state-database), steps 1 and 2).
- [ ] Note the group's `evg-...` ID.

### 6. GitHub (repository admin)

- [ ] Repository variable `PRODUCT_SECRETS_ENV_GROUP_ID` = the `evg-...` ID.
      The repository secrets `RENDER_API_KEY`, `TF_API_TOKEN` and variable
      `RENDER_OWNER_ID` used by HW2 pull request plans are reused.
- [ ] Environment `product-production`: deployment branches `main` only, no
      required reviewers. Secret `RENDER_API_KEY`; variable `RENDER_OWNER_ID`.
- [ ] Environment `product-disposable`: no branch restriction, no reviewers.
      Secret `RENDER_API_KEY`; variable `RENDER_OWNER_ID`.
- [ ] Environment `product-infrastructure`: required reviewers, deployment
      branches `main`. Secrets `RENDER_API_KEY`, `TF_API_TOKEN`; variable
      `RENDER_OWNER_ID`.

### 7. Bootstrap (first deploy)

The service needs an image that exists, and the release needs the service's
ID: the first release therefore stops after pushing the image.

1. [ ] **Merge the PR.** `Release (product)` runs `ci` and `build` (the image
   is pushed and checked), then `deploy` fails on purpose: "The
   product-production environment is missing: RENDER_SERVICE_ID SERVICE_URL".
   Copy the digest from the run summary's **Artifact** table.
2. [ ] **Make the package public**: GitHub, your profile, Packages,
   `ospsd-team4-product`, Package settings, Change visibility, Public. Check
   from a logged-out Docker:
   `docker logout ghcr.io && docker pull ghcr.io/tyler-arciniaga/ospsd-team4-product@<digest>`.
3. [ ] **Apply.** Actions, **Terraform apply (product)**, Run workflow on
   `main`, with `bootstrap_image_digest` = that digest and `disposable`
   ticked if you want the disposable service. Read the plan first (posted on
   the PR by **Terraform plan (product)**): it must only **create**
   `render_web_service.product`, the link, and optionally the disposable
   service. Nothing of HW2 appears in it.
4. [ ] **Variables.** Copy the **Services** table from the apply run into
   `RENDER_SERVICE_ID` and `SERVICE_URL` of `product-production` (and
   `product-disposable`).
5. [ ] **First release.** Re-run the failed jobs of step 1's run. Expected:
   `deploy` goes live, `/readyz` reports the commit, `smoke` passes (live
   mode), `record` records `success`.
6. [ ] **Idempotency.** Run the apply again with nothing filled in: both plans
   show *No changes*.
7. [ ] **Disposable** (if created): Actions, **Release (product)**, target
   `disposable`. The smoke check runs a full focus day and a replay on the
   in-memory providers.

If step 5 fails at startup with `CALENDAR_TOKEN_FILE does not name a
readable file`, the secret file is missing, misnamed, or not readable by the
image's non-root user (uid 10001). That last case is not yet verified on
Render; the service log names the variable, never its value.

## State database

Approved plans, remaining work and the completion log live in one Postgres
database that every instance of the service shares
([DESIGN.md, State store](DESIGN.md#state-store)). It is created by hand,
not by Terraform: a Terraform-managed database would put its password in
Terraform state. The service creates its own tables on startup, so the
database only has to exist.

1. [ ] **Create it.** Render dashboard, **New**, **Postgres**:
   - Name `ospsd-team4-product-state`, database `focus_day`, user
     `focus_day` (any names work; they end up in the URL).
   - Region **Virginia (US East)**, the service's region
     (`product/infra/variables.tf`). The service reaches it over Render's
     private network only if both are in the same region.
   - Instance type **Free**, PostgreSQL version 17 (CI and
     `docker-compose.yml` use 17).

   A workspace may have only one free database at a time. If the workspace
   already has one, delete it first or choose a paid type.
2. [ ] **Give the service its URL.** On the database's page, **Connect**,
   copy the **Internal Database URL** (`postgresql://focus_day:...@dpg-...-a/focus_day`;
   no `.render.com` host). Add it to the environment group
   `ospsd-team4-product-secrets` as `STATE_DB_URL`. Do not use the External
   URL: it goes over the internet and is not needed.
3. [ ] **Check it.** After the next deploy, the service log has no
   `startup_config_invalid` line, and a `POST /v1/focus-day` response ends
   with `{"name": "save_plan", "status": "ok", ...}`. The release's smoke
   check does this: it saves an empty plan for `release-smoke` on
   2000-01-03 each time, replacing the previous one.

**Order for an existing deployment.** The release that adds the store (#85)
refuses to start without `STATE_DB_URL`. Do steps 1 and 2 **before** merging
it: the running, older image ignores the extra variable, so adding it first
is safe. If the release went out first, `deploy` fails with
`Missing required configuration: STATE_DB_URL` in the log and the previous
deploy keeps serving; do steps 1 and 2, then re-run the failed release.

**Renewal.** Render's free databases expire 30 days after creation and are
deleted 14 days after that unless upgraded ([Render: Deploy for
Free](https://render.com/docs/free)). Before the expiry date shown on the
database's page, either upgrade it to a paid type (keeps the data) or
create a new free one and replace `STATE_DB_URL` (loses every saved plan,
remaining share and log entry; the service recreates the empty tables).
While the database is down or expired, a running service fails each request
at the `save_plan` step (500, `retryable`), and a new deploy or restart
cannot start (`Cannot connect to state database`), so Render keeps the
previous deploy serving.

**Rollback.** Rolling back to a release from before #85 leaves the
database untouched; that release ignores `STATE_DB_URL` and saves nothing.
Rolling forward again resumes saving. No migration is involved: the tables
are only ever created, never altered.

## Verify locally

```sh
cd product
uv run pytest                                  # includes the smoke-check tests
# The state store tests need a Postgres to wipe (README.md, Checks):
TEST_STATE_DB_URL=postgresql://focus_day:focus_day@localhost:55432/focus_day_test uv run pytest
cd .. && product/scripts/disposable_env.sh     # build the image and check it (starts its own Postgres)
cd product/infra && terraform init -backend=false && terraform validate && terraform test
```

## Roll back

`uv run python -m scripts.release known-good --repo Tyler-Arciniaga/OSPSD-Team-4 --environment product-production`
(from the repository root, with a `GITHUB_TOKEN`) prints the last successful
release. Then **Release (product)**, Run workflow on `main`, target
`production`, with that `commit` and `digest`. The rollback is refused unless
that pair was recorded as a success. A rollback does not undo calendar events
or docs a faulty release created.

## Tear down

- Disposable service: run the apply with `disposable` unticked.
- Product service: `terraform destroy` from `product/infra` (logged in with
  `terraform login`, `RENDER_API_KEY` and `RENDER_OWNER_ID` exported), or
  delete it in the Render dashboard and then remove it from state. There is
  no destroy workflow for the product.
- By hand: the environment group, the state database
  (`ospsd-team4-product-state`; deleting it deletes every saved plan), the
  GitHub environments and variable, the HCP workspace, the Google OAuth
  client and token, the Dropbox app.
