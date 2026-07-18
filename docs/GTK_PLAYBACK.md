# GTK Playback And Threading

GTK widgets must only be touched on the GTK main thread.

## Why Playback Uses A Worker

Stable playback uses `physics.advance_with_samples()`, which may run many 1-day internal substeps for a single user-visible interval. At `Days / step = 30`, this can take longer than a 33 ms GTK timer tick. Running it synchronously in `_tick()` starves GTK redraw and input handling.

## Threading Model

- `window.py` owns a single `ThreadPoolExecutor`.
- `playback.SimulationSession` owns simulation state, trails, overview/context caches, Auto Physics calibration/lock state, and the generation counter.
- `playback.SimulationSession` creates `SimulationJob` records containing the physics/display plan and copied full, overview, or context `SimulationState` values for worker use.
- The worker receives a `SimulationJob`.
- The worker runs physics only through `playback.run_simulation_job(...)` and returns a `SimulationJobResult` with final state, copied position samples, and measured worker duration.
- The worker must not mutate `Body` objects.
- The worker must not call GTK APIs.
- Completion is handed back to the main thread with `GLib.idle_add(...)`.

## Applying Results

Completed results are applied by `SimulationSession.apply_result(...)` on the GTK main thread. That method updates:

- `SimulationSession.state`
- body positions and velocities
- trails from sampled physics positions for active bodies
- overview/context state caches and trails
- the session elapsed time

After a result applies, `window.py` refreshes GTK widgets: relationship labels, selected editor fields, the time label, and the canvas scene. Trails are appended on the GTK main thread from the sampled positions returned by `advance_with_samples()`. The trail selection, capping, and inertial-to-focused-frame translation logic lives in `playback.py`.

JPL Horizons import and whole-system refresh share a separate single-worker executor. Refresh progress and completion are marshalled with `GLib.idle_add`; the worker only constructs immutable data. Cancellation and generation checks prevent stale batches from touching GTK or the active model, and a validated replacement system is installed on the GTK main thread only after every request succeeds.

Full N-body jobs pass every body even when focus displays only a subset. Root-star and focused policies may pass an active body subset. System-barycenter mode passes temporary group entities and applies only elapsed time plus group trails. Focus + Coarse Context advances focused bodies and outside aggregate context as one coupled temporary state. The worker splits the result so only focused body state is merged back, while context state remains session-owned. Full-job samples are aggregated on the main thread for focused inset trails.

At star-scale detail, Auto and scoped body policies send planet-and-moon memberships as mass-weighted proxy rows. On the main thread, each completed proxy position and velocity delta is applied to the planet and its moons. This freezes their relative phase while preserving a valid subsystem for later planetary Focus and Fit. Explicit Full N-body still sends every moon as its own physics row, but coarse views do not draw or sample moon trails.

## Stale Result Guard

`SimulationSession.generation` protects user edits, resets, system loads, and trail-frame switches from stale worker results. Increment it whenever the base simulation state is replaced because of user edits, loading a different system, resetting state, saving a new baseline, or selecting a different trail coordinate frame.

Worker results should only apply when their captured generation still matches the current generation.

## Lifecycle

On close:

- stop playback;
- remove the GTK timer;
- shut down the executor with `cancel_futures=True`;
- ignore late worker completions if the window is closed.

## Reset

The reset button restores the current system from the loaded-state snapshot. That snapshot is refreshed when a system is loaded, duplicated, or saved. Reset stops playback, increments the session generation, clears trails, rebuilds simulation state at zero elapsed time, and redraws on the GTK main thread. Structural edits rebuild the state while preserving the current elapsed time.
