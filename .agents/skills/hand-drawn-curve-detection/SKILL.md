---
name: hand-drawn-curve-detection
description: Process a photographed or scanned hand-drawn curve into traceable numerical coordinates and a diagnostic overlay for analysis or optimization. Use for curve digitization and target preparation, not decorative image editing or generating replacement curves.
---

# Hand-drawn curve detection

This is a temporary project-local host skill available through normal Codex discovery and explicit or implicit invocation. It may later be replaced through the self-improvement service or implemented partly as an Analysis MCP method. It supplies the processing workflow, not a preinstalled image-processing tool. Creating this skill does not install dependencies or register a method.

## Establish the intended input

Inspect the supplied original image before processing. Determine which curve or curves are intended and whether the deliverable is a function y(x), ordered parametric paths, or qualitative target shape. Identify axes, plot bounds, units, linear/log scales, calibration marks and the downstream consumer's schema/tolerance. Ask only about ambiguities that materially affect extraction or interpretation.

Preserve the original image bytes and identify dimensions and source/checksum when producing artifacts. If axis values are absent, return pixel coordinates or explicitly defined normalized coordinates. Never invent physical scales, unit labels, logarithmic axes or scientific accuracy. A sketch records intended shape, not an empirical measurement with known error.

## Prepare the image without changing its meaning

Use available deterministic image-processing libraries or tools; inspect the environment before choosing them. Missing dependencies are a capability gap, not permission to install packages. In this project use the required virtual-environment interpreter. Keep temporary intermediates in a bounded disposable workspace and retain only requested outputs and necessary provenance.

- Crop the plot region and correct rotation or perspective only when supported by visible geometry/control points. Retain the forward/inverse coordinate transforms and calibration residuals.
- Use color separation where strokes differ from axes/grid; otherwise use grayscale/background correction and thresholding appropriate to lighting and stroke contrast. Tune on visible evidence.
- Remove labels, grid and axes with masks tied to their detected positions. Keep intersecting curve pixels as ambiguous when separation is uncertain; indiscriminate line removal can erase plateaus or steep segments.
- Apply denoising or morphology conservatively relative to stroke width. Do not connect separate branches, close real gaps, erase extrema or fabricate missing segments. Preserve diagnostic masks when needed to explain uncertain results.

Do not use generative redrawing, inpainting or image enhancement to create numerical evidence. If a display-only illustration is requested separately, keep it distinct from the extraction source.

## Extract and calibrate

Trace connected stroke centers or skeleton paths, separating individual curves and segments. Track continuity and stroke identity through crossings; flag unresolved junctions instead of silently selecting a branch. Overhangs/loops require ordered parametric paths unless the user explicitly chooses one branch for y(x). Preserve genuine discontinuities.

Define coordinates explicitly: source-image origin is top-left with pixel y increasing downward; Cartesian plot y normally increases upward. For a rectified plot with bounds u_left/u_right and v_top/v_bottom, normalized coordinates may be x_n=(u-u_left)/(u_right-u_left), y_n=(v_bottom-v)/(v_bottom-v_top). Record these bounds and reject degenerate calibration.

For numeric axes, calibrate each axis from labelled anchors: fit value=a*pixel+b for linear scales or log(value)=a*pixel+b for logarithmic scales, then invert the log transform. Log anchors must be positive; retain the chosen base and calibration evidence. Use at least two distinct anchors per axis and additional visible anchors for residual checks. Perspective correction precedes axis mapping. For broken, nonlinear or curved axes, use an explicitly supported calibration or report the limitation.

Retain raw extracted samples separately from smoothing, interpolation or resampling. Apply those transformations only to meet a stated downstream need; report the method and parameters and preserve peaks and discontinuities. Do not extrapolate outside the observed domain or fill unobserved gaps silently. Numerical normalization for a loss function is separate from image-coordinate normalization and needs its own declared rule.

## Check usability

Overlay the extracted paths on the original image using the inverse transforms and visually inspect endpoints, crossings, steep regions, plateaus, gaps and extrema. Check numeric range, units, calibration anchors/residuals, finite values and branch order; require increasing unique x only when the consumer needs a single-valued function. Report coverage and unresolved/occluded regions.

Estimate positional uncertainty from stroke thickness, pixel resolution, calibration residuals and ambiguous segmentation when possible. Propagate it to axis values, including spatially varying log-scale error. Label estimates and unknowns; a heuristic extraction score is not a statistical confidence interval. Compare extraction error to the downstream tolerance before claiming the input is viable. If tolerance is unspecified, report measured/estimated error and conditional usability.

## Deliver

Return numerical samples in the requested CSV/JSON/table schema, a source-image overlay and compact provenance: original identity, selected curve/segment IDs, coordinate system/units, calibration, transforms, processing settings, gaps, uncertainty and any derived smoothing/resampling. For example, a table may carry curve_id, segment_id, sample_order, x and y, plus uncertainty/quality fields when supported; choose actual columns to fit the consumer.

State whether the result is calibrated numerical input, normalized qualitative shape, or requires user correction. Request a clearer image or anchors only when available evidence cannot support the intended use. Preserve unresolved ambiguity rather than presenting guessed samples as usable measurements. A usable extracted target still needs a defined comparison/loss and acceptance tolerance before adaptive optimization.
