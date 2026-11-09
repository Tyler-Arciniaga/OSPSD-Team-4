# Deployment

How the service is built, deployed, checked, rolled back and torn down. A
teammate should be able to follow this from a fresh checkout without asking
anyone. If you had to ask something, add it to the
[question log](#questions-asked-during-the-walkthrough): each question is a
gap in this document.

## Overview

```
commit on main, pinned in infra/release.tf
    │  pull request: GitHub Actions runs `terraform plan` and posts it for review
    ▼
manual "Terraform apply and deploy" workflow (approval required)
    │  terraform apply  → Render web service (infra/main.tf)
    │  Render deploy API → Render builds the Dockerfile at exactly that commit
    ▼
running container: config from Terraform, secrets from a Render environment group
    │
    ▼
caller → https://<service>.onrender.com  (/healthz, /readyz, /issues)
```

| Piece | Where it lives | Created by |
| --- | --- | --- |
| Artifact | Container image built by Render from `Dockerfile` at the pinned commit, with dependencies from `uv.lock` | Render, on each deploy |
| Infrastructure description | `infra/*.tf` in this repository | Reviewed pull requests |
| Infrastructure state | HCP Terraform, org `ospsd-team-4`, workspace `ospsd-team4-render` | Terraform |
| Web service and secrets link | Render, service `ospsd-team4-issue-tracker`, region Virginia, free plan | `terraform apply` |
| Provider credentials | Render environment group (ID in `infra/release.tf`) | By hand, once |
| CI, deploy and teardown automation | `.github/workflows/` | Reviewed pull requests |

### Configuration and secrets

| Value | Kind | Where it is set | Who can read it |
| --- | --- | --- | --- |
| `ISSUE_PROVIDER`, `IDEMPOTENCY_DB_PATH` | Configuration | `infra/main.tf` (service variables) | Anyone with repository access |
| `commit_sha`, `branch`, environment group ID | Configuration | `infra/release.tf` | Anyone with repository access |
| `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_LIST_ID` (or `LINEAR_API_KEY`, `LINEAR_TEAM_ID`) | Secret | Render environment group, by hand | Render workspace members; the running service |
| `RENDER_API_KEY` | Secret | Your shell; GitHub secret | Its owner; the plan, apply and destroy workflows |
| `RENDER_OWNER_ID` | Configuration | Your shell; GitHub variable | Repository collaborators |
| HCP Terraform API token | Secret | `~/.terraform.d` via `terraform login`; GitHub secret `TF_API_TOKEN` | Its owner; the workflows |

Secrets are never in the repository, the image or Terraform state. Terraform
only links the environment group by its ID, and that link's state holds the
two IDs alone. The provider marks every service variable's value as sensitive,
so plans show `(sensitive value)` even for plain configuration. State and
plan files are still treated as sensitive: they stay out of the repository
(`.gitignore`) and out of CI artifacts.

## Prerequisites (by hand, once)

These steps cannot be automated. Whoever owns the accounts does them once.
They are already done for this team; a teammate only needs the
[walkthrough access](#teammate-walkthrough).

1. **HCP Terraform** (app.terraform.io, free):
   - Create the organization `ospsd-team-4`.
   - Create the workspace `ospsd-team4-render` with the **CLI-driven workflow**.
   - Under **Settings → General → Execution Mode**, choose **Local**. Plans and applies then run where the Render key is, and HCP only stores and locks the state.
   - Invite teammates under **Organization settings → Users**.
2. **Render** (render.com, free, no payment method):
   - Create an API key under **Account Settings → API Keys**.
   - Note the workspace ID (`tea-…` or `usr-…`) from the workspace settings.
3. **Provider credentials** in a Render **environment group** (**Dashboard → Environment Groups → New**):
   - Trello: `TRELLO_API_KEY`, `TRELLO_TOKEN` and `TRELLO_LIST_ID`. Use a **dedicated test list**: the conformance run creates and deletes real cards there.
   - Create the token with `expiration=30days`, from the test account: `https://trello.com/1/authorize?expiration=30days&scope=read,write&response_type=token&name=OSPSD-Team4-Render&key=<TRELLO_API_KEY>`. The README's one-day token would expire under the running service.
   - Put the group's `evg-…` ID in `infra/release.tf`.
4. **GitHub** (**Settings** of this repository):
   - **Secrets and variables → Actions → Secrets:** `TF_API_TOKEN` (an HCP Terraform API token from **User Settings → Tokens**) and `RENDER_API_KEY`.
   - **Secrets and variables → Actions → Variables:** `RENDER_OWNER_ID`.
   - **Environments → New environment** `render-production`: add the people allowed to approve deploys as **Required reviewers**, and limit **Deployment branches** to `main`.

## Merge gate

A change reaches `main` only through a pull request whose checks passed.

| Check | Workflow | Runs on | Required to merge |
| --- | --- | --- | --- |
| `checks`: passes only if every job below passed | CI (`ci.yml`) | every push and pull request | **yes** |
| `tests`: `ruff check`, `ruff format --check`, mypy, pytest | CI | every push and pull request | through `checks` |
| `conformance-controlled`: the conformance suite against the real service backed by an in-memory provider | CI | every push and pull request | through `checks` |
| `docker-image`: builds the image (the release workflow checks the pushed image in a disposable container) | CI | every push and pull request | through `checks` |
| `plan`: `terraform fmt`, `validate`, plus `plan` posted on the pull request when it changes `infra/` or a Terraform workflow | Terraform plan | every pull request | no; review the posted plan |

Pull requests from forks run with no secrets: every workflow uses
`pull_request`, never `pull_request_target`. They get every CI check, and
Terraform formatting and validation without the plan.

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

## Deploy

Deploys are manual. Merges and pushes never deploy, and Render's auto-deploy
is off.

1. **Pin the commit.** On a branch, set `commit_sha` in `infra/release.tf`
   to the full SHA of the commit to deploy. It must be on `main` once the
   pull request merges, so merge with a merge commit, not a squash.
2. **Review the plan.** Open a pull request. The **Terraform plan**
   workflow posts the plan as a comment. A commit change shows under
   *Changes to Outputs* as `commit_sha`. Infrastructure changes show as
   resource changes; explain each one in the pull request.
3. **Merge** after review.
4. **Apply and deploy.** Go to **Actions → Terraform apply and deploy → Run
   workflow**, choose `main`, and enter the same commit SHA as a
   confirmation. A required reviewer approves the run. The workflow:
   1. plans and applies exactly that plan;
   2. asks Render to deploy that commit and waits until it is live (a free
      build takes several minutes);
   3. checks that `/readyz` reports that commit;
   4. **plans again and fails unless it shows no changes**.

   The run's summary page shows both plans and the deployed URL.

On the very first apply, Render may start its own build of `main`'s latest
commit when the service is created. The workflow's deploy of the pinned
commit follows it.

## Verify

```bash
URL=https://ospsd-team4-issue-tracker.onrender.com   # from the run summary or `terraform output service_url`
curl -sS "$URL/healthz"   # {"status":"ok"}
curl -sS "$URL/readyz"    # {"status":"ready", ..., "provider":"reachable","commit":"<pinned sha>"}
```

- `/healthz` is the liveness check Render uses. It does not contact the
  provider, so a provider outage never makes Render restart the service.
- `/readyz` is 200 whenever the service can serve. `provider` is information
  only: `unreachable` means the provider did not answer or rejected the
  credentials, which is worth checking in the environment group. See
  [Health checks](api.md#health-checks).
- A free service sleeps after 15 minutes without traffic, and the first
  request after that takes about a minute.

**Conformance suite against the deployment.** This creates and deletes real
cards in the test list:

```bash
scripts/conformance_remote.sh "$URL"
```

It waits for the service to wake, prints `/readyz` so the results record the
commit, and runs `contract/tests/conformance`. A `CleanupWarning` names an
issue the suite could not delete, so remove that issue by hand.

- **Linear:** rerun the suite against a Linear deployment once Linear rename
  and delete are merged. To switch, set `issue_provider = "linear"` (see
  `infra/variables.tf`) and put `LINEAR_API_KEY` and `LINEAR_TEAM_ID` in the
  environment group.
- **Until then**, every issue created against Linear is reported as a
  `CleanupWarning`.

**Repeat the plan.** Start the apply workflow again with the same commit. Its
first plan says *No changes*, apply changes nothing, and the second plan
passes. Render redeploys the same commit; no resource is replaced.

## Run Terraform from your machine (optional)

The workflows are the normal path. To run a plan yourself, you need HCP
access, a Render API key for the workspace, and Terraform 1.17.x:

```bash
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
cd infra
terraform login                 # saves an HCP token in ~/.terraform.d
export RENDER_API_KEY=...       # type it; never put it in a file
export RENDER_OWNER_ID=tea-...
terraform init
terraform plan                  # read-only
```

Do not run `terraform apply` locally: it would create or update the service
without deploying the pinned commit or checking it. Use the workflow.

## Disposable test environment

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

## Roll back

1. Set `commit_sha` in `infra/release.tf` back to the previous good commit.
   The earlier apply runs' summaries list each deployed commit.
2. Follow [Deploy](#deploy) from step 2.

Rolling back the code changes no data in the provider. Issues created by the
newer version stay. The idempotency key store is not durable on the free plan
(DESIGN.md 5.5), so any deploy, including a rollback, forgets stored keys.

If a deploy fails to build or start, Render keeps the previous deploy
running. Fix forward or roll back as above.

## Tear down

The teardown date is 11/20/2026 (DESIGN.md 5.5).

### 1. Remove what Terraform created

Run this **before** deleting the GitHub secrets in step 2, because the
workflow needs them.

1. Go to **Actions → Terraform destroy → Run workflow**, choose `main`, and
   type `destroy`. Any other word skips the job, and nothing is deleted.
2. A required reviewer approves the `render-production` environment.
3. The workflow plans the teardown, shows it on the run's summary page
   (**2 to destroy**, nothing else), destroys exactly that plan, and checks
   that Terraform state is empty.

This deletes the Render web service and its link to the environment group.
Confirm the service is gone in the Render dashboard.

Like deploys, this only runs when started by hand and approved. Nothing
triggers it automatically.

If you hold a Render API key, you can do the same from your machine (see
[Run Terraform from your machine](#run-terraform-from-your-machine-optional)):

```bash
cd infra
terraform plan -destroy         # review: 2 to destroy, nothing else
terraform destroy
```

### 2. Remove what was made by hand

| Item | Where | How |
| --- | --- | --- |
| Environment group (holds the provider credentials) | Render → Environment Groups | Delete the group |
| Render API key | Render → Account Settings → API Keys | Revoke |
| Trello token | Trello → profile → Settings → Applications | Revoke "OSPSD-Team4-Render" |
| Leftover test cards | The Trello test list | Delete any cards named in `CleanupWarning`s |
| Terraform state and workspace | HCP Terraform → workspace `ospsd-team4-render` → Settings → Destruction and Deletion | Delete the workspace, after `terraform destroy` |
| HCP Terraform API token | HCP Terraform → User Settings → Tokens | Revoke the token used for `TF_API_TOKEN` |
| GitHub secrets, variable and environment | Repository → Settings | Delete `TF_API_TOKEN`, `RENDER_API_KEY`, `RENDER_OWNER_ID` and the `render-production` environment |

**Kept on purpose:** the repository, including `infra/` and the workflows, so
the deployment can be recreated. Also kept are the Render, HCP Terraform and
Trello accounts and the test board, which cost nothing. Linear test issues
that were deleted stay recoverable in Linear for a period (DESIGN.md 5.3).

## Teammate walkthrough

The verification for this level: a teammate who did not write the
infrastructure code follows this document from a fresh checkout.

**Access the account owner gives you first:**
- Write access to this repository.
- Required reviewer on the `render-production` environment.
- Membership in the HCP Terraform org.

Everything below runs through GitHub Actions, so you need no Render API key
of your own.

- [ ] Clone the repository fresh and build the image:
      `scripts/disposable_env.sh` passes.
- [ ] Open a pull request that sets `commit_sha` in `infra/release.tf`, and
      read the plan comment. You can explain every line.
- [ ] Merge it, run **Terraform apply and deploy**, and approve it. The run
      passes, including *Plan again (must show no changes)*.
- [ ] `curl $URL/healthz` and `curl $URL/readyz` return 200, and `commit`
      matches.
- [ ] `scripts/conformance_remote.sh $URL` passes.
- [ ] Run the apply workflow again with the same commit. Both plans say
      *No changes*. Explain any change that does appear.
- [ ] Tear down: run **Terraform destroy** and approve it. Its run shows
      empty state. Then go through every row in
      [Remove what was made by hand](#2-remove-what-was-made-by-hand).
- [ ] Record your questions below.

### Questions asked during the walkthrough

| Date | Who | Question | Answer | Doc change |
| --- | --- | --- | --- | --- |
| | | | | |

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Plan job says "No HCP Terraform or Render credentials" | The repository secrets are missing, or the pull request comes from a fork. See [Prerequisites](#prerequisites-by-hand-once), step 4. |
| `init` fails with an organization or workspace error | Not logged in (`terraform login`), or the names in `infra/versions.tf` do not match HCP. |
| Plan says it is running in HCP Terraform and fails | The workspace's execution mode is not **Local**. |
| Apply fails "Confirmation ... does not match" | The SHA you typed differs from `commit_sha` in `infra/release.tf` on `main`. Nothing was applied. |
| Deploy ends as `build_failed` | See the deploy's logs in the Render dashboard. Run `scripts/disposable_env.sh` at that commit to reproduce the build locally. |
| Service exits at start with "Missing required configuration for trello: …" | The named variable is missing from the environment group, or the group is not linked. The message never contains values. |
| `/readyz` shows `provider: unreachable` | The Trello token expired or was revoked, the list ID is wrong, or Trello is down. Service status stays 200. |
| Destroy run shows the job as skipped | The confirmation was not exactly `destroy`, or the run was not started from `main`. Nothing was deleted. |
| Apply waits on "deploy … queued; waiting for it to start" | Normal on the first apply: creating the service starts a build of the branch head, and the pinned deploy waits behind it. |
| `ETag` arrives as `W/"..."` and `If-Match` updates fail with 412 | Render's Cloudflare edge compressed the response and weakened the tag. When the service gzips its own responses, gzip clients keep the strong tag; check the deployed response has `Content-Encoding: gzip`. Clients that do not accept `gzip` still get a weak tag. |
| Second plan shows changes | Something changed outside Terraform, for example in the dashboard. Read the diff. Either revert the dashboard change or bring the configuration in line through a pull request. |
