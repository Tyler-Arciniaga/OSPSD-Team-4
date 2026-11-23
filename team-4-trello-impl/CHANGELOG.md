# Changelog

All notable changes to `team-4-issue-tracker-trello` are recorded here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [semantic versioning](https://semver.org/). Each version is
released as the annotated tag `team-4-issue-tracker-trello-vX.Y.Z`.

## [1.1.0] - Unreleased

### Added

- Optional keyword-only constructor arguments:
  `TrelloIssueProvider(*, api_key=None, token=None, list_id=None)`
  ([#91](https://github.com/Tyler-Arciniaga/OSPSD-Team-4/issues/91)). Each
  provider uses its own arguments, so one process can serve several Trello
  accounts and lists at once, including from different threads. `api_key`
  and `token` are used for every request; `list_id` only for `list_issues`,
  `create_issue`, `create_tagged_issue`, `find_tagged_issue` and `ping`.

### Changed

- Nothing for existing callers. An omitted argument (`None`) falls back to `TRELLO_API_KEY`,
  `TRELLO_TOKEN` or `TRELLO_LIST_ID`, read when each call runs, so existing
  `TrelloIssueProvider()` callers see no change. Construction still never
  raises; missing configuration still fails on the call with the same
  `service_not_configured` errors (`missing_credentials`,
  `missing_target_list`).
- An empty string argument counts as missing (the same error as an unset
  variable) and does not fall back to the environment.
- `issue-tracker-contract` is unchanged (still 1.1.0).
- The 1.0.0 known limitation "one list per process" no longer applies.

## [1.0.0] - 2026-11-15

### Added

- First release as an installable package. `TrelloIssueProvider` was
  extracted from the Team 4 issue service (`app/providers/trello_provider.py`)
  with its behavior unchanged: the same methods, environment variables,
  requests and `ServiceError` translation.
- Implements `issue-tracker-contract` 1.1.0 and depends on it through its git
  tag `issue-tracker-contract-v1.1.0`.
- `set_span_factory()`, an optional hook for tracing Trello requests. Spans do
  nothing until a factory is set. The service's copy imports its OpenTelemetry
  tracing directly; the package takes it as a hook so it does not depend on
  OpenTelemetry.

### Known limitations

- Configuration is read from `TRELLO_API_KEY`, `TRELLO_TOKEN` and
  `TRELLO_LIST_ID`, so a process can use only one list.
- The Team 4 issue service still runs its own copy of this provider in
  `app/providers/trello_provider.py`. Until the service switches to this
  package, changes to the provider must be made in both places.

[1.1.0]: https://github.com/Tyler-Arciniaga/OSPSD-Team-4/releases/tag/team-4-issue-tracker-trello-v1.1.0
[1.0.0]: https://github.com/Tyler-Arciniaga/OSPSD-Team-4/releases/tag/team-4-issue-tracker-trello-v1.0.0
