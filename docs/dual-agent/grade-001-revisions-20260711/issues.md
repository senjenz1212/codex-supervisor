# Issues: GRADE-001

## Slice 1: Append a pinned grade revision

- Accept the existing `Grade` shape.
- Bind it to `RunEnvelopeRef`.
- Persist canonical detached evidence and return `grade_id` plus revision hash.

## Slice 2: Regrade with linear supersession

- Append revision N+1 for the same exact run envelope.
- Preserve all prior revisions.
- Reject duplicate roots, cross-envelope supersession, and branch attempts.

## Slice 3: Append invalidation lineage

- Atomically invalidate a superseded revision.
- Support explicit reasoned invalidation.
- Keep all invalidation records queryable by grade.

## Slice 4: Validate decision citations

- Require exact grade and revision hashes.
- Reject stale citations by default.
- Permit stale use only when every immutable invalidation hash is acknowledged.

