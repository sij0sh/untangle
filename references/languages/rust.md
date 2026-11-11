# Rust
- Prefer small private-first modules; split crates only for actual build, dependency or distribution boundaries.
- Model states with enums, failures with Result, and meaningful absence with Option.
- Trace borrow/clone decisions and ownership transfers; unsafe code needs explicit invariants.
- Each spawned task needs shutdown, join and observed failure; dropping a handle is not proof of completion.
- Never turn fallible production paths into unchecked unwrap/expect while simplifying.
