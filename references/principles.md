# Simplification principles

## Authority

Prefer, in order: explicit requirements and established adopted decisions → actual project conventions and contracts → these defaults and applicable language profile → simplest adequate reversible option. Prior decisions are context, not proof of correctness; flag evidence that makes one worth revisiting.

## Tests for avoidable complexity

- **One authority.** A business rule, state mutation, and side-effect lifecycle have one owner. Others use the owner's public contract, not its internals.
- **Directed dependencies.** Avoid cycles and back-channels. Separate responsibilities only at real capability, dependency, consumer, build, or deployment boundaries.
- **Local reasoning.** Understand behavior from owner, inputs, and dependencies without reconstructing unrelated systems. Prefer ordinary functions, explicit data, and composition over incidental frameworks.
- **Explicit boundaries.** At a trust or I/O boundary identify inputs, outputs, validation, errors, side effects, cleanup, and resource lifetime. Validate untrusted data there rather than repeatedly inside trusted code.
- **Proportional structure.** New layers, packages, interfaces, wrappers, and abstractions must address current—not hypothetical—needs. Duplicating a small helper across unrelated capabilities may be safer than coupling them; authoritative business rules must not drift.
- **Consumer value.** A component exists for an actual consumer or capability. Check all forms of consumption before proposing removal.

## Evidence and recommendation

For each candidate, name `path:line`, problem, owner/consumer impact, current observable behavior, smallest sufficient change, and uncertainty. Prefer root-cause fixes over a list of cosmetic edits. Characterize behavior before proposing Track A; any changed output/error/order/retry/validation/format/export/async/cleanup is Track B. Missing proof is a gap. Preserve alternatives when a tradeoff needs a user decision.

Prefer fewer concepts, fewer contracts, and less distant reasoning—not merely fewer lines or files. Avoid recommendation-only churn that adds indirection.
