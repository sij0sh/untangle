# C
- Identify allocation, borrowing, output-on-error, and release ownership at every API.
- Preserve status codes, partial progress and failure cleanup; one cleanup path is often preferable.
- Verify buffer size/capacity, overflow, termination, aliasing and initialized output state.
- Keep internal functions static and private state opaque when that reflects the actual consumer boundary.
- Trace thread cancellation, join and failure observation.
