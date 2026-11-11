# Java
- Group by capability; prefer concrete services and records, with interfaces only for real consumers.
- Preserve constructor and nullability contracts, specific exception causes, and transactional behavior.
- Own closeables via try-with-resources and asynchronous work via tracked cancellation and joins.
- Treat framework annotations, reflection and dependency injection as consumers even without explicit imports.
