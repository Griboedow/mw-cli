---
name: mw-drawio
description: Create or edit editable draw.io diagrams for MediaWiki installations with DrawioEditor or Flex Diagrams.
---

Use `mw-cli capabilities`, configured guidelines, and a working diagram page to discover the storage format.
Use CLI help for page/file operations. DrawioEditor and Flex Diagrams are different integrations:
- DrawioEditor embeds `<drawio filename="ChartName" />`; inspect an existing backing file and the installed
  extension version to determine its required name and format. Do not assume raw `.drawio` upload suffices.
- Flex Diagrams uses diagram pages/content models and `{{#display_diagram:PAGE}}`; inspect an existing
  Drawio namespace page. Newer installations may store SVG rather than native XML.

Author editable, uncompressed `mxfile/diagram/mxGraphModel` XML with unique cell IDs, root cells 0/1,
explicit geometry, and edges referencing existing nodes. Escape XML attributes. Preserve IDs on updates.
Validate XML locally and inspect the diagram in diagrams.net or a compatible renderer when available.
Preserve the installed format: an SVG/PNG preview without embedded diagram data is not an editable source.
Download/read the original before editing; upload/write through the matching workflow and then verify the
wiki embedding. Parser success alone is not visual verification. If the format cannot be established,
deliver a local `.drawio` artifact rather than claiming a working wiki integration.

References: https://www.mediawiki.org/wiki/Extension:DrawioEditor and
https://www.mediawiki.org/wiki/Extension:Flex_Diagrams
