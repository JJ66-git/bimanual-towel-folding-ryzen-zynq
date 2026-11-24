# FlexiFold cooperative visualization

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Competition judges and the operator looking at the FPGA-attached 4.3-inch portrait LCD and the AI PC. The user's explicit goal is to understand FPGA function and multi-end collaboration through meaningful state results, with essential measured data retained.

## Stack

Existing board path: Python 3.8, PYNQ 2.7, Pillow, 16-color AXI framebuffer. The native panel is 800×480, mounted portrait, with a 480×800 logical canvas. The companion is a local plain HTML/CSS/JavaScript page with a standard-library HTTP bridge on the AI PC. No additional frontend framework is needed for this existing Python runtime.

## Product Purpose

Explain and inspect the actual AI PC → PS → PL → display collaboration. The AI PC proposes actions; the PL checks global input constraints and observes grasp/fold phases and event-bound visual evidence. The screen does not authorize robot movement.

## Constraints and explicit user requirements

- No typical AI purple or AI blue visual language.
- Actual task packets and PL results must drive changing views; no fake live curves or fixed pretend results.
- On the small screen, prioritize judgment, collaboration and stage; keep essential metrics, expand details on the AI PC.
- Main operating words are Chinese state outcomes for judges; technical identifiers remain in details.
- Cameras and mechanical arms are not connected. Field vision is not calibrated. HOST mode cannot show successful uncalibrated visual evidence.
- Synthetic input sequences must remain prominently labeled and must be restored to HOST mode afterward.

## Evidence

B35 BUILD_ID 0x46504735, verified 50MHz FCLK, matching phase configuration CRC/SHA, actual FPG1/FVE1/FUI1 packets and PL registers. LCD physical panel identity 0x4384. Model and environment readiness are recorded separately and do not prove current inference utilization.

## Principles

1. State outcomes come before raw telemetry.
2. Every displayed result has a source and freshness.
3. Probe, task, host claim, PL conclusion and synthetic input remain distinct.
4. Display failure must not produce a success result or alter policy actions.
