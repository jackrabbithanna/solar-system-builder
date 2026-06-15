# Codebase Overview

Solar System Builder is a Python GNOME 49 / GTK4 / Libadwaita app built with Meson and run in GNOME Builder through Flatpak.

## Main Modules

- `src/main.py`: application object, app actions, About dialog, shortcuts dialog.
- `src/window.py`: main GTK window coordinator, playback controls, body inspector, local-library actions, and main-thread state application.
- `src/canvas.py`: `SolarSystemCanvas` GTK drawing widget, canvas gestures/tooltips, zoom state, and body/group selection signals.
- `src/sidebar.py`: sidebar hierarchy widget and panel controllers for system settings and body/orbit inspector state.
- `src/models.py`: schema-versioned `Body`, `SystemGroup`, and `SolarSystem` dataclasses with validation, parent-body relationships, group hierarchy, migration, and JSON conversion.
- `src/orbits.py`: pure Keplerian orbit conversion helpers for generating Cartesian simulation state from optional orbital metadata.
- `src/physics.py`: NumPy-backed simulation state, acceleration, low-level `step()`, user-facing `advance()`, and sampled `advance_with_samples()`.
- `src/scales.py`: pure scale helpers for time/distance units, elapsed-time formatting, adaptive internal step policy, simulation scope selection, and trail sampling cadence.
- `src/hierarchy.py`: GTK-free body/group hierarchy helpers for sidebar ordering, depth, group membership, relationship labels, and group centers.
- `src/viewport.py`: GTK-free canvas projection, scale, view-center, barycenter, zoom clamp, and hit-test helpers.
- `src/playback.py`: GTK-free playback helpers for active-state copies/merges, overview simulation states, hybrid worker advancement, generation checks, and trail appending/capping.
- `src/presets.py`: loads bundled preset data from `src/presets/`.
- `src/storage.py`: local JSON library using GLib app data paths.
- `src/constants.py`: SI constants used by physics and UI conversion.

## Data Flow

1. `load_builtin_solar_system()` or `load_builtin_solar_systems()` loads JSON preset data into `SolarSystem` objects.
2. `SimulationState.from_bodies()` copies masses, positions, and velocities into NumPy arrays.
3. Optional `SystemGroup` records organize flat bodies into semantic systems and subsystems; `hierarchy.py` computes sidebar order, group depth, and descendant body membership, while `sidebar.py` renders the hierarchy list.
4. The UI chooses an active simulation scope, such as full N-body, system overview, stellar overview, focused subsystem, or hybrid focused context.
5. `playback.py` builds copied active/overview `SimulationState` data for workers. Playback advances those copies through `physics.advance_with_samples()` so internal substep positions can be reused for dense trails.
6. Completed active body states are merged back into the full UI state on the GTK main thread; temporary overview/context states update elapsed time and group trails without mutating bodies.
7. `window.py` builds a `CanvasScene` snapshot from main-thread state and passes it to `SolarSystemCanvas`.
8. `canvas.py` draws body positions/trails, system overview group positions/trails, or focused bodies with muted context markers, while delegating coordinate math and hit tests to `viewport.py`.
9. Sidebar panel controllers emit edit/generate/settings intents; `window.py` applies model changes and refreshes simulation/canvas state on the GTK main thread.
10. Save/duplicate writes `SolarSystem` JSON through `storage.Library`.

`Body.parent_id` records local orbital/display parentage, such as planets orbiting a star. Body descendant chains can be used as focus targets, which also supports future planet-and-moon systems. Optional `Body.orbit` and `Body.data_source` records preserve user-entered orbital/source metadata for generating approximate initial state vectors, but `position_m` and `velocity_mps` remain the canonical simulation state. `SystemGroup` records larger semantic hierarchy, such as a binary subsystem or a planetary system. Physics remains a flat N-body simulation over the active body set, so groups do not constrain gravity by themselves.

`SolarSystem.settings` stores per-system playback and view preferences. The UI exposes the visible time step, time unit, accuracy profile, view mode, simulation scope, and distance editor unit, while the physics layer still receives SI values only.

## Reset Flow

`window.py` keeps a loaded-state snapshot of the current `SolarSystem`. Reset restores a deep copy of that snapshot, rebuilds `SimulationState`, clears trails, and invalidates pending playback worker results through `simulation_generation`.

## Tests

Tests live in `tests/` and are registered through `tests/meson.build`.

- `test_models.py`: model validation and preset round trips.
- `test_orbits.py`: orbital metadata conversion into Cartesian state vectors.
- `test_physics.py`: orbital stability, 1PN differences, invalid arrays, large-step regression coverage.
- `test_hierarchy.py`: sidebar hierarchy ordering, depth, labels, and descendant group membership.
- `test_viewport.py`: projection, zoom clamping, view-center, scale, and hit-test math.
- `test_playback.py`: trail sampling/capping, generation checks, worker helper, and active-state copy/merge behavior.
- `test_storage.py`: local library save/load/list/delete.
- `test_update_solar_system_preset.py`: preset update script behavior.

Run:

```sh
python3 -m unittest discover -s tests
meson test -C builddir
```
