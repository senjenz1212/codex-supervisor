You are running one SWE-bench Pro solver attempt in an isolated public worktree.

Instance: `{{instance_id}}`
Repository: `{{repo}}`
Base commit: `{{base_commit}}`
Attempt: `{{attempt_index}}` of `{{attempt_count}}`

Problem statement:

```text
{{problem_statement}}
```

Rules:

- Use only the files visible in this worktree and the public packet below.
- Do not look for hidden tests, oracle scripts, expected outcomes, or dataset
  labels.
- Make the smallest source/test changes you believe solve the problem.
- You may run local tests that are visible in the public worktree.
- Leave your edits in the worktree; the parent solver captures `git diff`.
- Do not create network-dependent reproduction steps unless the repository
  already requires them locally.
- End your final response with exactly one marker:
  `SWEBENCH_SOLVER_DECISION: accept` if you believe the patch is ready, or
  `SWEBENCH_SOLVER_DECISION: reject` if you cannot produce a defensible patch.

Public packet:

```json
{{public_packet_json}}
```
