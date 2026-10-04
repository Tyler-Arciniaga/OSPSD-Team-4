# Contributor Guide

See [README.md](README.md#project-structure) for the project structure and code map.

## Planning and task ownership

The following process is proposed for team review:

- Meet once a week to review progress, prioritize tasks, and assign an owner and reviewer to each substantial task.
- Track tasks, expected behavior, and dependencies in GitHub Issues, using the appropriate feature request or bug report template.
- Each member focuses on their designated task and reviews their assigned teammate’s work.
- Raise blockers in the team chat promptly.
- Review unfinished tasks at weekly meetings and coordinate urgent changes in the team chat.

## Contribution workflow

1. Agree on shared API contracts before implementing dependent work.
2. Make changes on a branch and open a focused pull request containing only changes related to the task.
3. Link the relevant GitHub issue in the pull request description. Use `Closes #<issue-number>` if the task is completed, or `Related to #<issue-number>` if it is partially addressed.
4. Use the pull request template to explain what changed, how it was verified, what remains, and any blockers.
5. The author runs the agreed checks, and the assigned teammate reviews the changes.
6. The author addresses feedback, and the reviewer verifies the updates. The author merges after approval and passing checks.

## Setup and verification

- Follow the README’s [Installation](README.md#installation), [Configuration](README.md#configuration), and [Running the Service](README.md#running-the-service) instructions.
- Follow [Code Checks and Tests](README.md#code-checks-and-tests) to run checks locally. GitHub Actions should run the same automated checks.
- Follow [Trello Verification](README.md#trello-verification) to verify the integration against a Trello test account.
- Keep credentials out of the repository.

## Design and code changes

- Keep API documentation, examples, implementation, and tests consistent.
- Preserve the behavior required by previously completed specification levels.
- Translate Trello responses into the service’s documented response format.
- Keep Trello-specific SDK types inside the integration code when the provider boundary is introduced.
- Keep credentials and temporary files out of version control. Add their paths to `.gitignore` before committing.
- Avoid unnecessary code, dependencies, and unrelated changes.
- Preserve existing checks unless the team documents a reason to change them.

## Coding agents and external code

- Give agents bounded tasks with a contract and acceptance criteria.
- Every submitted change has a student owner who can explain, verify, and maintain it.
- Human review is required for substantive changes.
- Disclose AI-generated code in the pull request.
- Verify provider assumptions against official documentation and the actual integration.
- Attribute external code and respect its license.

## Release workflow

Before the first release, agree on version naming, compatibility communication, and the release coordinator.

For each release:
1. The coordinator selects an exact commit and runs required checks.
2. Create and push an annotated tag.
3. Another teammate verifies setup and a representative workflow from a fresh checkout of that tag.
4. The coordinator publishes a GitHub Release with:
   - User-visible changes and relevant issues or pull requests.
   - Breaking changes and setup/configuration changes.
   - Known limitations.
   - Links to verification and CI evidence.

### Release rules

- Mark the review release as a prerelease.
- Use a different coordinator for the final release.
- Keep published tags attached to their original commits.
- Publish corrections as new versions.

### Handling a defective release

Explain whether users should return to an earlier version or wait for a fix. Identify any data or configuration changes that reverting the code would not undo.

### Improving the process

After the review release, record one observation about how the release process worked and any adjustment the team makes.