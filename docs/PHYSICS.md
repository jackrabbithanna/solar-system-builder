# Physics Notes

The physics layer is intentionally independent of GTK. Keep it pure enough to test with regular Python unit tests.

## Units

Use SI units internally:

- mass: kg
- distance and radius: meters
- velocity: meters per second
- time: seconds

The UI may display convenient units such as AU, light-years, days, or years, but conversion should happen at the boundary.

## Simulation API

- `SimulationState`: NumPy arrays for masses, positions, velocities, and elapsed seconds.
- `acceleration(...)`: computes Newtonian or first post-Newtonian accelerations.
- `step(state, dt_s, mode)`: low-level single velocity-Verlet style integrator step.
- `advance(state, total_dt_s, mode, max_step_s=DAY)`: user-facing advance helper that splits large intervals into stable internal substeps.
- `advance_with_samples(state, total_dt_s, mode, max_step_s=DAY)`: same bounded advance behavior, plus copied position samples after each internal substep.

UI playback must use `advance_with_samples()` so trails can use the same internal substeps as physics. Non-UI callers that only need the final state can use `advance()`. Do not make direct large calls to `step()`.

## Relativity Model

`post_newtonian` is a practical first post-Newtonian pairwise correction suitable for an interactive solar-system app. It is not full numerical relativity.

## Large-Step Instability

A direct 30-day `step()` is too coarse for Mercury's roughly 88-day orbit and can eject Mercury from the Solar System. The UI's visible time-step control is a user-visible simulation interval, not the integrator step size.

The fix is:

- preserve the selected UI interval, such as 30 simulated days or 1 simulated year per playback update;
- internally split that interval into bounded physics steps through `advance()` or `advance_with_samples()`;
- record trail points from those internal samples instead of only the final UI-step position.

Regression coverage exists in `tests/test_physics.py`.

The UI derives the internal `max_step_s` from a scale policy in `src/scales.py` instead of always using the physics default. That policy estimates parent-child orbital periods or unparented root-body pair periods where possible and clamps the result by the selected accuracy profile. The visible time step can be days, years, decades, or centuries; it must still be passed through `advance()` or `advance_with_samples()` with a bounded internal step.

## Simulation Scope

Large systems can choose an active simulation scope before calling physics. `full_nbody` advances every body. `stellar_overview` advances only root stars and collapses child systems for display. `focused_subsystem` advances the selected root system and its children. `auto` selects a stellar overview for multi-star whole-system views and focused detail for follow-selected views.

This scope is a UI orchestration layer. `physics.py` still receives a normal flat `SimulationState` containing only the active bodies, and `window.py` merges completed active states back into the full UI state on the GTK main thread.

## Trail Sampling

Dense trail rendering uses `advance_with_samples()` to collect positions after each bounded internal step. At a visible interval of 30 days, the physics and trail renderer may see many internal samples instead of one sparse 30-day line segment. This keeps zoomed inner-planet trails from looking polygonal or rosette-like solely because of display sampling.

For large visible time steps, the UI decimates those physics samples before appending trails. Trail cadence is a display policy and should not change integration accuracy. In scoped modes, trails are appended only for active bodies.

## Display Scale Helpers

`src/scales.py` also owns display-only scale helpers such as elapsed-time formatting, distance formatting, and body-to-body distance measurement for UI readouts. These helpers may use AU or light-years for presentation, but physics and model state still use meters.

## Stability Expectations

For the bundled Solar System preset, repeated `advance(..., 30 * DAY)` calls should keep Mercury near its starting orbital radius instead of sending it multiple AU away.
