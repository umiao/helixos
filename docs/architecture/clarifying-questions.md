# Clarifying Questions Protocol -- Design Document

> **Task**: T-P0-145
> **Status**: DESIGN (awaiting user review before implementation)
> **Date**: 2026-03-09

## 1. Problem Statement

The review agent can currently only APPROVE or REJECT a plan. When a reviewer
encounters ambiguity -- unclear requirements, missing context about the codebase,
or debatable design choices -- it must either pass (ignoring the issue) or reject
(forcing a full replan cycle). There is no mechanism for the agent to ask the
human a targeted question and incorporate the answer before making a verdict.

**Result**: Plans get rejected for issues that could be resolved with a single
clarifying exchange, wasting an entire review + replan cycle (~2-5 minutes and
~$0.10-0.50 in API costs).

## 2. Goals

1. Review agent can **pause and ask questions** instead of only approve/reject.
2. Human can **answer inline** in the ReviewPanel without leaving the UI.
3. After answers are provided, the review **resumes with Q&A context** injected.
4. Questions and answers are **persisted** for audit trail and future re-reviews.
5. Design is **backward-compatible** -- existing approve/reject flow unchanged.

## 3. Non-Goals

- Multi-turn conversation (agent asks follow-up to an answer). V1 is single-round: agent asks N questions, human answers all, review resumes.
- Plan-generation-time questions (only review-time for V1).
- Agent-to-agent clarification (only agent-to-human).

## 4. Data Model

### 4.1 New: ReviewQuestion

Stored in a new `review_questions` table. Each row is one question from one
reviewer in one review attempt.

```python
class ReviewQuestionRow(Base):
    """A clarifying question from a reviewer to the human."""

    __tablename__ = "review_questions"

    id: int                          # PK, auto-increment
    task_id: str                     # FK -> tasks.id, indexed
    review_attempt: int              # Which review attempt spawned this
    reviewer_focus: str              # e.g., "feasibility_and_edge_cases"
    question: str                    # The question text (max 2KB)
    context: str                     # Why the reviewer is asking (max 2KB)
    answer: str | None               # Human's answer (max 4KB), NULL until answered
    answered_at: datetime | None     # When the human answered
    created_at: datetime             # When the question was created

    Index: (task_id, review_attempt)
```

**Pydantic model** (for API serialization):

```python
class ReviewQuestion(BaseModel):
    id: int
    task_id: str
    review_attempt: int
    reviewer_focus: str
    question: str
    context: str
    answer: str | None = None
    answered_at: datetime | None = None
    created_at: datetime
```

### 4.2 Modified: ReviewResult JSON Schema

The LLM output schema gains an optional `questions` field:

```json
{
  "blocking_issues": [...],
  "suggestions": [...],
  "pass": true | false | null,
  "questions": [
    {
      "question": "Does the existing auth middleware handle token refresh, or should the plan include that?",
      "context": "Step 3 adds a new protected endpoint but the plan doesn't mention token refresh handling."
    }
  ]
}
```

**Rules**:
- When `questions` is non-empty and `pass` is `null`, the reviewer is
  requesting clarification before making a verdict.
- When `questions` is empty (or absent), the reviewer proceeds as before
  with a definitive `pass: true/false`.
- A reviewer can ask questions AND still provide preliminary blocking_issues
  and suggestions that don't depend on the answers.

### 4.3 Modified: ReviewLifecycleState

New state: `AWAITING_ANSWERS`

```
NOT_STARTED -> RUNNING -> AWAITING_ANSWERS -> RUNNING (resume after answers)
                  |                              |
                  +-> APPROVED / REJECTED_* / FAILED / PARTIAL
```

Transition rules:
- `RUNNING -> AWAITING_ANSWERS`: At least one reviewer emitted questions
  with `pass: null`.
- `AWAITING_ANSWERS -> RUNNING`: All questions answered, review resumes.
- `AWAITING_ANSWERS -> NOT_STARTED`: Task moved backward (e.g., to BACKLOG).

### 4.4 Modified: ReviewState

```python
class ReviewState(BaseModel):
    # ... existing fields ...
    pending_questions: int = 0       # Count of unanswered questions
    lifecycle_state: str             # Now includes "awaiting_answers"
```

