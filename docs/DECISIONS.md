# Architecture Decisions

This file records decisions that future sessions should not rediscover from scratch.

## 2D First

The first simulator view is a 2D GTK drawing surface. 3D rendering and camera controls are deferred.

## NumPy Physics

The physics core uses NumPy arrays for vector math and performance. NumPy is packaged into the Flatpak manifest because Builder's sandbox cannot use host Python packages.

## First Post-Newtonian Relativity

The app uses a practical first post-Newtonian approximation for solar-system scale simulation. Full numerical relativity is out of scope for the interactive first implementation.

## User Step Versus Integrator Step

The UI `Days / step` value is the visible simulation interval. It must not be used directly as one large integrator step. Use `physics.advance()` for final-state-only callers and `physics.advance_with_samples()` for UI playback.

## Selectable Gravity And Integration

Gravity model and integrator are independent persisted system settings. Post-Newtonian Velocity Verlet preserves the original behavior and remains the migration default. Newtonian gravity provides classical invariant checks, while RK4 provides a higher per-step-accuracy alternative that works with the velocity-dependent 1PN acceleration. Worker job plans capture both settings so main-thread changes cannot alter an in-flight job.

Velocity Verlet remains the recommended long-duration option; RK4 is not claimed to be symplectic. Auto policy estimates RK4 at twice the current integrator work because it uses four rather than two acceleration evaluations per substep.

## Conservation Diagnostics

Energy and angular momentum are measured in the center-of-mass frame against a loaded/saved-state baseline. Newtonian energy is an exact model invariant; in 1PN mode it is labeled as a health-check proxy. Approximate physics policies are explicitly identified because their topology changes can create expected drift.

## Trail Sampling

UI trails are sampled from bounded physics substeps, not just from the final position of each visible UI step. This keeps high `Days / step` playback from drawing sparse line segments that make inner orbits look artificially angular or rosette-like.

Focused Parent is the default trail perspective during Focus and Fit. Body focus subtracts the focused body's position at each recorded sample; group focus subtracts the focused group's same-sample mass barycenter. The canvas re-anchors those relative points at the current reference position. Overview and inset trails stay inertial, and changing frames clears trails and invalidates pending playback work. Configured orbit guides are a separate dashed display layer built from provenance metadata; they never replace canonical Cartesian state or recorded trails.

Selecting a descendant of the active Focus and Fit target changes inspection selection without changing camera state, focus bounds, zoom, or trails. Selecting outside the focused target exits focus.

## Canvas View State

Fixed Scale saves the mode per system but keeps the exact center, physical scale, pan, and 3D camera session-only. Each renderer captures its own linear view. Configured orbit/trail visibility and style are persisted, while toggling them never clears trail history or changes physics.

## Local JSON Library

User-created systems are stored as JSON in the app data directory. Portable JSON imports always become new editable copies with regenerated linked IDs and collision-safe names, so they cannot overwrite local systems or masquerade as bundled presets. Export writes the current simulation vectors from a clone and does not change library or dirty state.

## Bundled Presets

Bundled systems are versioned JSON data. They are read-only templates. Editing requires an explicit duplicate so Save never silently changes system identity.

## Canonical State and Reference Frames

Cartesian body vectors are canonical. Orbital elements and sources are provenance. Schema v9 records a body `state_origin` and one system-level reference frame. Reference-frame edits are permitted only through an atomic rigid transform: subtract one origin position/velocity, apply one proper rotation to every canonical vector, and rotate body/group orbital and flyby orientations into the target plane. Guided rotations use `Rz @ Ry @ Rx`; expert matrices must be finite, orthonormal, and have determinant `+1`. Epoch and time scale remain fixed.

## Reset Sources

The quick reset restores the snapshot captured on load or save. Explicit source resets reload editable systems from the local library or active bundled systems from packaged preset data. Saved preset-derived copies do not keep template lineage.

## Horizons Requests

Live JPL Horizons import is limited to Sol systems with a compatible frame. Requests use one executor and a serialized client, validate the API signature and major version, and return immutable drafts. Playback stops before the request; its elapsed time offsets the request epoch. Vectors use the selected cataloged parent as their center and are translated onto the parent's current shared-frame state on the GTK main thread, preventing fixed-epoch imports from being mixed with advanced parent states. Horizons object data prefills mean radius and mass when present; mass is derived from GM in SI units when GM is the available value. Small bodies may use structured JPL SBDB GM and diameter data as a fallback. Unavailable physical fields remain required in the review dialog. Only reviewed drafts are applied to the model on the GTK main thread.

Whole-system refresh is a separate atomic operation. It captures one UTC instant, fetches every canonical vector directly in the system's shared center, uses Horizons' reported TDB-UT offset for the stored TDB epoch, and fetches parent-centered elements only as metadata. Any request failure or cancellation discards the batch. Bundled presets remain immutable; refreshing one produces a saved editable copy.

## Main-Thread GTK Updates

Background workers may compute physics, but GTK updates must happen on the main thread through `GLib.idle_add`.
