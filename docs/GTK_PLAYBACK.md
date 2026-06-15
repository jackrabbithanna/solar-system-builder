# GTK Playback And Threading

GTK widgets must only be touched on the GTK main thread.

## Why Playback Uses A Worker

Stable playback uses `physics.advance_with_samples()`, which may run many 1-day internal substeps for a single user-visible interval. At `Days / step = 30`, this can take longer than a 33 ms GTK timer tick. Running it synchronously in `_tick()` starves GTK redraw and input handling.

## Threading Model

- `window.py` owns a single `ThreadPoolExecutor`.
- `playback.SimulationSession` owns simulation state, trails, overview/context caches, and the generation counter.
- `playback.SimulationSession` creates `SimulationJob` records containing copied active-scope, overview, and context `SimulationState` values for worker use.
- The worker receives a `SimulationJob`.
- The worker runs physics only through `playback.run_simulation_job(...)` and returns a `SimulationJobResult` with final state plus copied position samples.
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

After a result applies, `window.py` refreshes GTK widgets: relationship labels, selected editor fields, the time label, and the canvas scene. Trails are appended on the GTK main thread from the sampled positions returned by `advance_with_samples()`. The trail selection, capping, and storage logic lives in `playback.py`.

The UI may pass only an active body subset to the worker. Stellar overview mode advances root stars only, while focused subsystem mode advances a selected root body and its children. System overview mode passes temporary group barycenter entities to the worker and applies only elapsed time plus group trails. Hybrid focused context mode advances focused bodies and outside context barycenters in the same worker job; only focused body state is merged back into the model, while context barycenters remain display-only. Inactive bodies remain in the full UI state but are not updated by that worker result.

## Stale Result Guard

`SimulationSession.generation` protects user edits, resets, and system loads from stale worker results. Increment it whenever the base simulation state is replaced because of user edits, loading a different system, resetting state, or saving a new baseline.

Worker results should only apply when their captured generation still matches the current generation.

## Lifecycle

On close:

- stop playback;
- remove the GTK timer;
- shut down the executor with `cancel_futures=True`;
- ignore late worker completions if the window is closed.

## Reset

The reset button restores the current system from the loaded-state snapshot. That snapshot is refreshed when a system is loaded, duplicated, or saved. Reset stops playback, increments the session generation, clears trails, rebuilds simulation state, and redraws on the GTK main thread.
