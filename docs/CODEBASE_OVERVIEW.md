# Codebase Overview

Solar System Builder is a Python GNOME 49 / GTK4 / Libadwaita app built with Meson and run in GNOME Builder through Flatpak.

## Main Modules

- `src/main.py`: application object, app actions, About dialog, shortcuts dialog.
- `src/window.py`: main GTK window coordinator, playback controls, body inspector, worker scheduling, and main-thread UI refreshes.
- `src/canvas.py`: `SolarSystemCanvas` GTK drawing widget, canvas gestures/tooltips, zoom state, and body/group selection signals.
- `src/sidebar.py`: sidebar hierarchy widget and panel controllers for system settings and body/orbit inspector state.
- `src/models.py`: schema-v9 `Body`, `SystemGroup`, `SystemReferenceFrame`, and `SolarSystem` dataclasses with validation, hierarchy, migration, canonical-state provenance, and JSON conversion.
- `src/orbit_editing.py`: GTK-free body, group-barycenter, and binary-pair orbit mutations with playback-state rebuilding.
- `src/system_editing.py`: GTK-free system starters and atomic create/update/delete helpers for complete 3D body state and group membership.
- `src/horizons.py`: GTK-free, serialized JPL Horizons lookup/vector/element and SBDB physical-data client, response parsers, frame checks, and atomic import application.
- `src/orbits.py`: pure Keplerian orbit conversion helpers for generating Cartesian simulation state from optional orbital metadata.
- `src/physics.py`: NumPy-backed simulation state, acceleration, low-level `step()`, user-facing `advance()`, and sampled `advance_with_samples()`.
- `src/scales.py`: pure scale helpers for time/distance units, elapsed-time formatting, adaptive internal step policy, approximation selection, and trail sampling cadence.
- `src/hierarchy.py`: GTK-free body/group hierarchy helpers for sidebar ordering, depth, group membership, relationship labels, and group centers.
- `src/viewport.py`: GTK-free canvas projection, scale, view-center, barycenter, zoom clamp, and hit-test helpers.
- `src/playback.py`: GTK-free `SimulationSession`, worker job/result contracts, active/overview/context state orchestration, generation checks, and trail appending/capping.
- `src/presets.py`: loads bundled preset data from `src/presets/`.
- `src/storage.py`: local JSON library using GLib app data paths.
- `src/system_library.py`: GTK controller for bundled/saved system selection, naming, persistence, duplication, and deletion.
- `src/constants.py`: SI constants used by physics and UI conversion.

## Data Flow

1. `load_builtin_solar_system()` or `load_builtin_solar_systems()` loads JSON preset data into `SolarSystem` objects.
2. `SimulationState.from_bodies()` copies masses, positions, and velocities into NumPy arrays.
3. Optional `SystemGroup` records organize flat bodies into semantic systems and subsystems; `hierarchy.py` computes sidebar order, group depth, and descendant body membership, while `sidebar.py` renders the hierarchy list.
4. `SimulationSession` chooses a physics policy independently from focused display indices. Auto predicts full-N-body cost against a measured 200 ms worker budget and otherwise selects an approximation.
5. `playback.SimulationSession` builds copied full/active/overview/context `SimulationState` data and `SimulationJob` records for workers. At star-scale detail, Auto and scoped body policies replace each planet and its moons with a mass-weighted proxy; completed proxy deltas translate every member so local moon state remains intact. Hybrid focus combines focused bodies and coarse context into one temporary coupled state, then splits completed results so context gravity affects the focus without merging context proxies into the body model. Playback advances worker copies through `physics.advance_with_samples()` so internal substep positions can be reused for body and aggregate trails.
6. Completed `SimulationJobResult` values are applied by `SimulationSession` on the GTK main thread; active body states are merged back into the full UI state, while temporary overview/context states update elapsed time and group trails without mutating bodies.
7. `window.py` builds a `CanvasScene` snapshot from main-thread state and passes it to `SolarSystemCanvas`.
8. `canvas.py` draws body positions/trails, system overview group positions/trails, or focused bodies with muted context markers, while delegating coordinate math and hit tests to `viewport.py`.
9. Sidebar panel controllers emit edit/generate/settings intents; `window.py` validates complete body/group edits through GTK-free editing helpers, then rebuilds playback state on the main thread.
10. Creation supports preset duplication, a JPL-compatible Sun-only system, and custom single/binary/hierarchical star state. Manual bodies accept complete Cartesian state or orbital elements.
11. Horizons lookup, ephemeris, and optional SBDB physical-data requests run on a dedicated single-worker executor. Playback is stopped before a fetch, the request epoch includes current simulation elapsed time, and target vectors are requested relative to the selected cataloged parent. Import drafts prefill mass from GM and radius from mean-radius or diameter data when JPL supplies them. Completed drafts return through `GLib.idle_add`; reviewed parent-relative vectors are translated onto the parent's current state and only then mutate the model on the GTK thread.
12. `SystemLibraryController` coordinates explicit preset duplication, saved-system persistence, guarded selection, and deletion. `window.py` owns dirty-state prompts and loaded snapshots.

`Body.parent_id` records local orbital/display parentage. Stars are roots; planets, dwarf planets, comets, and asteroids orbit stars; moons orbit planets or dwarf planets. `Body.state_origin` records whether the current canonical vector came from Cartesian entry, orbital generation, or Horizons. Optional `Body.orbit` and `Body.data_source` preserve provenance, while `position_m` and `velocity_mps` remain canonical. `SolarSystem.reference_frame` describes the shared epoch, time scale, center, plane, and reference system. `SystemGroup` records larger semantic hierarchy, but physics remains a flat N-body simulation over the active body set.

Schema v9 migrates older body provenance and reference-frame metadata. The bundled Solar System and Dwarf Planets presets migrate to their known JPL solar-system-barycentric frame; other legacy systems migrate to an app-local frame. A body can have only one direct group owner.

`SolarSystem.settings` stores per-system playback and view preferences. The UI exposes the visible time step, time unit, accuracy profile, view mode, physics policy (serialized under the compatibility key `simulation_scope`), and distance editor unit, while the physics layer still receives SI values only.

## Reset Flow

`window.py` keeps a loaded-state snapshot of the current `SolarSystem`. Reset restores a deep copy of that snapshot, asks `SimulationSession` to rebuild `SimulationState`, clears trails, and invalidates pending playback worker results through the session generation counter.

## Tests

Tests live in `tests/` and are registered through `tests/meson.build`.

- `test_models.py`: model validation and preset round trips.
- `test_orbits.py`: orbital metadata conversion into Cartesian state vectors.
- `test_physics.py`: orbital stability, 1PN differences, invalid arrays, large-step regression coverage.
- `test_hierarchy.py`: sidebar hierarchy ordering, depth, labels, and descendant group membership.
- `test_viewport.py`: projection, zoom clamping, view-center, scale, and hit-test math.
- `test_playback.py`: trail sampling/capping, generation checks, worker helpers, session job planning, copied worker state, and active/overview/hybrid result application.
- `test_storage.py`: local library save/load/list/delete.
- `test_system_editing.py`: starter workflows, full 3D state input/update, reparenting, atomic validation, cascades, and seeded circular orbits.
- `test_horizons.py`: lookup and ephemeris parsing, URL/frame contracts, API compatibility, duplicates, and atomic import.
- `test_system_library.py`: system-library controller new, save, duplicate, rename, selection, and delete behavior.
- `test_update_solar_system_preset.py`: preset update script behavior.

Run:

```sh
python3 -m unittest discover -s tests
meson test -C builddir
```
