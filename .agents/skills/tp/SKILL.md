---
name: tp
description: Create an append-only, Git-traceable record around a substantive BUSFIN 8200 problem-set request when the user explicitly invokes TP. Do not use implicitly or for ordinary repository setup.
---

# Traceable Prompt (TP)

Use this workflow only when the user explicitly invokes TP for substantive work related to the BUSFIN 8200 problem set. The invocation authorizes the local commits required below, but does not authorize pushing, rewriting history, or unrelated repository changes.

## Required workflow

1. Confirm the repository and identify the exact problem-set item.
   - Work only in the BUSFIN 8200 problem-set repository containing `Problem Sets AI Policy.pdf`.
   - Extract the item from the request when it is unambiguous.
   - If the item is unclear, ask the user to specify it and do not create the pre-interaction commit or begin substantive work yet.

2. Check the governing policy before substantive work.
   - Read `Problem Sets AI Policy.pdf` closely enough to apply all provisions relevant to the request.
   - If the request conflicts with the policy, explain the conflict and stop before performing the prohibited work.
   - Do not treat these skill instructions as replacing or weakening the course policy.

3. Record the state before the interaction.
   - Inspect `git status` so the user-visible state is understood.
   - Stage the entire current repository state, including pre-existing tracked and untracked project changes, with `git add -A`.
   - Create a commit named `TP pre-interaction: <problem-set item>`. Use `--allow-empty` when there is nothing to commit.
   - Record the full commit hash from `git rev-parse HEAD` as the interaction's pre-interaction commit.
   - Do not push unless the user separately requests it.

4. Resolve substantive ambiguity before implementation.
   - Identify any mathematical, economic, or empirical design decision the request leaves unspecified.
   - Describe the ambiguity neutrally and ask the user to decide before implementing it.
   - Explanations of consequences and feasible options are allowed, but do not silently choose a substantive assumption, model, estimator, sample construction, identification strategy, or interpretation for the user.
   - Preserve the pre-interaction commit while waiting and resume the same interaction after the user decides.

5. Complete the permitted request.
   - Follow the course policy and the user's decisions.
   - Keep a contemporaneous list of files inspected, files directly modified, errors, omissions, ambiguities, substantive suggestions, and assistance categories.
   - Distinguish the user's decisions from suggestions made by the assistant.

6. Append one interaction entry.
   - Use `AI_INTERACTIONS.md` at the repository root. If it does not exist, create it with the single heading `# AI Interactions` before the first entry.
   - Append the new entry at the end of the file. Never delete, combine, reorder, edit, summarize, or rewrite existing entry text.
   - Preserve the user's substantive prompt verbatim, including relevant follow-up decisions. Exclude only the mechanical skill-selection token when it is not part of the substantive request.
   - Use the schema below. Write `None` where a required field has no content; do not omit required fields.

```markdown
## Interaction: <ISO 8601 local timestamp> — <problem-set item>

- **Problem-set item:** <item>
- **Purpose:** <why the user requested assistance>
- **Git commit before interaction:** `<full commit hash>`
- **Assistance categories:** <one or more of: checking mathematics; checking economic reasoning; empirical implementation; code debugging; formatting/translation; other — specify>
- **Files inspected:** <paths, or None>
- **Files directly modified:** <paths, or None>
- **Errors, omissions, or ambiguities identified:** <details, or None>
- **Substantive mathematical, economic, or empirical suggestions:** <details, or None>
- **Grouped minor subsequent requests:** <Yes/No; if Yes, summarize each and confirm same item and work session>

### User's substantive prompt

<verbatim prompt and relevant follow-up decisions>

### Assistance provided

<concise but complete description>
```

7. Verify append-only logging and record the state after the interaction.
   - Review the diff and verify that pre-existing `AI_INTERACTIONS.md` lines are unchanged and the new material appears only at the end.
   - Stage the entire resulting repository state with `git add -A`.
   - Create a commit named `TP post-interaction: <problem-set item>`. Use `--allow-empty` if necessary.
   - Record the full post-interaction commit hash and report both commit hashes to the user.
   - Do not push unless the user separately requests it.

## Grouped minor follow-ups

Group a later debugging or formatting request only when the user explicitly asks to group it, it concerns the same problem-set item, and it occurs in the same work session.

- If the interaction entry has not yet been finalized, include the follow-up in that entry before the post-interaction commit.
- If the entry was already committed, append a clearly labeled addendum at the end of that same entry without altering any existing line, then commit the addendum and related changes. Report the additional commit hash.
- Otherwise, treat the request as a new TP interaction with its own pre-interaction commit, entry, and post-interaction commit.

## Completion check

Do not call the interaction complete until the item is identified, relevant policy is followed, substantive ambiguity is resolved by the user, the assistance is complete, the append-only log entry is present, and the post-interaction commit succeeds. If a commit or logging step fails, report the failure plainly and do not claim traceability is complete.
