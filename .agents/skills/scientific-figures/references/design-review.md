# Design and review

Communication guidance is distilled from the bundled Nature Reviews artwork guide, physical pages 1-5. The workflow and reporting conventions are this skill's own operational choices.

## Planning

State the scientific takeaway in one sentence. Identify what the reader needs to compare, locate, or follow. Choose a diagram for processes or relationships, a chart for numerical evidence, or a table/list when categorization is the main job. Mixed figures need both conceptual and quantitative checks.

Establish audience, evidence, final medium and dimensions, and target publication. Record unavailable inputs rather than inventing defaults.

## Checklist

| Area | Review question |
| --- | --- |
| Message | Does the figure support the narrative and distinguish supported claims from hypotheses? |
| Flow | Is reading order clear, usually left to right or top to bottom? Does a circular layout express a cycle or have a clear focal point? |
| Hierarchy | Do position, contrast, colour, and detail emphasize scientific importance rather than decoration? |
| Editing | Can repetition, redundant steps, or ornament be removed without losing necessary meaning? |
| Labels | Are objects, abbreviations, symbols, encodings, and panel labels defined in the figure or caption? |
| Relationships | Are arrows and line styles consistent and explained, without implying unsupported direction or causality? |
| Consistency | Do related shapes, colours, orientations, and ordering retain meaning across panels and figures? |
| Colour | Does colour encode focus or grouping deliberately? Can labels or other cues communicate the same meaning? |
| Accessibility | Are important elements distinguishable and readable at final size? Is contrast adequate for the actual context? |
| Density | Do panels have sufficient space? Would splitting the figure improve comprehension? No universal panel limit applies. |
| Images | Is native resolution sufficient at final size? Are scale bars and editable labels provided where applicable? Upsampling cannot restore detail. |
| Caption | Are panels, sources, abbreviations, encodings, and relevant quantitative definitions explained? |
| Export | Are there clipping, overlap, missing-glyph, rasterization, or editability problems? |

Inspect all relevant rendered panels at intended size when possible. A colour-blindness simulation provides supporting evidence, not universal accessibility certification.

[W3C non-text contrast guidance](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html) generally specifies at least 3:1 contrast for meaningful graphical parts against adjacent colours in applicable web content, subject to exceptions. Do not apply this as a blanket requirement to every colour pair in print, or claim complete WCAG conformance from one check. Without exact colours and appropriate measurement, mark measured contrast `not assessed`; describe visible concerns separately.

## Design output

Return Markdown with:

- **Message and audience:** Main takeaway and intended reader.
- **Evidence and constraints:** Available material, medium, dimensions, destination, and unknowns.
- **Figure type:** Diagram, chart, table, or combination, with rationale.
- **Panels and reading order:** Each panel's purpose and arrangement.
- **Encodings and labels:** Meanings of colours, shapes, arrows, axes, and symbols.
- **Caption outline:** Definitions and context required for interpretation.
- **Open questions and verification:** Unresolved choices and checks after rendering.

Use `unknown` where appropriate. A proposal is not a completed figure or validated scientific result.

## Review output

Begin with **Overall assessment** and **Evidence inspected**. Identify artifact paths or versions, reviewed panels, supplied data, and whether final-size visual inspection occurred. Scope readiness claims to this evidence.

For supported actionable findings, use:

| Severity | Figure/panel location | Observed evidence | Consequence | Concrete correction |
| --- | --- | --- | --- | --- |

Severity values:

- `critical`: Misrepresents or reverses scientific meaning or numerical evidence.
- `major`: Materially obstructs interpretation or violates a confirmed target requirement.
- `minor`: Local clarity or consistency defect with limited impact.

Separate observations from inferences. Do not report personal taste as a defect, invent findings, or assign an unsupported numeric quality score. Say when no actionable findings were found. End with **Not assessed** and **Open questions**, explaining unavailable checks and the evidence needed to resolve them. Review does not authorize editing. Keep the report in the conversation unless saving is requested.