## 5. API Endpoints

### 5.1 GET /api/tasks/{task_id}/review/questions

Returns all questions for a task, optionally filtered by review_attempt.

**Query params**: `review_attempt: int | None` (filter by attempt, default: latest)

**Response**:
```json
{
  "questions": [
    {
      "id": 1,
      "task_id": "abc-123",
      "review_attempt": 1,
      "reviewer_focus": "feasibility_and_edge_cases",
      "question": "Does the existing auth middleware handle token refresh?",
      "context": "Step 3 adds a new protected endpoint...",
      "answer": null,
      "answered_at": null,
      "created_at": "2026-03-09T12:00:00Z"
    }
  ],
  "total": 1,
  "unanswered": 1
}
```

### 5.2 POST /api/tasks/{task_id}/review/questions/{question_id}/answer

Submit an answer to a specific question.

**Request**:
```json
{
  "answer": "Yes, the auth middleware handles refresh via the RefreshTokenMiddleware class in src/auth.py."
}
```

**Response**: Updated `ReviewQuestion` object.

**Side-effects**:
- Sets `answer` and `answered_at` on the question row.
- If this was the last unanswered question for the current review_attempt:
  - Emits `"questions_answered"` SSE event.
  - Sets `review_lifecycle_state` to `RUNNING`.
  - Resumes the review pipeline (see Section 7).

### 5.3 POST /api/tasks/{task_id}/review/questions/answer-all

Batch-answer all unanswered questions for the current review attempt.

**Request**:
```json
{
  "answers": [
    { "question_id": 1, "answer": "Yes, it handles refresh." },
    { "question_id": 2, "answer": "No, we can skip that edge case." }
  ]
}
```

**Response**:
```json
{
  "answered": 2,
  "remaining": 0,
  "review_resumed": true
}
```

**Rationale**: The common case is answering all questions at once, so a batch
endpoint avoids N round-trips and the complexity of tracking partial answers.

### 5.4 POST /api/tasks/{task_id}/review/questions/skip

Skip all unanswered questions and force the review to continue without answers.
The review resumes with a note that questions were skipped.

**Response**:
```json
{
  "skipped": 2,
  "review_resumed": true
}
```

## 6. Review Prompt Changes

### 6.1 Updated review.md Template

Add a new section to the review prompt:

```markdown
## Clarifying Questions

If you need more information from the human to make a confident verdict,
you may ask clarifying questions instead of guessing.

Set "pass" to null and include a "questions" array:

{
  "blocking_issues": [],
  "suggestions": [],
  "pass": null,
  "questions": [
    {
      "question": "Your specific question here",
      "context": "Why you need this information to evaluate the plan"
    }
  ]
}

Guidelines for questions:
- Ask only when the answer materially affects your verdict (would change pass/fail).
- Be specific. "Is this correct?" is bad. "Does UserService.get_by_email() return None or raise NotFoundError when the user doesn't exist?" is good.
- Include context explaining why the answer matters for the plan.
- Maximum 3 questions per review. If you need more, the plan likely needs revision.
- Do NOT ask questions about things you can determine from the plan itself.

If you have no questions, omit the "questions" field entirely and provide
a definitive "pass": true or false.
```

### 6.2 Resume Prompt (New: review_resume.md)

When the review resumes after questions are answered, the reviewer receives
a continuation prompt:

```markdown
You previously reviewed this plan and asked clarifying questions.
Here are the questions and the human's answers:

{{qa_pairs}}

Based on these answers, complete your review. Respond with the same JSON
schema as before (blocking_issues, suggestions, pass). Do NOT ask further
questions -- provide a definitive verdict.
```

## 7. Backend Flow

### 7.1 Question Emission (During Review)

```
ReviewPipeline.review_task()
    |
    v
_call_reviewer() returns ReviewResult
    |
    v
Parse result.questions
    |
    +-- questions is empty or absent --> proceed as before (approve/reject)
    |
    +-- questions is non-empty, pass is null:
        |
        v
        Persist questions to review_questions table
        Set lifecycle_state = AWAITING_ANSWERS
        Emit "review_questions" SSE event with question data
        PAUSE this reviewer (store partial state)
        |
        v
        If ALL reviewers either finished or are awaiting:
            Set task lifecycle_state = AWAITING_ANSWERS
            Emit "review_awaiting_answers" SSE event
```

