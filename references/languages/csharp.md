# C#
- Prefer capability-local concrete services, records, intentional public APIs and nullable-aware contracts.
- Preserve exception, nullability and DI/container behavior, including reflection consumers.
- Own disposables with using/await using; trace CancellationToken and await spawned Tasks through shutdown.
- Avoid sync-over-async and unobserved async void except required event handlers.
- Split projects and interfaces only for actual consumers, substitution, build or deployment boundaries.
