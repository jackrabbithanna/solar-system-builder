# Architecture Decisions

This file records decisions that future sessions should not rediscover from scratch.

## 2D First

The first simulator view is a 2D GTK drawing surface. 3D rendering and camera controls are deferred.

## NumPy Physics

The physics core uses NumPy arrays for vector math and performance. NumPy is packaged into the Flatpak manifest because Builder's sandbox cannot use host Python packages.

## First Post-Newtonian Relativity

The app uses a practical first post-Newtonian approximation for solar-system scale simulation. Full numerical relativity is out of scope for the interactive first implementation.

## User Step Versus Integrator Step

The UI `Days / step` value is the visible simulation interval. It must not be used directly as one large integrator step. Use `physics.advance()` for substepping.

## Local JSON Library

User-created systems are stored as JSON in the app data directory. Document import/export is a later feature.

## Bundled Preset

The initial Solar System is bundled as versioned JSON data. It is treated as a read-only template; user edits should create or save a local copy.

## Main-Thread GTK Updates

Background workers may compute physics, but GTK updates must happen on the main thread through `GLib.idle_add`.