### 7.2 Answer Submission and Resume

```
POST /api/tasks/{id}/review/questions/answer-all
    |
    v
Persist answers to review_questions table
    |
    v
Check: any unanswered questions remaining?
    |
    +-- yes --> return (partial), keep AWAITING_ANSWERS
    |
    +-- no --> all answered:
        |
        v
        Set lifecycle_state = RUNNING
        Emit "review_resumed" SSE event
        |
        v
        For each reviewer that asked questions:
            Build resume prompt with Q&A pairs
            Call _call_reviewer() again with resume context
        |
        v
        Aggregate results as normal (consensus score, etc.)
        Proceed to terminal state (APPROVED/REJECTED_*/etc.)
```

### 7.3 Partial State Storage

When a reviewer asks questions, its partial state is stored in memory
(not DB) as part of the `_run_review_bg` asyncio task:

```python
@dataclass
class PausedReviewer:
    focus: str
    model: str
    partial_issues: list[BlockingIssue]
    partial_suggestions: list[str]
    questions: list[ReviewQuestion]
```

On resume, the partial issues and suggestions are merged with the
resumed review's output to form the final `LLMReview`.

## 8. Frontend UX Flow

### 8.1 ReviewPanel Changes

When `task.review_lifecycle_state === "awaiting_answers"`:

1. **Question Cards**: Each question displayed as a card with:
   - Reviewer focus badge (e.g., "Feasibility")
   - Question text (markdown-rendered)
   - Context text (muted, smaller)
   - Answer textarea (auto-focused on first question)
   - Character count (max 4000)

2. **Action Bar**:
   - "Submit Answers" button (primary, enabled when all questions have answers)
   - "Skip Questions" button (secondary/danger, continues review without answers)

