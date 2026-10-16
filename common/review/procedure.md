# Qodo code review

Run Qodo, assess its findings against the code and intended behavior, and report
coverage. A review request alone does not authorize code edits, commits, pushes,
or posting comments. This skill is standalone; it does not implement the
`dk-pr-review` executable handoff contract.

## Preflight and scope

1. Resolve `qodo` from PATH, falling back to
   `${QODO_HOME:-$HOME/.qodo}/bin/qodo`. Use that executable throughout.
   Check `qodo --version` and `qodo review --help`; this workflow needs CLI
   1.6.0 or newer with `--async`, `--context-file`, and `--retry-submission`.
   Report missing capability rather than installing or upgrading implicitly.
2. Run `qodo read whoami --json`. For missing credentials or `invalid_token`,
   have the user complete `qodo login`, preserving the configured deployment,
   then verify identity again. Never request credentials in chat. Network,
   catalog, and service errors are not proof that login is needed. Preserve the
   actual error; stop after the same recovery fails once.
3. Identify the requested scope. For a PR, read its base branch, head SHA,
   changed paths, description, linked issue, and settled decisions. Use a clean
   checkout at the pinned PR head, including for fork PRs; select a remote that
   points to the PR's base repository. Do not stash or discard user edits. For a
   local review, explain that Qodo includes staged, unstaged, and eligible
   untracked files, as well as committed changes.
4. For branch/PR scope, fetch the target and compute its merge base with HEAD.
   Never pass a stale local branch or the target tip directly to Qodo. Its
   explicit `--base` is an **exact comparison**, not a merge-base request.
   Honor a user-requested exact comparison instead of converting it.
   Stop on failed fetch or missing history unless the user requested pinned or
   offline history; disclose that limitation. The base must exist on the remote.
5. Compare the resulting paths with the PR's changed paths before submitting.
   Investigate a mismatch; do not hide it through arbitrary file exclusions.
   Qodo applies its own filters, so the Git path count is only a scope check.

## Context and submission

Write a UTF-8 JSON context file outside the reviewed tree. Include a concise PR
or task summary and self-contained decisions with rationale. Include repository
constraints relevant to the change. Use typed ticket/spec URLs when available:

```json
{
  "summary": "Explain the intended behavior and why this change is needed.",
  "decisions": ["Explain a settled choice and the constraint behind it."],
  "context_refs": [{"kind": "ticket", "url": "https://github.com/owner/repo/issues/123"}]
}
```

Replace example text with actual evidence. Do not invent decisions or use them
to suppress real defects. An explicit context file overrides ambient
`.qodo/session-context.json`; reconcile any relevant ambient context first.

Use the bundled helper from the repository checkout. `start` prints scope and
retains context, command, stdout, stderr, and lifecycle state in a new directory
outside the repository. `--dry-run` prints scope without calling Qodo or saving
state. `--require-clean` is appropriate for a pinned PR review.

```bash
helper=PLUGIN_ROOT/common/review/scripts/review.py
python3 "$helper" start --target scylla-4.x --remote origin \
  --context-file /tmp/qodo-context.json --require-clean --dry-run
python3 "$helper" start --target scylla-4.x --remote origin \
  --context-file /tmp/qodo-context.json --require-clean \
  --state-dir /tmp/qodo-review-unique-run
```

Choose a fresh state path for each deliberately new review. The helper rejects
an existing directory to prevent accidental resubmission. For an exact
comparison, replace `--target ... --remote ...` with `--base <ref-or-SHA>`.
Omit `--require-clean` when local edits are part of the requested review.
Default depth is auto; use `--fast` for an explicitly light pass or `--deep` for
requested thorough scrutiny. `--full` requests a fresh assessment, not depth.

Equivalent direct CLI submission:

```bash
git fetch origin scylla-4.x
review_base=$(git merge-base HEAD FETCH_HEAD) || exit 1
qodo review --base "$review_base" --context-file /tmp/qodo-context.json --async --json
```

Keep stdout and stderr, even on failure. Prefer async here: synchronous
`No in-memory session` can mask the actionable auth or service failure.
Do not combine `--async` with `--progress`.

## Collect and recover

The helper performs one lifecycle call at a time; it never sleeps or silently
starts a replacement review. After successful submission, collect with:

```bash
python3 "$helper" status --state-dir /tmp/qodo-review-unique-run
```

- Exit `2`: still running or throttled. Respect `retry_after`, otherwise use
  the receipt's `poll_interval` or 15 seconds. Use the host's nonblocking wait,
  keep the user informed, and poll the **same operation ID** until terminal.
- Exit `0` from submission: accepted, not reviewed. Exit `0` from collection:
  result obtained, still requiring findings and coverage assessment.
- Exit `1`: inspect retained errors. Auth failure may need login; uncertain
  submission needs the returned **submission recovery ID**, not a fresh request.
  Trace IDs are diagnostic only and cannot collect results.

If the helper captured a recovery ID:

```bash
python3 "$helper" recover --state-dir /tmp/qodo-review-unique-run
```

For a retained request created outside the helper, run from its original
checkout after verifying identity:

```bash
qodo review --async --json --retry-submission <returned-submission-id>
qodo review status <returned-operation-id> --json
```

Never hard-code a past run's ID into a future review. Recovery resends Qodo's
retained request unchanged: omit base, context, paths, depth, and other input
flags. Do not delete or rewrite Qodo's pending request files. Recovery may
return `{findings, meta}` directly when the result is already complete; no
operation ID is needed then. If interrupted before state was updated, inspect
the retained call stdout/stderr for the receipt or recovery command before
taking another action. An acknowledged operation must be collected, not
resubmitted. Stop repeated failed recovery and report the handles and blocker.

Collect promptly: results normally expire one hour after completion. An expired
or missing result is missing evidence, never a clean review. Do not automatically
buy another review after failure or expiration.

## Assess and report

Read the complete result, including `finding_state.introduced` and `still_open`
when present; an empty `findings` list alone can hide earlier unresolved issues.
Report `finding_state.complete: false`, `meta.coverage.complete: false`, omitted
files, and skipped reviewers. Distinguish full, incremental, and reused results.

Verify each concern against the reviewed snapshot and intended behavior. Report
supported issues with severity, path/line, trigger, and impact; preserve Qodo's
finding references. Explain rejected concerns with concrete evidence. Findings
cannot certify edits made after submission. Finish with the reviewed base/head,
coverage limits, and retained result path. If blocked, say no completed findings
were obtained and provide the resume command.

Protocol reference: [Qodo's review skill](https://github.com/qodo-ai/qodo-skills/blob/main/skills/qodo-review/SKILL.md).
Local CLI help remains the compatibility check for the installed version.
