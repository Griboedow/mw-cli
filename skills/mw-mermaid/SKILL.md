---
name: mw-mermaid
description: Author Mermaid diagrams in MediaWiki when the target wiki has a compatible Mermaid extension.
---

Use `mw-cli capabilities` to identify the extension; read configured guidelines and an existing diagram page.
Use CLI help for reads/writes. The SemanticMediaWiki/Mermaid extension uses `{{#mermaid: ... }}`,
not a Markdown fence. Confirm the installed variant before choosing syntax.

```text
{{#mermaid:flowchart LR
  A[Request] --> B[Validate]
  B --> C[Save]
}}
```

Keep node IDs stable and labels short. Wiki parser pipes/braces can interfere with Mermaid labels;
follow the installed extension's conventions rather than blindly pasting Markdown examples.
Preview using `mw-cli parse TITLE --file diagram.wiki`; inspect browser rendering when available
because parser success does not validate Mermaid JavaScript. Preserve surrounding page content and
save using the original revision. If unsupported, provide a local `.mmd` source instead of inventing tags.
Reference: https://github.com/SemanticMediaWiki/Mermaid/blob/master/docs/USAGE.md
