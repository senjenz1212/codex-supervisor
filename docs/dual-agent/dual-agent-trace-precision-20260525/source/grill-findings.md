# Grill Findings

## Finding 1

status: resolved

Concern: `tool_call_id` could become unstable if it hashes mutable result
summaries.

Resolution: Derive the default id from stable invocation facts: name, start
time, duration, and probe id. Preserve caller-supplied ids.

## Finding 2

status: resolved

Concern: `parent_tool_call_id` can lie if every nearby call is forced into a
tree.

Resolution: Only set parents where the supervisor owns the relationship, such
as runner validators that evaluate a specific Claude invocation.

## Finding 3

status: resolved

Concern: Token fields could be guessed from cost and create false precision.

Resolution: Parse token usage only from reported Claude JSON. Leave values
missing when usage is absent.

## Finding 4

status: resolved

Concern: MAST tests could remain decorative if they only call
`classify_failure` directly.

Resolution: Add trigger-shaped tests through payload classification and
cross-event sequence detection.
