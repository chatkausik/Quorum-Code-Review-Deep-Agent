# Diagram sources

`quorum-system-overview.mmd` is the source of truth for the presentation
architecture image used by the README, architecture reference, and operations
guide. The detailed Mermaid blocks embedded in those documents remain the
source of truth for exact component, concurrency, and sequence relationships.

Regenerate both checked-in overview assets after changing the source:

```bash
npx --yes @mermaid-js/mermaid-cli@11.16.0 \
  -i docs/diagrams/quorum-system-overview.mmd \
  -o docs/images/quorum-system-overview.png \
  -w 2200 -H 1400 -b white -s 1.25

npx --yes @mermaid-js/mermaid-cli@11.16.0 \
  -i docs/diagrams/quorum-system-overview.mmd \
  -o docs/images/quorum-architecture.png \
  -w 2200 -H 1400 -b white -s 1.25
```

Visually inspect both outputs before committing. The overview must show all
three persistence layers, distinguish trusted SQLite records from bounded
untrusted Mem0 context, and preserve the explicit human posting gate.
