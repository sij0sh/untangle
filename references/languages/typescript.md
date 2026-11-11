# TypeScript
- Preserve runtime validation at trust boundaries; types are not validation. Avoid unchecked `any`/assertions on ingress.
- Prefer capability-local functions and small public module surfaces; split packages only for real consumer/build boundaries.
- Use discriminated unions for states/results; interfaces only for real consumer contracts.
- Trace promise ownership, awaiting, cancellation, shutdown, and rejected work before classifying an async change as A.
- Check strictness and unchecked-index/optional-property behavior against project config before proposing changes.
