# Python
- Prefer cohesive modules, explicit public APIs, typed maintained functions, plain values and concrete collaborators.
- Separate ordinary outcomes from operational exceptions; preserve exception causes and public error behavior.
- Resource acquisition needs an owner/context manager; account for cleanup on failure.
- Trace tasks/threads through cancellation, join and failure observation; async/sync changes may alter contracts.
- Inspect import-time I/O, mutable global state and behavior-hiding decorators before simplifying.
