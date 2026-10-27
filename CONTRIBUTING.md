# Contributing

Use Python 3.10 or newer, run `python -m unittest -v` and `python examples/run_demo.py`, and keep tests limited to localhost and synthetic data. A symlink test may skip if the OS does not allow symlink creation. The project has no runtime pip dependencies.

For a bug report include the Python/OS version, a small synthetic reproduction, expected and actual behavior, and the relevant command. Do not post real student names, databases, credentials or private files to public issues.

Discuss significant schema or API changes first. Preserve the transactional stock check and duplicate-loan invariant, include tests for meaningful behavior and failures, and update OpenAPI and documentation with changed behavior. Never expose this unauthenticated local MVP in public demos. Credit third-party work and disclose AI assistance where used.
