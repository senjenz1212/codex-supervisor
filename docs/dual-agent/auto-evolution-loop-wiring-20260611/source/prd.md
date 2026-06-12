# PRD: Auto-Evolution Loop Wiring

## Problem Statement

Phases A through D added the mechanics for supervisor auto-evolution: failure-signal experiment drafts, report-only AutoResearch execution, policy overlay proposals, lesson feedback, and quality audits. The mechanics are useful only if they are reachable from normal supervisor operation. Today several pieces remain manual-only: a human must call the generator, runnable experiments do not execute on a daemon cadence, accepted AutoResearch reports do not reliably draft proposals at report emission, the P11 audit is not scheduled, and operator verbs are incomplete in the AXI CLI.

The product problem is liveness without unsafe autonomy. The supervisor should notice repeated failures and draft experiments by itself, execute only operator-activated experiments on cadence, auto-draft policy proposals from accepted evidence, and keep final authority with existing gates, typed outcomes, reviewers, and human approval.

## Solution

Wire the verified mechanisms into the normal supervisor lifecycle:

- Workflow finalization drafts AutoResearch experiments from recurring ledger signals.
- The daemon runs activated experiments on an interval and respects weekly caps.
- AutoResearch report emission derives draft policy proposals from accepted, derivable reports.
- The daemon runs due weekly P11 audits.
- AXI exposes operator verbs for experiment activation and proposal approve/deny.
- Workflow finalization records lesson-injection feedback.
- Request-path guard tests prove MCP and CLI verbs do not spawn workers or drive workflow phases.

All autonomous steps stop at draft or report-only execution. The only human touchpoints are activating a draft experiment and approving or denying a drafted proposal.

## User Stories

1. As an operator, I want repeated supervisor failures to create draft experiments automatically so I can review candidates without manually mining the ledger.
2. As an operator, I want activated experiments to run from the daemon so request paths stay fast and transport-safe.
3. As an operator, I want accepted AutoResearch reports to create draft policy proposals so useful evidence is not stranded in reports.
4. As an operator, I want AXI verbs for activation and proposal decisions so the operational path is scriptable and ledger-backed.
5. As a reviewer, I want lesson feedback and P11 audits recorded from normal lifecycle hooks so trend and lesson hygiene become measurable.

## PRD Promise Contracts

P1. Finalization drafts AutoResearch experiments from recurring failure signals.

- User-visible promise: A workflow result finalization pass calls the AutoResearch generator and records draft-only experiment rows from recurring failure signals.
- Representative action: Finalize a run with three matching taxonomy failures for one task class and gate.
- Public boundary: `CodexSupervisorMcpAPI._workflow_result`.
- Allowed outcomes: Exactly one draft experiment is generated for the recurring signal; below threshold generates none.
- Forbidden outcomes: No human-only generator requirement; no runnable status by default; no gate advancement.

P2. Daemon executes only operator-activated AutoResearch experiments on cadence.

- User-visible promise: A long-lived daemon task executes runnable experiments on cadence and skips draft experiments.
- Representative action: Call `AutoResearchRunnerTask.tick_once` with one draft and one activated experiment.
- Public boundary: `supervisor.autoresearch.daemon_tasks.AutoResearchRunnerTask`.
- Allowed outcomes: Activated experiment executes through the existing runner with weekly cap applied.
- Forbidden outcomes: MCP or CLI request path execution; draft auto-activation; cap bypass.

P3. Accepted derivable AutoResearch reports draft policy proposals at report emission.

- User-visible promise: Accepted, derivable AutoResearch reports produce draft policy proposals automatically when the report is emitted.
- Representative action: Run `run_autoresearch_fixture` on a report with an accepted evaluator-backed overlay candidate.
- Public boundary: `supervisor.autoresearch.orchestrator.run_autoresearch_fixture`.
- Allowed outcomes: One proposal event with source `autoresearch_deriver` and status `draft`.
- Forbidden outcomes: Gaming-flagged, non-positive, or non-derivable reports create applyable proposals; automatic policy mutation.

P4. Weekly P11 audit runs from the daemon and remains observational.

- User-visible promise: Due accepted execution or outcome runs are sampled by a daemon audit task.
- Representative action: Call `WeeklyP11AuditTask.tick_once`.
- Public boundary: daemon task plus state candidate-run query.
- Allowed outcomes: Audit result/trend event is recorded observationally.
- Forbidden outcomes: Audit blocks gates, mutates policy, or runs from request paths.

P5. AXI exposes experiment activation and proposal approve/deny operator verbs.

- User-visible promise: AXI supports `experiments list`, `experiments generate`, `experiments activate`, and proposal-aware `approve`/`deny`.
- Representative action: Run AXI command handlers against a seeded state database.
- Public boundary: `mcp_tools.codex_supervisor_axi.main`.
- Allowed outcomes: Activation changes draft to runnable; proposal approve applies recorded overlay diff with hashes and rollback pointer; proposal deny records denial only.
- Forbidden outcomes: Proposal decisions without human operator fields; generic approve/deny behavior regresses.

P6. Workflow finalization records lesson feedback and retires repeated no-benefit lessons.

- User-visible promise: Injected lessons receive feedback counters when a run finishes, and repeated no-benefit recurrence retires the lesson from future selection.
- Representative action: Finalize a run three times with an injected lesson whose taxonomy recurs.
- Public boundary: `_workflow_result` and `state.record_supervisor_lesson_injection_feedback`.
- Allowed outcomes: Injection and recurrence counts update; retired lesson is excluded from `query_supervisor_lessons`.
- Forbidden outcomes: Lessons become gate authority; stale lessons keep getting injected after retirement.

P7. New MCP and AXI request paths never execute workflow phases or spawn workers.

- User-visible promise: New MCP and AXI verbs never call dispatcher `run_once` or spawn workflow worker processes.
- Representative action: Monkeypatch dispatcher and spawn seams to raise, then invoke new request-path verbs.
- Public boundary: MCP API methods and AXI CLI verbs.
- Allowed outcomes: Calls return ledger reads/writes only.
- Forbidden outcomes: Any request path executes workflow phases, starts subprocesses, or blocks on long-running workers.

## Implementation Decisions

- Add `AutoResearchRunnerTask` and `WeeklyP11AuditTask` as daemon-owned loops with testable `tick_once` methods.
- Keep `generate_autoresearch_experiment_drafts` draft-only and use existing recurrence threshold/cap configuration.
- Add a stricter derivation trigger so report emission calls policy proposal derivation only when a report contains a derivable accepted record.
- Keep `approve` and `deny` generic behavior when `--proposal-id` is absent; proposal-aware behavior is opt-in.
- Keep all metrics, audits, drafts, lesson feedback, and policy proposals observational or human-approved.

## Testing Decisions

- Use public-boundary tests for `_workflow_result`, daemon task ticks, AXI `main`, and `run_autoresearch_fixture`.
- Use negative tests for below-threshold signals, draft skip, weekly cap, gaming/negative derivation, proposal denial, and request-path execution.
- Avoid live model or launchd dependency by using deterministic fakes for daemon tick tests.
- Preserve existing runtime evidence, overlay, quality trend, MCP, and workflow-driver tests.

## Out Of Scope

- Changing the policy overlay schema.
- Auto-activating experiments.
- Auto-applying policy proposals.
- Replacing the existing gates or reviewer panel.
- Adding a new runtime or transport for AutoResearch.
