# Changelog

## [Unreleased]

### Added / Changed / Fixed

- Added a Docker-aware RAG evaluation suite with deterministic frozen retrieval regression, production-shaped replay, synthetic end-to-end judging, reproducibility manifests, comparison signatures, persisted run details, and bounded background jobs.
- Added RAG evaluation APIs and dashboard workflows for starting jobs, restoring terminal jobs after restart, filtering history, and expanding per-question diagnostics.
- Hardened retrieval evaluation status handling, stable chunk identity, frozen configuration snapshots, failure accounting, reranker cache isolation, and Docker dataset fallback behavior.
- Updated the Docker deployment and project analysis documentation to describe the two dashboard layers, metric formulas, execution profiles, reproducibility limits, and remaining production boundaries.
