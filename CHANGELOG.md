# Changelog

All notable changes to this project. This project adheres to semantic versioning.

## v0.2.2 — 2026-11-16

- Attach a CycloneDX SBOM to each release (covered by the signed checksums)

## v0.2.1 — 2026-11-15

- Sign release checksums with Sigstore (keyless) and attach the `.sigstore.json` bundle
- Add property/fuzz hardening tests for the AES-256-GCM + scrypt envelope

## v0.2.0 — 2026-11-14

- Refuse attachment symlinks on Windows too
- Add date filtering to the timeline
- Add attach: reference an external file by SHA-256 (no copy)
- Add Markdown export of a case
- Add a read-only stats summary of a case
- Add full-text search across record titles, bodies and provenance
- Show a live word and character count under the body field
- Docs: point install at the live v0.1.0 release

## v0.1.0

- Initial public release.
