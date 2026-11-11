# Go
- Prefer cohesive packages, concrete structs, explicit errors; define small interfaces at consumer boundaries.
- Preserve `%w` error wrapping, `errors.Is`/`errors.As` behavior, and context cancellation semantics.
- Every goroutine needs an owner, shutdown, join, and observed failure; cancellation is not completion.
- Treat `Close` failures, deferred cleanup and exported identifiers as part of the contract.
