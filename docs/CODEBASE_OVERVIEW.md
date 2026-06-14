# Codebase Overview

Solar System Builder is a Python GNOME 49 / GTK4 / Libadwaita app built with Meson and run in GNOME Builder through Flatpak.

## Main Modules

- `src/main.py`: application object, app actions, About dialog, shortcuts dialog.
- `src/window.py`: main GTK window controller, drawing, playback controls, body inspector, local-library actions.
- `src/models.py`: schema-versioned `Body`, `SystemGroup`, and `SolarSystem` dataclasses with validation, parent-body relationships, group hierarchy, migration, and JSON conversion.
- `src/physics.py`: NumPy-backed simulation state, acceleration, low-level `step()`, user-facing `advance()`, and sampled `advance_with_samples()`.
- `src/scales.py`: pure scale helpers for time/distance units, elapsed-time formatting, adaptive internal step policy, simulation scope selection, and trail sampling cadence.
- `src/presets.py`: loads bundled preset data from `src/presets/`.
- `src/storage.py`: local JSON library using GLib app data paths.
- `src/constants.py`: SI constants used by physics and UI conversion.

## Data Flow

1. `load_builtin_solar_system()` or `load_builtin_solar_systems()` loads JSON preset data into `SolarSystem` objects.
2. `SimulationState.from_bodies()` copies masses, positions, and velocities into NumPy arrays.
3. Optional `SystemGroup` records organize flat bodies into semantic systems and subsystems for navigation.
4. The UI chooses an active simulation scope, such as full N-body, system overview, stellar overview, focused subsystem, or hybrid focused context.
5. Playback advances the active `SimulationState` through `physics.advance_with_samples()` so internal substep positions can be reused for dense trails.
6. Completed active body states are merged back into the full UI state on the GTK main thread; temporary overview/context states update elapsed time and group trails without mutating bodies.
7. `window.py` draws active body positions/trails, system overview group positions/trails, or focused bodies with muted context markers on a `GtkDrawingArea`.
8. Save/duplicate writes `SolarSystem` JSON through `storage.Library`.

`Body.parent_id` records local orbital/display parentage, such as planets orbiting a star. Body descendant chains can be used as focus targets, which also supports future planet-and-moon systems. `SystemGroup` records larger semantic hierarchy, such as a binary subsystem or a planetary system. Physics remains a flat N-body simulation over the active body set, so groups do not constrain gravity by themselves.

`SolarSystem.settings` stores per-system playback and view preferences. The UI exposes the visible time step, time unit, accuracy profile, view mode, simulation scope, and distance editor unit, while the physics layer still receives SI values only.

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
