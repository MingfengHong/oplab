# Governance and attribution

## Project license

Oplab source is licensed under `AGPL-3.0-or-later`. Contributions are accepted
under the same terms unless a signed agreement says otherwise. Deployments that
modify Oplab and make it available over a network must follow the corresponding
source obligations in the license. This document is an engineering policy, not
legal advice.

## Dependency policy

Dependencies must have an SPDX-identifiable license compatible with an AGPL
distribution and a recorded version in `uv.lock` or `pnpm-lock.yaml`. Custom
licenses, model weights, datasets, fonts, icons, and copied interface assets need
an explicit review; a permissive repository-level license is not enough evidence.

The project may use MIT and Apache-2.0 components with attribution. Code carrying
field-of-use, hosted-service, logo, or commercial restrictions must not enter the
repository without a written compatibility decision. Multica is a clean-room
product reference only.

## Source attribution

Every ingested research source records its canonical URI, authors when available,
publication date, content hash, access/license status, and stable passage
locators. Reports cite source labels generated from these records. A Writer may
not create a new label or cite an unbound passage.

## Generated artifact attribution

Generated memos retain `run_id`, `thread_id`, `trace_id`, model identifier,
content hash, and source IDs in their `Artifact.provenance`. Model-generated text
does not transfer ownership of upstream sources or relax their citation terms.
Users remain responsible for checking publication and dataset licenses before
external redistribution.

## Release evidence

Before a release:

1. run Python tests, Ruff, TypeScript checks, and the Next.js production build;
2. export an SBOM from the locked Python, npm, and container dependency trees;
3. review new dependency licenses and third-party notices;
4. verify a clean database can migrate to the release schema;
5. exercise the interrupt/resume workflow and citation verifier.
