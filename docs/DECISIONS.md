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

## Trail Sampling

UI trails are sampled from bounded physics substeps, not just from the final position of each visible UI step. This keeps high `Days / step` playback from drawing sparse line segments that make inner orbits look artificially angular or rosette-like.

## Local JSON Library

User-created systems are stored as JSON in the app data directory. Document import/export is a later feature.

## Bundled Presets

Bundled systems are versioned JSON data. They are read-only templates. Editing requires an explicit duplicate so Save never silently changes system identity.

## Canonical State and Reference Frames

Cartesian body vectors are canonical. Orbital elements and sources are provenance. Schema v9 records a body `state_origin` and one system-level reference frame. The UI displays but does not relabel an existing frame because a correct change requires transforming every body vector.

## Horizons Requests

Live JPL Horizons import is limited to Sol systems with a compatible frame. Requests use one executor and a serialized client, validate the API signature and major version, and return immutable drafts. Playback stops before the request; its elapsed time offsets the request epoch. Vectors use the selected cataloged parent as their center and are translated onto the parent's current shared-frame state on the GTK main thread, preventing fixed-epoch imports from being mixed with advanced parent states. Horizons object data prefills mean radius and mass when present; mass is derived from GM in SI units when GM is the available value. Small bodies may use structured JPL SBDB GM and diameter data as a fallback. Unavailable physical fields remain required in the review dialog. Only reviewed drafts are applied to the model on the GTK main thread.

## Main-Thread GTK Updates

Background workers may compute physics, but GTK updates must happen on the main thread through `GLib.idle_add`.
