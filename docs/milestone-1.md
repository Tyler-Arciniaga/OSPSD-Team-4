# Milestone 1 Submission

## Team and Project

- Team: Team 4
- Members: Tyler Arciniaga, Turki Almejhed, Joseph Jiminian,
  Chan Young Kang, Vanchi Nathan
- Vertical: Issue Tracker
- Provider: Trello

## Completed Work

- Level 1: Documented GET /issues/{issue_id} operation,
  public response model, and initial behavior tests.
- Level 2: Trello card retrieval, translation into public
  id and title fields, and provider error handling.
- 15 offline test cases covering retrieval and failure scenarios.
- GitHub Actions checks for linting, formatting, type checking,
  and automated tests.
- Turki independently verified the integration locally,
  confirming all 15 tests passed and live card retrieval worked.

## Documentation

- [Setup, configuration, and usage](../README.md)
- [Local checks](../README.md#code-checks-and-tests)
- [Trello verification and cleanup](../README.md#trello-verification)
- [API contract](api.md)
- [Contributor guide and development workflow](../AGENTS.md)
- [Release responsibilities and recovery](../AGENTS.md#release-workflow)
- [CI configuration](../.github/workflows/ci.yml)

## Known Limitations and Next Steps

- Only single-issue retrieval is implemented.
- Full Trello card IDs are required; short links are not supported.
- Automated tests simulate Trello responses; live verification
  is performed separately.
- A separate testing strategy document remains to be added.
- Continue with the next specification levels and prepare
  the October 14 review release.

The submission PR records the annotated tag, matching commit SHA,
CI results, and links to review and verification evidence.