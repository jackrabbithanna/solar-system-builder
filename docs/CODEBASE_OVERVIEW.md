# Codebase Overview

Solar System Builder is a Python GNOME 49 / GTK4 / Libadwaita app built with Meson and run in GNOME Builder through Flatpak.

## Main Modules

- `src/main.py`: application object, app actions, About dialog, shortcuts dialog.
- `src/window.py`: main GTK window controller, drawing, playback controls, body inspector, local-library actions.
- `src/models.py`: schema-versioned `Body` and `SolarSystem` dataclasses with validation, parent-body relationships, migration, and JSON conversion.
- `src/physics.py`: NumPy-backed simulation state, acceleration, low-level `step()`, user-facing `advance()`, and sampled `advance_with_samples()`.
- `src/scales.py`: pure scale helpers for time/distance units, elapsed-time formatting, adaptive internal step policy, and trail sampling cadence.
- `src/presets.py`: loads bundled preset data from `src/presets/`.
- `src/storage.py`: local JSON library using GLib app data paths.
- `src/constants.py`: SI constants used by physics and UI conversion.

## Data Flow

1. `load_builtin_solar_system()` or `load_builtin_solar_systems()` loads JSON preset data into `SolarSystem` objects.
2. `SimulationState.from_bodies()` copies masses, positions, and velocities into NumPy arrays.
3. Playback advances `SimulationState` through `physics.advance_with_samples()` so internal substep positions can be reused for dense trails.
4. Completed states and sampled trail points are applied on the GTK main thread.
5. `window.py` draws body positions and trails on a `GtkDrawingArea`.
6. Save/duplicate writes `SolarSystem` JSON through `storage.Library`.

`Body.parent_id` records display and authoring hierarchy, such as planets orbiting a star. Physics remains a flat N-body simulation over `SolarSystem.bodies`, so multiple stars naturally exert gravity on each other and their planets.

`SolarSystem.settings` stores per-system playback and view preferences. The UI exposes the visible time step, time unit, accuracy profile, view mode, and distance editor unit, while the physics layer still receives SI values only.

## Reset Flow

`window.py` keeps a loaded-state snapshot of the current `SolarSystem`. Reset restores a deep copy of that snapshot, rebuilds `SimulationState`, clears trails, and invalidates pending playback worker results through `simulation_generation`.

## Tests

Tests live in `tests/` and are registered through `tests/meson.build`.

- `test_models.py`: model validation and preset round trips.
- `test_physics.py`: orbital stability, 1PN differences, invalid arrays, large-step regression coverage.
- `test_storage.py`: local library save/load/list/delete.
- `test_update_solar_system_preset.py`: preset update script behavior.

Run:

```sh
python3 -m unittest discover -s tests
meson test -C builddir
```