3. **Visual Treatment**:
   - Amber/yellow banner: "Review paused -- N questions need your input"
   - Question cards have left amber border (similar to blocking issues' red border)
   - Answered questions show green checkmark, unanswered show amber circle

### 8.2 State Flow in ReviewPanel

```
lifecycle_state === "running"
    --> Spinner + phase label (existing behavior)

lifecycle_state === "awaiting_answers"
    --> Amber banner + question cards + answer textareas + submit button

lifecycle_state === "running" (after submit)
    --> Spinner + "Resuming review with your answers..."

lifecycle_state === "approved" / "rejected_*"
    --> Existing verdict display + Q&A history section
```

### 8.3 Q&A History Display

After review completes (any terminal state), the Q&A exchange is shown in a
collapsible "Clarifying Questions" section within the review attempt group:

```
Review Attempt #1
  +-- Reviewer: Feasibility
  |     Verdict: Approved
  |     [v] Clarifying Questions (2)
  |         Q: Does UserService.get_by_email() return None or raise?
  |         A: Returns None. We handle it in the route handler.
  |         Q: Is the rate limiter per-user or global?
  |         A: Per-user, keyed by user_id.
  +-- Reviewer: Security
  |     Verdict: Approved (no questions)
```

### 8.4 Polling Behavior

Add `"awaiting_answers"` to the set of states that trigger ReviewPanel polling:
```typescript
const shouldPoll =
  task.status.startsWith("review") ||
  task.review_lifecycle_state === "running" ||
  task.review_lifecycle_state === "awaiting_answers";
```

Question data is fetched alongside review history during polling.

## 9. SSE Events

New event types:

| Event Type | Trigger | Data |
|---|---|---|
| `review_questions` | Reviewer emits questions | `{ questions: ReviewQuestion[], reviewer_focus: string }` |
| `review_awaiting_answers` | All active reviewers paused | `{ total_questions: int, reviewers_waiting: string[] }` |
| `questions_answered` | Human submits answers | `{ answered: int, remaining: int }` |
| `review_resumed` | Review pipeline resumes | `{ review_attempt: int }` |

## 10. Migration Plan

### 10.1 Database

New table `review_questions` -- created via `init_db()` with
`checkfirst=True` (no migration needed for new installs, existing DBs
get the table on next startup).

### 10.2 ReviewLifecycleState

Add `AWAITING_ANSWERS = "awaiting_answers"` to the enum. Update
`REVIEW_LIFECYCLE_TRANSITIONS` to include new valid transitions:

```python
ReviewLifecycleState.RUNNING: {
    ...,  # existing targets
    ReviewLifecycleState.AWAITING_ANSWERS,
},
ReviewLifecycleState.AWAITING_ANSWERS: {
    ReviewLifecycleState.RUNNING,       # resume after answers
    ReviewLifecycleState.NOT_STARTED,   # task moved backward
},
```

### 10.3 ReviewResult Schema

Add optional `questions` field to the JSON schema passed to Claude SDK.
Backward compatible -- existing reviews without questions continue to work.

### 10.4 Frontend Types

```typescript
interface ReviewQuestion {
  id: number;
  task_id: string;
  review_attempt: number;
  reviewer_focus: string;
  question: string;
  context: string;
  answer: string | null;
  answered_at: string | null;
  created_at: string;
}
```

## 11. Edge Cases

| Scenario | Behavior |
|---|---|
| Reviewer asks questions but also has blocking issues | Questions persisted, blocking issues stored as partial state. After answers, resumed review may add/remove issues. |
| Human moves task to BACKLOG while awaiting answers | `AWAITING_ANSWERS -> NOT_STARTED`. Unanswered questions remain in DB but are orphaned. |
| Review timeout while awaiting answers | No timeout on AWAITING_ANSWERS (human-blocked). Timeout only applies to RUNNING state. |
| Multiple reviewers ask questions | All questions shown together, grouped by reviewer. All must be answered before resume. |
| Human answers some questions but not all | "Submit Answers" disabled. Partial saves stored in frontend state only (not persisted until submit). |
| Reviewer asks 0 questions, other asks 3 | First reviewer's result stored normally. Task enters AWAITING_ANSWERS only for the second reviewer. On resume, only the questioning reviewer re-runs. |
| Skip questions | Review resumes with note "Questions were skipped by human -- proceed with available information." |
| Replan after questions answered | Questions from previous attempt preserved in history. New attempt starts fresh (no carried-over questions). |

## 12. Implementation Tasks

Suggested decomposition for implementation (to be added to TASKS.md after
this design is approved):

1. **Backend: Data model + migration** [S]
   - Add `ReviewQuestionRow` to db.py
   - Add `AWAITING_ANSWERS` to ReviewLifecycleState
   - Update transition map
   - Add questions field to ReviewResult JSON schema

2. **Backend: API endpoints** [S]
   - GET questions, POST answer, POST answer-all, POST skip
   - Wire to HistoryWriter or new QuestionWriter

3. **Backend: Pipeline integration** [M]
   - Parse questions from reviewer output
   - Implement pause/resume logic in `review_task()`
   - Create `review_resume.md` prompt template
   - Emit new SSE events

4. **Frontend: Question UI in ReviewPanel** [S]
   - Question cards with answer textareas
   - Submit/Skip buttons
   - AWAITING_ANSWERS state rendering

5. **Frontend: Q&A history display** [S]
   - Collapsible Q&A section in review attempt groups
   - Fetch questions alongside review history

6. **Integration testing** [S]
   - End-to-end: reviewer asks -> human answers -> review resumes -> verdict
   - Edge cases from Section 11

## 13. Open Questions (for User Review)

1. **Question limit per reviewer**: Design says max 3. Should this be
   configurable in `orchestrator_config.yaml`?

2. **Multi-turn follow-up (V2)**: Should the agent be able to ask follow-up
   questions after seeing answers, or is single-round sufficient for V1?

3. **Question priority/severity**: Should questions have a severity indicator
   (e.g., "blocking" vs "nice to know") to help the human prioritize?

4. **Notification**: When questions arrive, should there be a toast/sound
   notification in the UI beyond the SSE-driven state change?

5. **Auto-answer from codebase**: Future enhancement -- should the system
   attempt to answer questions automatically by reading the codebase before
   showing them to the human?
