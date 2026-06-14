# GTK Playback And Threading

GTK widgets must only be touched on the GTK main thread.

## Why Playback Uses A Worker

Stable playback uses `physics.advance()`, which may run many 1-day internal substeps for a single user-visible interval. At `Days / step = 30`, this can take longer than a 33 ms GTK timer tick. Running it synchronously in `_tick()` starves GTK redraw and input handling.

## Threading Model

- `window.py` owns a single `ThreadPoolExecutor`.
- The worker receives a copied `SimulationState`.
- The worker runs physics only.
- The worker must not mutate `Body` objects.
- The worker must not call GTK APIs.
- Completion is handed back to the main thread with `GLib.idle_add(...)`.

## Applying Results

Completed states are applied by `_apply_simulation_state(...)` on the GTK main thread. That method updates:

- `self.state`
- body positions and velocities
- trails
- selected body editor fields
- time label
- drawing area invalidation

## Stale Result Guard

`simulation_generation` protects user edits, resets, and system loads from stale worker results. Increment it whenever the base simulation state is replaced because of user edits, loading a different system, resetting state, or saving a new baseline.

Worker results should only apply when their captured generation still matches the current generation.

## Lifecycle

On close:

- stop playback;
- remove the GTK timer;
- shut down the executor with `cancel_futures=True`;
- ignore late worker completions if the window is closed.

## Reset

The reset button restores the current system from the loaded-state snapshot. That snapshot is refreshed when a system is loaded, duplicated, or saved. Reset stops playback, increments `simulation_generation`, clears trails, rebuilds simulation state, and redraws on the GTK main thread.
