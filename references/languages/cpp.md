# C++
- Prefer RAII, value semantics and unique ownership; do not introduce dangling views or raw owning pointers.
- Trace span/string_view lifetimes, move/borrow semantics, exceptions and noexcept guarantees.
- Split targets/libraries only for actual consumers or build boundaries; keep APIs small.
- Async tasks and threads require ownership, stop, join and observed failure.
- Respect the project's language standard and error policy when recommending replacements.
