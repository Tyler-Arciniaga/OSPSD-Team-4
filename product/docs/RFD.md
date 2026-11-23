# RFD: Standup and Focus-Time Planning Agent

## Summary

A planning agent for student project teams that turns each developer's claimed issues into a daily plan on their own calendar and a standup doc the whole team can read. The agent estimates each issue's duration from its title and proposes the day's focus blocks around the developer's weekly availability. The developer approves the plan, or edits it in plain language, before anything is created. Later the developer reports progress in plain language, and the agent proposes updates to the tracker, calendar and plan that apply only after confirmation. The team lead gets visibility into who is working on what, and when it will land, as a byproduct.

## Problem

On a student project the lead knows who owns each ticket but not whether work has started or when it will land. Asking costs a message and a wait, and the answer is a guess. Developers also carry the planning in their heads: when to do each ticket and how long it will take. That planning is almost always completely invisible to the team.

- Without a shared, current plan, the team finds out a ticket slipped once it is already late.  
- The workaround is asking people, or long standups where some people are not direct about the work they must do. Developers also spend time away from coding preparing for the standup, going over their assigned issues and remembering what they worked on yesterday and for how long.

## User, customer and who we are not building for

- **User:** a developer on a student project team who claims issues in the team's tracker. The agent plans their day and writes their standup talking points, so they no longer plan and rehearse by hand.  
- **Customer:** the team lead, who wants to see who is working on what and when, without chasing people.  
- **Segment:** student teams building a shared project and hold daily (or just generally frequent) standup meetings to go individual contributions/updates. They use an issue tracker, online personal calendars, and shared documents.  
- **Not building for:**  
  - Industry teams with formal process.  
  - Team members who are unwilling to report progress. Enforcing diligence is the lead's job, not the product's.  
  - Performance evaluation. The product shows declared plans and progress and never scores or ranks team members.

## Evidence

Answers from teammates, gathered in a team meeting and paraphrased from notes.

Interview questions:

1. How did you know who was working on what, and how far along they were?  
2. Tell me about a time you needed to know whether something would be done by a deadline. What did you do?  
3. What have you tried to do in order to keep the team in sync?  
4. Who did that tracking, and how did it go?

**What we heard**

- Q1, knowing who does what: teams read GitHub (“commit history shows what changed since I last looked”) and talk over Slack or iMessage. Confusion is raised at the weekly standup or by direct message.  
- Q2, meeting a deadline: they first establish a realistic expectation by having everyone open a PR of current work so progress is “completely visible”. They estimate from previously completed work, communicate so nobody “bottlenecks”, and keep a plan B in case the "realistic deadline” cannot be achieved.  
- Q3, staying in sync: weekly/”every other day” standups. Oftentimes one person keeps everyone aligned.  
- Q4, who tracks: usually one or two people become the group leaders. They make sure work finishes on time and people work on what they should.  
- Standup length: a big complaint was that standups run long, because much of the time goes to aligning everyone on what each person is working on. People felt that time is “better spent” coding and wished standups were shorter.  
- Team visibility: people are often unsure who is expected to do what beyond the work they’re actively completing(i.e. the PR they currently have open).  
- Uneven preparation: some people plan what to present in standups to keep things streamlined and efficient (i.e. findings and implications from their work), while others arrive less prepared. The effort is not equal, so the standup is only as strong as its weakest link. Aligning still takes long because the unprepared person's work needs heavy clarification.

Do student teams hold daily standups? Not always daily, but often once or twice a week.

**What this means for the brief**

- Supports the customer: one or two leads already chase team progress manually.  
- Supports estimating from history: teams estimate from previously completed work. Our logged duration corrections and completion log are the same signal, so later estimates can use them.  
- Challenges the daily horizon: standups are weekly or twice a week, so a daily plan may be more frequent than teams are used to. We keep daily planning for now and treat the cadence as a risk to test.  
- Supports the standup pain: a big complaint was long standups spent aligning everyone on who does what, with people wishing they were shorter. This backs the Problem bullet on long standups. Preparation was also raised: those who prepare lose time to it, and uneven preparation by teammates still result in long standup meetings. An agent-written standup doc could give every person a readable update, which is a hypothesis to test.  
- Supports the shared plan: GitHub commits and PRs show what each person is currently working on but people are unsure who is doing what beyond these single PRs. A plan everyone can read shows who works on what, and when, so the standup can be shorter.

## Key scenarios

**1\. Plan the day (uses all three verticals)**

1. A developer asks the agent to schedule their day.  
2. The agent reads the developer's claimed issues from the tracker and carries over any partly finished work.  
3. The model estimates each issue's duration from its title. Code places the blocks around the developer's weekly availability.  
4. The agent shows the proposed plan. The developer approves it, or edits it in plain language, for example "the first block needs another hour."  
5. Only after approval, the agent creates the events on the developer's own calendar and writes the standup doc to shared storage.

**2\. Report progress**

1. At the end of the day the developer tells the agent, for example, "today I the finished two morning tickets, about 75% through the evening one."
2. The model parses this into proposed actions: remove the two finished issues from the tracker, mark their blocks done, record the remaining share of the evening issue.
3. The developer confirms. The agent records each completion in its own log first, then applies the changes.  
4. The next day's plan includes the remaining work (which will likely have a shorter estimated duration now that only 25% is left).

**3\. Ask about an issue**

