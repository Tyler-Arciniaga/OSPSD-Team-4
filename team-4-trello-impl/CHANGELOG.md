# Changelog

All notable changes to `team-4-issue-tracker-trello` are recorded here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [semantic versioning](https://semver.org/). Each version is
released as the annotated tag `team-4-issue-tracker-trello-vX.Y.Z`.

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

[1.0.0]: https://github.com/Tyler-Arciniaga/OSPSD-Team-4/releases/tag/team-4-issue-tracker-trello-v1.0.0
