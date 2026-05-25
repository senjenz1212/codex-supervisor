# Grill Findings

## Finding 1

status: resolved

Concern: Recording tool args/results can leak secrets or raw prompts.

Resolution: Store only bounded summaries, counts, probe statuses, paths already
present in artifacts, and receipt IDs. Keep raw model text in existing redacted
fixtures rather than duplicating it into every tool call.

## Finding 2

status: resolved

Concern: A workspace snapshot can accidentally become a full archive.

Resolution: Record hashes, git status, HEAD, source artifact hashes, and bounded
diff metadata. Exclude `.git`, env files, caches, and common secret paths from
file-tree hashing.

## Finding 3

status: resolved

Concern: `triage.md` could become another long transcript.

Resolution: Keep it as an opinionated first page: verdict, root cause, status
split, top tool calls, evidence pointers, and next safe action.

## Finding 4

status: resolved

Concern: A failed live `/lead` invocation can be incorrectly relabeled as a
receipt-verification failure because P11 runs downstream.

Resolution: Choose the primary blocking gate probe before P11, mark claim
verification as not reached when no outcome exists, and regression-test the
timeout/no-outcome path.