1. A user asks in plain language, for example "what's the status of the login page issue?" The model finds the issue by its title and asks which one is meant if several match.  
2. The agent confirms the issue exists in the tracker, then looks up who claimed it in its own database.  
3. If the owner is on the same team, the agent reports from the owner's approved plan: whether it is scheduled today and when, how much is left from earlier work, and whether its block passed with no report. For now, two users are on the same team if they share the same tracker list. This is the simple rule, to be fleshed out.  
4. The agent answers only from approved plans, never from drafts. This workflow changes nothing, so it needs no approval step.

## Proposal

The agent builds on the Milestone 1 service. It composes our Trello issue tracker, the shared calendar contract, and the shared storage contract, and adds a model and a database.

- **The model does four jobs:** choose which workflow or endpoint to call from a plain-language request, estimate each block's duration from the issue title, parse a progress report into proposed actions, and match a user's question to an issue by its title.  
- **Code does the rest:** place blocks around availability, enforce the approval policy, execute only approved actions, answer questions only from approved plans, and make retries safe.  
- **Approval:** nothing changes on the tracker, calendar or in storage until the owner confirms the specific plan or actions shown. Questions about an issue only read, so they need no confirmation.  
- **State:** the agent's database holds each user's weekly availability, per-issue estimate and remaining share, calendar event IDs, a completion log, and per-user calendar credentials. The tracker holds only the issue title and ID, so claims and estimates live in the database.  
- **Calendars:** each person's blocks go on their own calendar, so each user signs in with their own Google account. The shared calendar contract cannot read events, so availability is entered once a week.  
- **Storage:** one shared team folder with one standup doc per person per day, so the team can read them.  
- **Progress is self-reported.** Removing an issue from the tracker is the done signal, because the tracker has no status field to use.

## Scope

**Must**

- Claim issues and see your own claimed list.  
- Propose a daily plan from title-based estimates and weekly availability.  
- Approve the whole plan, then edit individual blocks in plain language.  
- After approval, create blocks on the user's own calendar and write the standup doc.  
- Report progress in plain language and confirm the proposed actions before they apply.  
- Carry the remaining share of unfinished work into the next day's plan.  
- Answer plain-language questions about an issue from its owner's approved plan, for teammates on the same tracker list. Read-only.  
- Isolate each user's data.

**Should**

- Flag blocks whose time passed with no report in the standup doc.  
- Log duration corrections as an estimate signal.

**Won't, and why**

- Enforce diligence or score people. That is the lead's or instructor's job, and a score would make the product read as a performance/surveillance tool.  
- Read the user's real calendar. The shared calendar contract has no read operation, so availability is entered weekly.  
- Plan beyond one day. Daily planning is the chosen horizon, which keeps the first version small enough to ship and test in launch week.  
- Verify reported progress. Progress is self-reported by design.  
- Serve industry teams. Our evidence and launch users are students.  
- Run as a hand-off background agent. That is extra credit and the key scenarios do not need it.

## Success metrics and guardrails

One primary outcome per scenario, measured during launch week. Numeric targets are set before launch.

| Scenario | Metric | How measured |
| :---- | :---- | :---- |
| Plan the day | Share of proposed plans approved, plus the size of duration edits | Approval and edit events the agent logs. Edits are the estimate-quality signal. |
| Report progress | Share of reports where the user confirms the parsed actions unchanged | Confirmation events the agent logs |
| Ask about an issue | Share of questions answered from an approved plan, rather than "no approved plan" or no match | Question and outcome events the agent logs |

**Guardrails the agent must never break**

- Never publish a teammate's schedule that its owner has not approved.  
- Never be read as a performance score. It does not rank or score people.  
- Keep each user's data isolated from other users.  
- Never change anything outside the plan or actions the owner approved.  
- Never delete an issue without explicit confirmation.

## Assumptions, risks and tests

Ranked by the damage a wrong assumption would cause.

1. Title-only estimates are good enough that users approve plans without heavy edits. If wrong, users reject most plans and the product is essentially a glorified calendar form.  
2. A retry after a calendar timeout does not create duplicate events. The calendar contract's create is not idempotent. If wrong, users see double blocks.  
3. Developers will report progress most days. If wrong, plans drift from reality and the standup doc goes stale.  
4. Teammates will open the standup docs. If wrong, the lead gets no visibility.  
5. Student teams read a daily plan or hold frequent standups. Unknown until the interviews.  
6. Launch week fits Google's limits for apps in testing, including the test-user list and token lifetime. Check the current limits.

**Test 1: estimates from titles**

- Belief: a model estimate from the title alone is close enough that owners make small edits.  
- Test: run the model on real issue titles from our own tracker (number to decide), ask each owner to adjust the estimate, and compare the size of the adjustments with a flat 90-minute baseline.  
- Result and decision: to fill after the test.

**Test 2: retry after a timeout**

- Belief: the workflow can retry safely after the calendar times out.  
- Test: with the real calendar implementation, let a create succeed, interrupt the response, then retry. Record whether a duplicate event appears and what the implementation reports.  
- Result and decision: to fill after the test.

## Open questions

| Question | Owner | Date |
| :---- | :---- | :---- |
| If someone deletes a card in the tracker themselves, does it count as done or cancelled? | Chan | Nov 22 |
| Determine what specific Agent provider to use and with what tier. | Turki | Nov 23 |
| What format should weekly availability take: recurring hours plus meetings? | Turki | Nov 22 |
| Which Dropbox account and folder hold the standup docs? | Vanchi | Nov 23 |
| What numeric targets do we set for the four metrics? | Tyler | Nov 22 |
| Do Google's testing-mode limits cover launch week? | Joseph | Nov 23 |

&nbsp;

