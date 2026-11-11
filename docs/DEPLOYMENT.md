# Deployment

How the service is built, released, checked, rolled back and torn down. A
teammate should be able to follow this from a fresh checkout without asking
anyone. If you had to ask something, add it to the
[question log](#questions-asked-during-the-walkthrough): each question is a
gap in this document.

## Overview

```
pull request
    │  CI: lint, format, types, tests, conformance against the in-memory
    │  provider, image build; Terraform fmt/validate/test (+ plan if infra/ changed)
    ▼
merge to main (branch protection: `checks` must pass)
    │
    ▼  release workflow, no manual step, one release at a time
 1 ci      the same checks again, on the merge commit
 2 build   build the image once, push ghcr.io/…:<commit>, take its digest,
           check that exact image (pulled by digest) in a disposable container
 3 deploy  skip if main has moved on; point Render at <image>@<digest>,
           wait until live, /readyz must report the commit
 4 smoke   conformance suite against the live service and the real Trello list
 5 record  GitHub Deployment: commit, digest, run, deploy window;
           success only if the smoke check passed
    ▼
caller → https://<service>.onrender.com  (/healthz, /readyz, /issues)
```

| Piece | Where it lives | Created by |
| --- | --- | --- |
| Artifact | Container image in GitHub's container registry, `ghcr.io/tyler-arciniaga/ospsd-team4-issue-service`, tagged with the full commit SHA and **deployed only by digest** (`@sha256:…`) | The release workflow, once per release |
| Release record | GitHub **Deployments** (repository → Environments, or the API), environments `render-production` and `render-disposable` | The release workflow's `record` stage |
| Infrastructure description | `infra/*.tf` in this repository | Reviewed pull requests |
| Infrastructure state | HCP Terraform, org `ospsd-team-4`, workspace `ospsd-team4-render` | Terraform |
| Web service and secrets link | Render, service `ospsd-team4-issue-tracker`, region Virginia, free plan; optionally `ospsd-team4-issue-tracker-disposable` | Terraform apply workflow |
| Provider credentials | Render environment group (ID in `infra/release.tf`) | By hand, once |
| Automation | `.github/workflows/`: `ci.yml`, `release.yml`, `terraform-plan.yml`, `terraform-apply.yml`, `terraform-destroy.yml` | Reviewed pull requests |

### Configuration and secrets

Configuration is supplied separately from the image: the same image runs in
production, in the disposable service and in a local container.

| Value | Kind | Where it is set | Who can read it |
| --- | --- | --- | --- |
| `ISSUE_PROVIDER`, `IDEMPOTENCY_DB_PATH` | Configuration | `infra/main.tf` (service variables) | Anyone with repository access |
| Image repository, first digest, disposable toggle, environment group ID | Configuration | `infra/release.tf` | Anyone with repository access |
| `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_LIST_ID` (or `LINEAR_API_KEY`, `LINEAR_TEAM_ID`) | Secret | Render environment group, by hand | Render workspace members; the running service |
| `RENDER_API_KEY` | Secret | GitHub environment secrets (`render-production`, `render-disposable`, `render-infrastructure`) and one repository secret for PR plans | Its owner; the workflows using those environments |
| HCP Terraform API token (`TF_API_TOKEN`) | Secret | GitHub: `render-infrastructure` environment secret and one repository secret for PR plans; `~/.terraform.d` via `terraform login` locally | Its owner; the Terraform workflows |
| `RENDER_OWNER_ID` | Configuration | GitHub environment variables and one repository variable | Repository collaborators |
| `RENDER_SERVICE_ID`, `SERVICE_URL` | Configuration | GitHub environment variables of `render-production` and `render-disposable` | Repository collaborators |

Secrets are never in the repository, the image or Terraform state. Terraform
only links the environment group by its ID, and that link's state holds the
IDs alone. The provider marks every service variable's value as sensitive, so
plans show `(sensitive value)` even for plain configuration. State and plan
files are still treated as sensitive: they stay out of the repository
(`.gitignore`) and out of CI artifacts.

**Least privilege.** Every workflow defaults to `contents: read`. Only the
`build` job may push packages, only the `record` job may write deployments,
and only the `resolve` job and the apply workflow may read them. The smoke
check runs with no secrets at all: it goes through the service's public API,
and the service holds the provider credentials. Render API keys cannot be
scoped to one service, so whoever holds one can change any service in the
workspace; that is why the key lives only in GitHub environments limited to
`main`, plus one repository secret the PR plan needs (a residual risk:
same-repository pull requests can reach it; pull requests from forks
cannot).

## Prerequisites (by hand, once)

These steps cannot be automated. Whoever owns the accounts does them once.
They are already done for this team, except where
[the Level 2 checklist](#level-2-checklist-repository-admin) says otherwise;
a teammate only needs the [walkthrough access](#teammate-walkthrough).

1. **HCP Terraform** (app.terraform.io, free):
   - Create the organization `ospsd-team-4`.
   - Create the workspace `ospsd-team4-render` with the **CLI-driven workflow**.
   - Under **Settings → General → Execution Mode**, choose **Local**. Plans and applies then run where the Render key is, and HCP only stores and locks the state.
   - Invite teammates under **Organization settings → Users**.
2. **Render** (render.com, free, no payment method):
   - Create an API key under **Account Settings → API Keys**.
   - Note the workspace ID (`tea-…` or `usr-…`) from the workspace settings.
3. **Provider credentials** in a Render **environment group** (**Dashboard → Environment Groups → New**):
   - Trello: `TRELLO_API_KEY`, `TRELLO_TOKEN` and `TRELLO_LIST_ID`. Use a **dedicated test list**: every release's smoke check creates and deletes real cards there.
   - Create the token with `expiration=30days`, from the test account: `https://trello.com/1/authorize?expiration=30days&scope=read,write&response_type=token&name=OSPSD-Team4-Render&key=<TRELLO_API_KEY>`. The README's one-day token would expire under the running service.
   - Put the group's `evg-…` ID in `infra/release.tf`.
4. **GitHub**: the environments, secrets and variables in the
   [Level 2 checklist](#level-2-checklist-repository-admin).

## Merge gate

A change reaches `main` only through a pull request whose checks passed.

| Check | Workflow | Runs on | Required to merge |
| --- | --- | --- | --- |
| `checks`: passes only if every job below passed | CI (`ci.yml`) | every push and pull request | **yes** |
| `tests`: `ruff check`, `ruff format --check`, mypy, pytest | CI | every push and pull request | through `checks` |
| `conformance-controlled`: the conformance suite against the real service backed by an in-memory provider | CI | every push and pull request | through `checks` |
| `docker-image`: builds the image (the release workflow checks the pushed image in a disposable container) | CI | every push and pull request | through `checks` |
| `plan`: `terraform fmt`, `validate`, `test`, plus `plan` posted on the pull request when it changes `infra/` or a Terraform workflow | Terraform plan | every pull request | no; review the posted plan |

Pull requests from forks run with no secrets: every workflow uses
`pull_request`, never `pull_request_target`. They get every CI check, and
Terraform formatting, validation and tests without the plan. They cannot
deploy: only pushes to `main` and runs started by hand release anything, and
the deploy credentials live in environments limited to `main`.

### Branch protection (repository admin, once)

Only a repository admin can do this. Do it after one CI run has finished on a
branch, so GitHub offers `checks` as a status name.

1. **Settings → Branches → Add classic branch protection rule**, branch name
   pattern `main`.
2. Tick **Require a pull request before merging**. Set **Required approvals**
   to `0`: reviews are a team rule (AGENTS.md), not a setting.
3. Tick **Require status checks to pass before merging**, search for
   `checks` and add it (the one from **CI**). Leave the other CI jobs out:
   `checks` already requires them.
4. Tick **Do not allow bypassing the above settings**, so admins follow the
   same rule.
5. Leave **Allow force pushes** and **Allow deletions** unticked.
6. **Create**. Then check it works: a pull request with a failing test shows
   `checks` failing and the merge button blocked.

## Release pipeline

Every push to `main` runs **Release** (`release.yml`) with no approval step.
Each stage starts only if the one before passed.

| Stage | What it does | Fails the release when |
| --- | --- | --- |
| `resolve` | Decides what to release: the pushed commit, or a [rollback](#roll-back) target | A rollback target is not a recorded success; production from a branch other than `main` |
| `ci` | Runs `ci.yml` again on the exact commit (except the build-only image job: `build` builds the real image) | Any check fails |
| `build` | `scripts/build_image.sh`: builds the image once for linux/amd64 with `GIT_SHA` set, pushes it tagged `:<full commit>`, reads its digest back from the registry. Then `scripts/disposable_env.sh` pulls `<image>@<digest>` and checks it with fake configuration | The image does not build, or the pushed image fails a container check |
| `deploy` | Skips itself if `main` has moved on (pushes only). Otherwise `scripts/release.py deploy`: sets the Render service's image to `<image>@<digest>`, starts a deploy of exactly that digest, waits until it is live. Then `check-ready`: `/readyz` must be `ready` and report the commit | Render refuses or the deploy fails; the service never reports the commit; the environment is not set up |
| `smoke` | `scripts/smoke_check.sh`: the conformance suite against the live URL, which calls the real Trello list | Any conformance test fails |
| `record` | `scripts/release.py record`: a GitHub Deployment for the commit, with the image, digest, run link, Render deploy ID and deploy window. Status `success` only if `smoke` passed, otherwise `failure` with the failed stage | (never fails the release on its own) |

Nothing is rebuilt after `build`: every later stage, and any rollback, uses
the digest. No `latest` tag is ever pushed or deployed, and the deploy step
refuses anything that is not `sha256:<64 hex>`.

**What a failed smoke check does.** It stops the release and records it as
`failure`, so the previous `success` stays the
[known-good revision](#roll-back). It does **not** undo the deploy: Render
keeps running the faulty revision until someone rolls back. Automatic
rollback is out of scope for this level. The run summary names the failing
conformance tests.

**Leftover smoke-check data.** Every issue the suite creates is titled
`Conformance …` and deleted after its test. A delete that fails does not
fail the release; it is listed under **Leftover issues: remove by hand** in
the run summary, by ID. Delete those cards from the Trello test list.

## Find the commit behind the running service

Any of these, from cheapest:

1. `curl -sS https://ospsd-team4-issue-tracker.onrender.com/readyz` → its
   `commit` field. The image sets it from `GIT_SHA` at build time.
2. **Repository → Environments → render-production** (or the
   **Deployments** list): the newest entry with a green **success** shows the
   commit and links to the run. Its payload holds the digest:
   ```bash
   gh api "repos/Tyler-Arciniaga/OSPSD-Team-4/deployments?environment=render-production&per_page=5" \
     --jq '.[] | {created_at, sha, digest: .payload.digest, run: .payload.run_url}'
   ```
   Or the latest successful release only:
   ```bash
   GITHUB_TOKEN="$(gh auth token)" uv run --locked python -m scripts.release known-good \
     --repo Tyler-Arciniaga/OSPSD-Team-4 --environment render-production
   ```
3. The image itself records its commit:
   ```bash
   IMAGE=ghcr.io/tyler-arciniaga/ospsd-team4-issue-service@<digest>
   docker pull --quiet "$IMAGE" >/dev/null
   docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE"
   ```
4. Render dashboard → the service → **Events** lists each deploy; the
   service's image setting (**Settings → Image**) names the digest the
   release set.

All four must agree for a release recorded as `success`; `check-ready`
already compared the first with the release's commit.

## Concurrency

- **One release at a time per service.** The release workflow uses the
  concurrency group `render-production` (or `render-disposable`) and never
  cancels a running release.
- **Only the newest waiting release survives.** While a release runs, the
  next one waits. If a third arrives, GitHub cancels the waiting one and
  keeps the newest.
- **An older commit never overwrites a newer one.** Before deploying, a
  pushed release checks that its commit is still the tip of `main` and skips
  the deploy if not (the run summary says so). The newer commit's own release
  deploys instead. Skipped releases are not recorded.
- **Terraform apply and destroy share the production group**, so they never
  overlap a production release.
- **Caveat:** GitHub keeps only one *waiting* run per group, across all three
  workflows. Starting an apply while a release waits replaces that release
  (re-run it, or merge again); a merge while an apply waits replaces the
  apply (start it again). Do not start an apply while a release is queued.
- The disposable toggle is applied by the apply workflow, which holds the
  production lock, not the disposable one: do not apply it while a
  disposable release is running.

## Verify

```bash
URL=https://ospsd-team4-issue-tracker.onrender.com   # or SERVICE_URL of the environment
curl -sS "$URL/healthz"   # {"status":"ok"}
curl -sS "$URL/readyz"    # {"status":"ready", ..., "provider":"reachable","commit":"<released sha>"}
```

- `/healthz` is the liveness check Render uses. It does not contact the
  provider, so a provider outage never makes Render restart the service.
- `/readyz` is 200 whenever the service can serve. `provider` is information
  only: `unreachable` means the provider did not answer or rejected the
  credentials, which is worth checking in the environment group. See
  [Health checks](api.md#health-checks).
- A free service sleeps after 15 minutes without traffic, and the first
  request after that takes about a minute.

**Run the smoke check yourself.** It creates and deletes real cards in the
test list; it needs `uv` and no credentials:

```bash
scripts/smoke_check.sh "$URL" "$(curl -sS "$URL/readyz" | jq -r .commit)"
```

`scripts/conformance_remote.sh "$URL"` runs the same suite without checking
the commit.

- **Linear:** rerun the suite against a Linear deployment once Linear rename
  and delete are merged. To switch, set `issue_provider = "linear"` (see
  `infra/variables.tf`) and put `LINEAR_API_KEY` and `LINEAR_TEAM_ID` in the
  environment group.
- **Until then**, every issue created against Linear is reported as a
  `CleanupWarning`.

## Roll back

The **known-good revision** is the newest release recorded as `success` in
that environment. Rolling back redeploys its image digest: no rebuild, the
same deploy, readiness and smoke checks, and a new release record.

1. Find the known-good commit and digest:
   ```bash
   GITHUB_TOKEN="$(gh auth token)" uv run --locked python -m scripts.release known-good \
     --repo Tyler-Arciniaga/OSPSD-Team-4 --environment render-production
   ```
   (or read them from the newest green entry under **Environments →
   render-production**, as in [Find the commit](#find-the-commit-behind-the-running-service)).
2. **Actions → Release → Run workflow**: branch `main`, target
   `production`, and paste the `commit` and `digest`.
3. The `resolve` stage refuses the run unless that exact commit and digest
   were released to that environment and passed their smoke check. Then
   `deploy`, `smoke` and `record` run as for any release.
4. Confirm `/readyz` reports the restored commit and the run is recorded
   `success`.

Render keeps running the restored digest until the next release. The next
merge to `main` releases `main` as usual, faulty code included, so fix the
fault on `main` first (revert or fix forward) and merge that.

### What a rollback restores, and what it does not

| Restored | Not restored |
| --- | --- |
| The code: the exact image that passed its smoke check before | **Data the faulty release wrote to Trello** (cards created, renamed or deleted). See [Repair provider data](#repair-provider-data-after-a-faulty-release) |
| The service's image setting in Render (set to the same digest) | Values someone changed **by hand** in the Render environment group (credentials, list ID) |
| Terraform-managed configuration, if you also revert an `infra/` change and apply it (code rollbacks do not change Terraform) | **Idempotency keys**: the key store is not durable on the free plan, so every deploy, including a rollback, forgets them (DESIGN.md 5.5). A client retrying a create with an old key after the rollback may create a duplicate |
| | Anything outside this repository: Render dashboard edits, Trello board settings |

### Repair provider data after a faulty release

A rollback restores the code, not the data. To find what a faulty release
wrote:

1. **Get the window.** Open the faulty release's run summary (**Deployed**
   table, *Deploy window*) or its Deployment payload:
   ```bash
   gh api "repos/Tyler-Arciniaga/OSPSD-Team-4/deployments?environment=render-production&per_page=20" \
     --jq '.[] | {id, sha, started: .payload.deploy_started_at, finished: .payload.deploy_finished_at, run: .payload.run_url}'
   ```
   The faulty revision served from its `deploy_started_at` until the
   rollback's `deploy_finished_at`.
2. **List what changed in the test list during that window.** In a shell,
   type the Trello key and token (never put them in a file or the
   repository), then ask Trello for the list's card actions in the window:
   ```bash
   read -rs TRELLO_API_KEY; read -rs TRELLO_TOKEN; export TRELLO_API_KEY TRELLO_TOKEN
   LIST=<TRELLO_LIST_ID>; SINCE=<faulty deploy_started_at>; BEFORE=<rollback deploy_finished_at>
   curl -sS -H "Authorization: OAuth oauth_consumer_key=\"$TRELLO_API_KEY\", oauth_token=\"$TRELLO_TOKEN\"" \
     "https://api.trello.com/1/lists/$LIST/actions?filter=createCard,updateCard,deleteCard&since=$SINCE&before=$BEFORE&limit=1000" \
     | jq -r '.[] | [.date, .type, .data.card.id, .data.card.name] | @tsv'
   ```
   A Trello card ID also encodes its creation time (its first 8 hex digits
   are Unix seconds), which confirms when a card was made.
3. **Separate smoke data from real data.** Cards titled `Conformance …` came
   from smoke checks; delete any that remain (the run summary's *Leftover
   issues* lists those it knew about).
4. **Compare the rest with what should have happened.** For each card created
   or renamed in the window, check its title against what the caller sent
   (the caller's own records, or the issue it was meant to track). Fix a
   wrong title through the restored service
   (`PATCH /issues/{id}` with `If-Match`), and delete cards that should not
   exist (`DELETE /issues/{id}`). Look for duplicates from retried creates:
   idempotency keys were forgotten at both deploys.
5. **Write down what you changed** in the pull request or issue that fixes
   the fault.

## Infrastructure changes

Terraform changes are reviewed in a pull request (its plan is posted as a
comment) and applied by hand after merge. Applying deploys no code.

1. **Actions → Terraform apply → Run workflow**, branch `main`.
   - **disposable**: tick to keep or create the disposable service; leave
     unticked otherwise. **Unticked removes it if it exists**, so the plan
     shows a destroy.
   - **bootstrap_image_digest**: leave empty, except for the very first apply
     ([Bootstrap](#bootstrap-docker-service-to-image-service-once)). Empty
     means the latest successful production release's digest, used only by a
     service the apply creates or converts; services that already run an
     image keep their digest.
2. A required reviewer on `render-infrastructure` approves after reading the
   plan in the run.
3. The workflow applies exactly that plan, prints each service's ID and URL,
   then **plans again and fails unless it shows no changes**.

Releases change the running digest without Terraform: `infra/main.tf`
ignores later digest changes, so a plan after a release shows *No changes*.
`infra/tests/` checks this with a mock provider (`terraform test`, run on
every pull request).

### Run Terraform from your machine (optional)

The workflows are the normal path. To run a plan or the tests yourself, you
need Terraform 1.17.x; a plan also needs HCP access and a Render API key:

```bash
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
cd infra
terraform init -backend=false && terraform test   # no credentials needed
terraform login                 # saves an HCP token in ~/.terraform.d
export RENDER_API_KEY=...       # type it; never put it in a file
export RENDER_OWNER_ID=tea-...
terraform init
terraform plan                  # read-only
```

Do not run `terraform apply` locally: the workflow applies only reviewed
configuration from `main`, with approval, and checks the second plan.

## Disposable container check

To check that the artifact runs anywhere, without touching the deployment
or a real provider account:

```bash
scripts/disposable_env.sh
```

It builds the image from `HEAD`, runs it with fake configuration, and checks
these points. Then it removes the container and image.

To check an image that was already pushed, pin it by digest; the script
refuses a tag:

```bash
IMAGE_REF=ghcr.io/tyler-arciniaga/ospsd-team4-issue-service@sha256:<digest> \
EXPECTED_COMMIT=<commit> scripts/disposable_env.sh
```

- The image records its commit.
- It runs as a non-root user.
- It contains no `.env`, `.git` or Terraform files.
- With a variable missing, it exits and names the variable without printing
  any value.
- `/healthz` and `/readyz` return 200 while the provider is unreachable.
- The logs contain no credential.

The release workflow runs it on every release, on the image it just pushed,
pulled back from the registry by digest, before anything is deployed. Pull
requests only build the image.

## Bootstrap: Docker service to image service (once)

Level 1 had Render build the Dockerfile at a pinned commit. Level 2 runs the
image the pipeline built. The switch keeps the service, its URL and its
secrets link, and has no downtime: the running Docker deploy keeps serving
until step 6 replaces it. Idempotency keys are lost at that deploy, as on
every free-plan deploy.

Why each step is needed (checked against the Render provider 1.9.1
source and Render's API docs):

- Render's provider supports image services pinned by digest, and switching
  the runtime is an **in-place update** (no replacement, same URL). Render's
  docs do not say whether the API accepts the switch; step 4 is where that is
  shown. If it refuses, the apply fails and the service is unchanged.
- An image service needs an image that already exists and can be pulled:
  Render pulls a public GHCR package without credentials.
- With `num_instances` set, the provider calls Render's scale endpoint on
  every update, which the free plan refuses, so `infra/main.tf` leaves it
  unset (free services run one instance anyway).
- Deploying a digest through Render's API does not change the service's
  image setting, so `scripts/release.py deploy` sets it first; Terraform
  ignores later digest changes so plans stay clean.

Steps:

1. Do the [Level 2 checklist](#level-2-checklist-repository-admin) items 1
   to 3 (environments, secrets, variables; leave `RENDER_SERVICE_ID` and
   `SERVICE_URL` unset for now).
2. **Merge the Level 2 pull request.** The release passes `ci` and `build`
   (the image is pushed), then `deploy` **fails on purpose**: "The
   render-production environment is missing: RENDER_SERVICE_ID SERVICE_URL…
   nothing was deployed". Production keeps serving the old commit. Copy the
   digest from the run summary's **Artifact** table.
3. **Check the package is public.** A package pushed from a public
   repository's workflow may already be. If not: GitHub → your profile →
   **Packages** → `ospsd-team4-issue-service` → **Package settings** →
   **Change visibility** → Public. Either way, check from a logged-out Docker:
   `docker logout ghcr.io && docker pull ghcr.io/tyler-arciniaga/ospsd-team4-issue-service@<digest>`.
4. **Apply.** **Actions → Terraform apply → Run workflow** on `main`,
   `disposable` unticked, `bootstrap_image_digest` = that digest. The
   approval comes **before** the job plans, and the job applies its plan
   straight away, so read the plan first: the one the Terraform plan
   workflow posted on the merged pull request, or a
   [local plan](#run-terraform-from-your-machine-optional) with
   `-var bootstrap_image_digest=<digest>`. `render_web_service.api` must be
   **updated in place** (runtime docker → image) and nothing destroyed.
   **If it shows a replacement, reject the run and stop**: the URL would
   change.
5. **Variables.** Copy the **Services** table from the apply run into the
   `render-production` environment variables `RENDER_SERVICE_ID` and
   `SERVICE_URL`.
6. **First release.** Re-run the failed jobs of step 2's run (**Re-run
   failed jobs**). Expected: `deploy` sets the image and goes live, `/readyz`
   reports the commit, `smoke` passes, `record` records `success`. This step
   proves digest deploys and free-plan image services live.
7. **Second plan.** Run the apply again with nothing filled in: both plans
   must show *No changes*.
8. **Branch protection.** [Branch protection](#branch-protection-repository-admin-once).

To undo before step 6: revert the `infra/` change on `main` and apply; the
service returns to the Docker runtime. After step 6, use [Roll back](#roll-back).

## Faulty release demonstration

The level asks to show that a faulty release is detected and stopped, and
that the previous revision is restored and passes the same smoke check. This
runs on the **disposable service**, so production is never involved. Run it
before the teardown date (11/20/2026).

1. **Create the disposable service.** **Terraform apply** with `disposable`
   ticked. Copy its ID and URL from the run's **Services** table into the
   `render-disposable` environment variables `RENDER_SERVICE_ID` and
   `SERVICE_URL`. It starts on the latest production release's digest.
2. **Release a working revision.** **Actions → Release → Run workflow**,
   branch `main`, target `disposable`, commit and digest empty. Expected:
   every stage passes and the release is recorded `success` in
   `render-disposable`. Note the commit and digest
   (`known-good --environment render-disposable`).
3. **Prepare the faulty revision** on a throwaway branch, never merged.
   Proposed change (to be approved before use): in
   `app/providers/trello_provider.py`, `_create_card` builds the create URL by
   string formatting instead of `urlencode`:
   ```python
   url = f"https://api.trello.com/1/cards?idList={list_id}&name={title}"
   if desc is not None:
       url += f"&desc={desc}"
   request = Request(url, headers=headers, method="POST")
   ```
   Why it makes a good demonstration: it is a plausible "simplification",
   and every fast check passes (ruff, format, mypy, all unit tests, which
   replace `urlopen` and parse the URL leniently) as does the conformance
   suite against the in-memory provider, so the release builds and deploys
   it. Against real Trello, Python's HTTP client refuses a URL containing the
   spaces in `Conformance create test` before sending it, so `POST /issues`
   returns 500 and the smoke check fails. (Checked locally on a copy: all
   fast checks pass; the real client raises `InvalidURL` without opening a
   connection.)
4. **Release the faulty revision.** Run workflow from the throwaway branch,
   target `disposable`. Expected: `ci`, `build`, `deploy` pass; `/readyz`
   reports the faulty commit; `smoke` **fails** and the summary lists the
   failing create tests; `record` records `failure` ("build: failed at
   smoke"). The known-good revision is still step 2's.
5. **Restore.** Run workflow from `main`, target `disposable`, with step 2's
   commit and digest. Expected: no `ci` or `build`, `/readyz` reports the
   step 2 commit again, the same smoke check passes, `record` records
   `success` ("rollback: smoke check passed").
6. **Capture evidence** in the table below.
7. **Tear down.** Terraform apply with `disposable` unticked; the plan shows
   the disposable service destroyed. Check the Trello test list has no
   `Conformance` cards left, delete the throwaway branch, and clear the
   `render-disposable` variables.

Optionally repeat only step 5 on production (target `production`, the
production known-good commit and digest) to have a production rollback on
record.

### Evidence

| Step | Run link | Deployment record (environment, status) | `/readyz` commit | Image digest |
| --- | --- | --- | --- | --- |
| 2. Working release | | `render-disposable`, success | | |
| 4. Faulty release | | `render-disposable`, failure (failed at smoke) | | |
| 5. Rollback | | `render-disposable`, success | | |
| Production release after bootstrap | | `render-production`, success | | |

Failing tests named in step 4's summary:

Leftover issues listed in any summary, and how they were removed:

## Level 2 checklist (repository admin)

Things only a repository admin can do. In order:

1. **GitHub environments** (**Settings → Environments → New environment**):
   - `render-production`: **Deployment branches** → *Selected branches* →
     `main`. **No required reviewers** (merges release without a click).
     Secret `RENDER_API_KEY`; variable `RENDER_OWNER_ID`.
   - `render-disposable`: **no branch restriction** (the demonstration
     releases a throwaway branch), no reviewers. Secret `RENDER_API_KEY`;
     variable `RENDER_OWNER_ID`.
   - `render-infrastructure`: **Required reviewers**: the people allowed to
     approve applies and teardown. Deployment branches: `main`. Secrets
     `RENDER_API_KEY`, `TF_API_TOKEN`; variable `RENDER_OWNER_ID`.
2. **Repository secrets** `RENDER_API_KEY` and `TF_API_TOKEN` and the
   repository variable `RENDER_OWNER_ID` stay, for pull request plans only.
3. **Actions permissions** (**Settings → Actions → General → Workflow
   permissions**): leave the default *Read repository contents*; each job
   asks for more itself.
4. [Bootstrap](#bootstrap-docker-service-to-image-service-once), which
   includes making the GHCR package **public** and setting
   `RENDER_SERVICE_ID` and `SERVICE_URL` on `render-production`.
5. [Branch protection](#branch-protection-repository-admin-once).
6. Merge one small change and watch the release complete with no manual
   step.
7. The [faulty release demonstration](#faulty-release-demonstration),
   including approving the faulty change it uses.

## Tear down

The teardown date is 11/20/2026 (DESIGN.md 5.5).

### 1. Remove what Terraform created

Run this **before** deleting the GitHub secrets in step 2, because the
workflow needs them.

1. Go to **Actions → Terraform destroy → Run workflow**, choose `main`, and
   type `destroy`. Any other word skips the job, and nothing is deleted.
2. A required reviewer approves the `render-infrastructure` environment.
3. The workflow plans the teardown, shows it on the run's summary page
   (**2 to destroy**, or 3 if the disposable service exists; nothing else),
   destroys exactly that plan, and checks that Terraform state is empty.

This deletes the Render web service(s) and the link to the environment
group. Confirm they are gone in the Render dashboard.

Like applies, this only runs when started by hand and approved. Nothing
triggers it automatically.

If you hold a Render API key, you can do the same from your machine (see
[Run Terraform from your machine](#run-terraform-from-your-machine-optional)):

```bash
cd infra
terraform plan -destroy         # review: 2 (or 3) to destroy, nothing else
terraform destroy
```

### 2. Remove what was made by hand

| Item | Where | How |
| --- | --- | --- |
| Environment group (holds the provider credentials) | Render → Environment Groups | Delete the group |
| Render API key | Render → Account Settings → API Keys | Revoke |
| Trello token | Trello → profile → Settings → Applications | Revoke "OSPSD-Team4-Render" |
| Leftover test cards | The Trello test list | Delete any `Conformance …` cards, including those named in run summaries |
| Terraform state and workspace | HCP Terraform → workspace `ospsd-team4-render` → Settings → Destruction and Deletion | Delete the workspace, after `terraform destroy` |
| HCP Terraform API token | HCP Terraform → User Settings → Tokens | Revoke the token used for `TF_API_TOKEN` |
| Container images | GitHub → Packages → `ospsd-team4-issue-service` → Package settings | Delete the package |
| GitHub secrets, variables and environments | Repository → Settings | Delete `TF_API_TOKEN`, `RENDER_API_KEY`, `RENDER_OWNER_ID` and the `render-production`, `render-disposable` and `render-infrastructure` environments |

Deleting the package makes every recorded release undeployable, so do it
only after the services are gone.

**Kept on purpose:** the repository, including `infra/` and the workflows, so
the deployment can be recreated. Also kept are the Render, HCP Terraform and
Trello accounts and the test board, which cost nothing. Linear test issues
that were deleted stay recoverable in Linear for a period (DESIGN.md 5.3).

## Teammate walkthrough

The verification for this level: a teammate who did not write the pipeline
follows this document from a fresh checkout.

**Access the account owner gives you first:**
- Write access to this repository.
- Required reviewer on the `render-infrastructure` environment.
- Membership in the HCP Terraform org.

Everything below runs through GitHub Actions, so you need no Render API key
of your own.

- [ ] Clone the repository fresh and build the image:
      `scripts/disposable_env.sh` passes.
- [ ] Open a pull request with a small change. `checks` passes, and the
      Terraform plan reports "plan skipped" (no `infra/` change).
- [ ] Merge it. The **Release** run passes every stage with no manual step,
      and its summary shows the commit, digest, deploy window and
      *Release recorded: success*.
- [ ] [Find the running commit](#find-the-commit-behind-the-running-service)
      two ways; both match the merge commit.
- [ ] [Roll back](#roll-back) to the previous known-good revision using only
      this document. The rollback run passes, and `/readyz` reports the
      older commit. Then release `main` again (Actions → Release → Run
      workflow, branch `main`, target production, commit and digest empty).
- [ ] Run **Terraform apply** with nothing filled in. Both plans say
      *No changes*. Explain any change that does appear.
- [ ] Record your questions below.

### Questions asked during the walkthrough

| Date | Who | Question | Answer | Doc change |
| --- | --- | --- | --- | --- |
| | | | | |

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Release `deploy` fails: "environment is missing: … The bootstrap is not finished" | `RENDER_SERVICE_ID`, `SERVICE_URL`, `RENDER_OWNER_ID` or the `RENDER_API_KEY` secret is not set on that GitHub environment. Expected once, during the [bootstrap](#bootstrap-docker-service-to-image-service-once). Nothing was deployed. |
| Release `deploy` fails: "Render refused to set the service image: HTTP 400/404" | The service is not an image service yet (bootstrap step 4), the image repository differs from the service's, or the service ID is wrong. |
| Deploy ends as `update_failed` or never goes live | Render could not pull or start the image. If the package is private, make it public (bootstrap step 3). See the deploy's logs in the Render dashboard. Run `IMAGE_REF=…@<digest> scripts/disposable_env.sh` to check the image locally. |
| `check-ready` fails: "/readyz reports commit '<other sha>'" | The service runs a different image than the release deployed: another deploy happened in between (from the dashboard?), or Render set `RENDER_GIT_COMMIT`. Check Render's **Events**. |
| Smoke check fails | The release is recorded `failure` and Render runs it until someone [rolls back](#roll-back). The summary names the failing tests. Run `scripts/smoke_check.sh` locally against the URL to reproduce. |
| Release shows "Deploy skipped" | A newer commit reached `main` while this one waited; that commit's release deploys instead. Nothing to do. |
| A release or an apply never started ("cancelled") | Another run in the same concurrency group replaced it while it waited ([Concurrency](#concurrency)). Start it again. |
| Rollback refused: "was never released to …" or "never passed its smoke check" | Only a commit and digest recorded together as `success` in that environment can be restored. Use `known-good`. |
| Apply fails: "No successful production release is recorded yet" | Before the first successful release, give `bootstrap_image_digest` by hand. |
| Plan job says "No HCP Terraform or Render credentials" | The repository secrets are missing, or the pull request comes from a fork. See the [checklist](#level-2-checklist-repository-admin), item 2. |
| `init` fails with an organization or workspace error | Not logged in (`terraform login`), or the names in `infra/versions.tf` do not match HCP. |
| Plan says it is running in HCP Terraform and fails | The workspace's execution mode is not **Local**. |
| Service exits at start with "Missing required configuration for trello: …" | The named variable is missing from the environment group, or the group is not linked. The message never contains values. |
| `/readyz` shows `provider: unreachable` | The Trello token expired or was revoked, the list ID is wrong, or Trello is down. Service status stays 200. |
| Destroy run shows the job as skipped | The confirmation was not exactly `destroy`, or the run was not started from `main`. Nothing was deleted. |
| `ETag` arrives as `W/"..."` and `If-Match` updates fail with 412 | Render's Cloudflare edge compressed the response and weakened the tag. When the service gzips its own responses, gzip clients keep the strong tag; check the deployed response has `Content-Encoding: gzip`. Clients that do not accept `gzip` still get a weak tag. |
| Second plan shows changes | Something changed outside Terraform, for example in the dashboard. Read the diff. Either revert the dashboard change or bring the configuration in line through a pull request. A change to `runtime_source.image.digest` alone should never appear (it is ignored); if it does, see `infra/tests/`. |
