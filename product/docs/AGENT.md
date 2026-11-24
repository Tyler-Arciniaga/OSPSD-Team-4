# Agent model decision

Team 4 will use the Google Gemini Developer API free tier and one model, `gemini-3.5-flash-lite`, for all four Level 3 jobs. Provider documentation checked November 23, 2026. [Model details][model] · [Free-tier pricing][pricing]

| Job | Model | Expected result |
| --- | --- | --- |
| Choose a workflow from a plain-language request | `gemini-3.5-flash-lite` | Select daily planning, a progress update, or an issue-status lookup. Ask for clarification if the request is unclear. |
| Estimate a focus block from an issue title | `gemini-3.5-flash-lite` | Suggest a duration in minutes that the developer can edit before approving the plan. |
| Parse a progress report for `POST /v1/progress` | `gemini-3.5-flash-lite` | Propose which issues are complete and how much work remains on each unfinished issue. |
| Match a title for `GET /v1/issue-status` | `gemini-3.5-flash-lite` | Select an issue from the caller’s permitted candidates or ask which issue they mean. |

## Why this fits

- Function calling lets the CLI route “plan my day” to the planning workflow. The service handles scheduling and enforces approval before making changes. [Model capabilities][model]
- Structured output lets the service validate issue IDs and progress values before showing a proposed update or looking up an approved plan. [Model capabilities][model]
- Free-tier text input and output fit our launch-week budget. Google AI Studio showed a free-tier limit of 500 requests per day for Gemini 3.5 Flash-Lite. At two calls per draft plan, 25 plans would use 50 requests, leaving room for edits, progress reports and status questions. [Pricing][pricing] · [Gemini API rate limits](https://aistudio.google.com/rate-limit)
- We can test title-based estimates against the RFD’s 90-minute baseline using developers’ corrections before deciding whether that job needs a different model.

## Alternatives considered

- Gemini 3.8 Flash. Google AI Studio showed a free-tier limit of 20 requests per day for this model. Ten two-call draft plans would use that allowance before teammates could report progress. [Gemini API rate limits](https://aistudio.google.com/rate-limit)

## API key

The backend reads `GEMINI_API_KEY` from its environment. Use a local key for development and a separate deployment secret. Never commit either key. [Google API key guide](https://ai.google.dev/gemini-api/docs/api-key)

[model]: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
[pricing]: https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite
