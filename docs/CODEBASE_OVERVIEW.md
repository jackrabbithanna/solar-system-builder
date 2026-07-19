# Codebase Overview

Solar System Builder is a Python GNOME 49 / GTK4 / Libadwaita app built with Meson and run in GNOME Builder through Flatpak.

## Main Modules

- `src/main.py`: application object, app actions, About dialog, shortcuts dialog.
- `src/window.py`: main GTK window coordinator, playback controls, body inspector, worker scheduling, and main-thread UI refreshes.
- `src/canvas.py`: `SolarSystemCanvas` GTK drawing widget, toggleable 2D/scientific-3D Cairo rendering, fixed and automatic cameras, configured orbit guides, styled trails, gestures/tooltips, and body/group selection signals.
- `src/sidebar.py`: sidebar hierarchy widget and panel controllers for system settings and body/orbit inspector state.
- `src/models.py`: schema-v13 `Body`, `FlybyData`, `SystemGroup`, `SystemReferenceFrame`, and `SolarSystem` dataclasses with validation, hierarchy, migration, canonical-state provenance, physics/integrator settings, canvas/path settings, and JSON conversion.
- `src/documents.py`: pure validated JSON document parsing, canonical serialization, and collision-safe imported names.
- `src/reference_frames.py`: pure rigid origin/axis transforms, barycenter origins, matrix validation, and orbital/flyby orientation rotation.
- `src/orbit_editing.py`: GTK-free body, group-barycenter, and binary-pair orbit mutations with playback-state rebuilding.
- `src/system_editing.py`: GTK-free system starters and atomic create/update/delete helpers for complete 3D body state and group membership.
- `src/horizons.py`: GTK-free, serialized JPL Horizons lookup/vector/element and SBDB physical-data client, response parsers, frame checks, atomic import, and whole-system refresh batches.
- `src/orbits.py`: pure Keplerian orbit conversion helpers for generating Cartesian simulation state and configured reference-conic guides from optional orbital metadata.
- `src/flybys.py`: pure encounter-oriented hyperbolic trajectory generation and flyby radial-state helpers.
- `src/physics.py`: NumPy-backed simulation state, Newtonian/1PN acceleration, Velocity Verlet/RK4 integration, bounded advance helpers, and conservation diagnostics.
- `src/scales.py`: pure scale helpers for time/distance units, elapsed-time formatting, adaptive internal step policy, approximation selection, focus containment, and trail reference frames.
- `src/hierarchy.py`: GTK-free body/group hierarchy helpers for sidebar ordering, depth, group membership, relationship labels, and group centers.
- `src/viewport.py`: GTK-free 2D/3D canvas projection, orbit-camera, scale, view-center, barycenter, trail anchoring, zoom clamp, and depth-aware hit-test helpers.
- `src/playback.py`: GTK-free `SimulationSession`, worker job/result contracts, active/overview/context state orchestration, generation checks, and inertial/relative trail appending and capping.
- `src/presets.py`: loads bundled preset data from `src/presets/`.
- `src/storage.py`: local JSON library using GLib app data paths.
- `src/system_library.py`: GTK controller for bundled/saved system selection, naming, persistence, duplication, and deletion.
- `src/constants.py`: SI constants used by physics and UI conversion.

## Data Flow

1. `load_builtin_solar_system()` or `load_builtin_solar_systems()` loads JSON preset data into `SolarSystem` objects.
2. `SimulationState.from_bodies()` copies masses, positions, and velocities into NumPy arrays.
3. Optional `SystemGroup` records organize flat bodies into semantic systems and subsystems; `hierarchy.py` computes sidebar order, group depth, and descendant body membership, while `sidebar.py` renders the hierarchy list.
4. `SimulationSession` chooses a physics policy independently from focused display indices. Auto predicts full-N-body cost against a measured 200 ms worker budget and otherwise selects an approximation.
5. `playback.SimulationSession` builds copied full/active/overview/context `SimulationState` data and immutable `SimulationJob` records carrying the selected gravity model and integrator. At star-scale detail, Auto and scoped body policies replace each planet and its moons with a mass-weighted proxy; completed proxy deltas translate every member so local moon state remains intact. Hybrid focus combines focused bodies and coarse context into one temporary coupled state, then splits completed results so context gravity affects the focus without merging context proxies into the body model. Playback advances worker copies through `physics.advance_with_samples()` so internal substep positions can be reused for body and aggregate trails. Focused-parent trails translate each sample against the focused body or group barycenter before storage.
6. Completed `SimulationJobResult` values are applied by `SimulationSession` on the GTK main thread; active body states are merged back into the full UI state, while temporary overview/context states update elapsed time and group trails without mutating bodies.
7. `window.py` builds a `CanvasScene` snapshot from main-thread state and passes it to `SolarSystemCanvas`.
8. `canvas.py` draws configured reference-orbit guides, recorded trails, body positions, system overview groups, or focused bodies with context markers. The session-only canvas toggle selects the top-down 2D path or an orthographic XYZ view with an orbit camera, reference plane, and depth cues; the hybrid navigation inset remains 2D. Fixed Scale freezes a session-only center and physical scale independently for each renderer. Coordinate math, path styles, relative-trail anchoring, and hit tests stay in `viewport.py`, while playback retains transient trail points as XYZ data so either view can be selected without losing history.
9. Sidebar panel controllers emit edit/generate/settings intents; `window.py` validates complete body/group edits through GTK-free editing helpers, then rebuilds playback state on the main thread.
10. Creation supports preset duplication, a JPL-compatible Sun-only system, custom single/binary/hierarchical star state, and persistent unbound flybys. Manual bodies accept complete Cartesian state or orbital elements; the flyby builder converts periapsis, velocity at infinity, starting distance, and 3D angles into canonical Cartesian state.
11. Horizons lookup, ephemeris, and optional SBDB physical-data requests run on a dedicated single-worker executor. Playback is stopped before a fetch, the request epoch includes current simulation elapsed time, and target vectors are requested relative to the selected cataloged parent. Import drafts prefill mass from GM and radius from mean-radius or diameter data when JPL supplies them. Whole-system refresh captures one current UTC instant, requests every vector in the shared system frame, derives the matching TDB epoch from Horizons, and returns an immutable all-or-nothing batch. Completed results return through `GLib.idle_add` and only mutate the model on the GTK thread.
12. `SystemLibraryController` coordinates explicit preset duplication, saved-system persistence, guarded selection, and deletion. Portable imports are duplicated with regenerated linked IDs before entering the library; exports materialize a cloned current simulation state without mutating the library.
13. Reference-frame edits stop playback, build and validate a transformed candidate through `reference_frames.py`, and only replace the main-thread model after every position, velocity, body/group orbit, and flyby orientation has been transformed successfully. Epoch and time scale are not changed by this workflow.

`Body.parent_id` records local orbital/display parentage. Stars are roots; bound planets, dwarf planets, comets, and asteroids orbit stars; moons orbit planets or dwarf planets. Non-moon bodies may also be unparented Cartesian or flyby bodies. `Body.state_origin` records whether the current canonical vector came from Cartesian entry, orbital generation, Horizons, or the flyby builder. Optional `Body.orbit`, `Body.flyby`, and `Body.data_source` preserve provenance, while `position_m` and `velocity_mps` remain canonical. `SolarSystem.reference_frame` describes the shared epoch, time scale, center, plane, and reference system. `SystemGroup` records larger semantic hierarchy, but physics remains a flat N-body simulation over the active body set.

Schema v13 adds persisted gravity-model and integrator settings. It retains v12 canvas/path settings, v11 flyby inputs, v10 trail-frame migration, and v9 body-provenance/reference-frame migration. Older documents receive post-Newtonian Velocity Verlet defaults during load. A body can have only one direct group owner.

`SolarSystem.settings` stores per-system playback and view preferences. The UI exposes the visible time step, time unit, accuracy profile, gravity model, integrator, view mode, physics policy (serialized under the compatibility key `simulation_scope`), trail perspective, path visibility/style, and distance editor unit, while the physics layer still receives SI values only. The 2D/3D choice and exact camera transforms are window-session state: they are not serialized or treated as dirty system edits.

## Reset Flow

`window.py` keeps a loaded-state snapshot of the current `SolarSystem`. Reset to Loaded State restores that deep copy. Reset to Last Save reloads an editable system through `Library`, while Reset Bundled Preset reloads the active built-in by ID from packaged data. Every variant stops playback, rebuilds `SimulationState`, clears trails, and invalidates pending worker results through the session generation counter.

## Tests

Tests live in `tests/` and are registered through `tests/meson.build`.

- `test_models.py`: model validation and preset round trips.
- `test_orbits.py`: orbital metadata conversion into Cartesian state vectors and configured body/group/binary guide sampling.
- `test_physics.py`: integrator behavior, conservation diagnostics, orbital stability, 1PN differences, close approaches, high masses, reverse time, and large-step regressions.
- `test_hierarchy.py`: sidebar hierarchy ordering, depth, labels, and descendant group membership.
- `test_viewport.py`: projection, zoom clamping, fixed-view/path styles, view-center, scale, and hit-test math.
- `test_playback.py`: trail sampling/capping, generation checks, worker helpers, session job planning, copied worker state, and active/overview/hybrid result application.
- `test_storage.py`: local library save/load/list/delete.
- `test_documents.py`: portable parsing/serialization, migrations, imported naming, and linked-ID regeneration.
- `test_reference_frames.py`: origin translation, Euler/matrix rotation, barycenters, provenance rotation, and atomic failures.
- `test_system_editing.py`: starter workflows, full 3D state input/update, reparenting, atomic validation, cascades, and seeded circular orbits.
- `test_horizons.py`: lookup and ephemeris parsing, URL/frame contracts, API compatibility, duplicates, atomic import, and current-epoch system refresh.
- `test_system_library.py`: system-library controller new, save, duplicate, rename, selection, and delete behavior.
- `test_update_solar_system_preset.py`: preset update script behavior.

Run:

```sh
python3 -m unittest discover -s tests
meson test -C builddir
```
