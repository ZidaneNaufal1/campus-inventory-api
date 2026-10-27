# Provenance

Owner/maintainer: Muhammad Zidane Naufal Azzam (ZidaneNaufal1). The implementation, tests, and documentation were developed for this repository with assistance from Ciel/OpenAI Codex on 2026-10-27. Manual authorship of every line, maintainer understanding, actual users, and independent audit are not claimed.

The runtime uses Python's standard library, including sqlite3 and http.server. The local demo and tests create synthetic data and a temporary database; no external dataset or assets are used. The pattern for rejecting foreign SQLite files and stable symlink paths was adapted from the owner's MIT-licensed webhook-event-recorder and http-status-monitor; no inter-repository runtime dependency is required. GitHub Actions checkout/setup-python support CI. Project source is MIT licensed; toolchain terms are separate.
