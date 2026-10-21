# Quantitative charts

Apply alongside design-review.md. These are general review recommendations unless explicitly attributed to a publication. Figure review does not replace scientific or statistical validation.

## Evidence

- Identify series as simulation, measurement, derived quantity, reference data, or schematic trend where ambiguity could mislead.
- Consult actual data, metadata, and plotting code when available. Check quantities, units, normalization, and transformations; visual plausibility alone does not establish correctness.
- For agentic_srim outputs, establish run conditions, depth coordinate, units, normalization, and quantity definitions from actual metadata and output documentation. Do not infer them from filenames alone or silently convert units.
- Preserve original values and data. Changes to binning, filtering, interpolation, aggregation, or statistical treatment need scientific justification and applicable approval.

## Axes and comparisons

- Label quantities and units, or define dimensionless quantities, and explain symbols and series.
- Check scale choice, transformations, axis breaks, clipping, and whether comparable panels use compatible scales. If checking a logarithmic transform, inspect nonpositive source values when available.
- A zero baseline usually matters for magnitude-encoding bars; do not impose it on every line plot or logarithmic axis.
- Ensure comparisons use compatible definitions and units. Identify different run conditions rather than implying a controlled comparison.
- Check whether overplotting or summaries hide relevant variation. Choose line, scatter, distribution, or summary encodings according to the question and available evidence.

## Uncertainty

Define displayed intervals and error bars, their calculation, and sample size or replication unit where applicable. Distinguish independent replicates from repeated observations, bins, or simulation histories when relevant. Do not invent uncertainty models or demand error bars for every deterministic curve.

Consider observations or distributions alongside summaries when variation matters and source data permit. Never manufacture individual observations from summary statistics. Unsupported significance claims are scientific questions requiring evidence, not aesthetic defects.

[Nature's formatting guide](https://www.nature.com/nature/for-authors/formatting-guide) requires definitions of error bars and statistics in legends. The Nature Biomedical Engineering editorial [Show the dots in plots](https://www.nature.com/articles/s41551-017-0079) recommends individual observations particularly for small samples; this is context-dependent guidance, not a universal plotting rule.

## Rendering and publication

Inspect actual rendered labels, ticks, lines, legend placement, and clipping at intended size. If dimensions or rendering are missing, mark final-size legibility `not assessed`.

[Nature's research figure specifications](https://research-figure-guide.nature.com/figures/preparing-figures-our-specifications/) cover axis labels, accessible colour, editable text, image resolution, and export for their destination. Confirm the actual journal and current instructions before claiming compliance. Do not transfer Nature Reviews' conceptual-figure typography, palette, or restrictions on original data to research charts.

Use design-review.md's reporting format, distinguishing visual defects, data discrepancies, statistical questions, and unavailable checks. Web references consulted 2026-10-21; verify current publisher requirements for submissions.
