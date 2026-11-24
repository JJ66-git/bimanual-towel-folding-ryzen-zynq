# Cooperative visual direction

This is a code-led replacement of the prior LCD diagnostic list, scoped to FPGA cooperative monitoring and its read-only companion page. Modes: Operate for the operator, Read for competition judges. No approved raster comp is being copied.

The user's requirements are: 4.3-inch native800×480 panel mounted portrait (logical480×800); important measurements retained; meaningful state outcomes prioritized; FPGA's independent work and multi-end cooperation visible; real task-driven changes; no AI-purple/blue look. Their physical feedback requires larger small text and no low-contrast red text on black.

Implementation direction: warm paper/charcoal, vermilion markers, acid-green verified activity, authored chip/wire geometry, strong Chinese result headline. The hero is a state outcome rather than a large decorative metric. The LCD keeps judgment, collaboration, PL-observed stage, constraint result, visual evidence, sample count, feedback age, PL compute and PS service. Boxed labels use18–24px, boxed verdicts28px, feedback34px, primary judgment40–44px; phase index digits16px and release/clock footer14px. User feedback explicitly enlarged the box text beyond the first16px implementation. White text carries status on dark backgrounds.

The companion expands input vectors, timing records, event history and actual LCD frames. It never sends task or robot packets. Host claims cannot set PL results; probes never count as tasks; absent/stale values stay unknown/historical. Synthetic data is explicit, produced by actual UDP/PL only in the actual-board demonstration; separate browser-render fixtures are explicitly marked.

The scope does not redesign the existing camera/SmolVLA main window or claim a connected camera/arm. No modeled NPU utilization, invented closed-loop success, or touchscreen support is represented.

Verification uses the actual PYNQ2.7/Pillow7 board, not just the PC renderer. Captured LCD images are board-submitted framebuffers with snapshot SHA association, not photographs. Reviewer inputs should distinguish actual-board SYNTHETIC captures from render-only fixture previews.

## Direction contract

THESIS: A result-led cooperation monitor: judges see what the independent FPGA concluded and where the procedure is, rather than a register dump.

OWN-WORLD: Warm paper and charcoal, vermilion current phase, acid-green verified/completed marks, Chinese result typography and exact chip/wire geometry.

STORY: Read the current judgment, follow AI PC → FPGA → displays, locate the explicit current phase, inspect the evidence, then optionally expand actual measurements on the PC.

FIRST VIEWPORT: Portrait LCD uses a large judgment at top, three cooperation nodes, explicit stage highlight, two constraint/feedback boxes, visual evidence and three key measures. Companion uses a judgment and chip-flow first viewport with event and detail routes. Freeze/export are read-only actions.

FORM: Code-led industrial/editorial monitor. No seed assignment or approved comp is asserted: the launcher refused execution, context was read directly, and the concept-seed workflow was not run. The warm visual world is an implementation decision, not a falsely claimed user pin. Current refinements follow the user's direct requests on physical size, current phase, box typography and padding.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Quality bar

A judge can identify the present phase from both literal text and the highlighted node; an old sample cannot impersonate current state; synthetic inputs cannot impersonate a robot run. Every important LCD result is legible at the actual480×800 canvas without touching its frame. Browser interaction, source identity, error, stale, mobile, and export states are verified; full skill concept-seeding and future camera/robot field validation are not claimed.
