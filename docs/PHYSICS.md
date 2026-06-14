# Physics Notes

The physics layer is intentionally independent of GTK. Keep it pure enough to test with regular Python unit tests.

## Units

Use SI units internally:

- mass: kg
- distance and radius: meters
- velocity: meters per second
- time: seconds

The UI may display convenient units such as AU or days, but conversion should happen at the boundary.

## Simulation API

- `SimulationState`: NumPy arrays for masses, positions, velocities, and elapsed seconds.
- `acceleration(...)`: computes Newtonian or first post-Newtonian accelerations.
- `step(state, dt_s, mode)`: low-level single velocity-Verlet style integrator step.
- `advance(state, total_dt_s, mode, max_step_s=DAY)`: user-facing advance helper that splits large intervals into stable internal substeps.

UI playback must use `advance()`, not direct large calls to `step()`.

## Relativity Model

`post_newtonian` is a practical first post-Newtonian pairwise correction suitable for an interactive solar-system app. It is not full numerical relativity.

## Large-Step Instability

A direct 30-day `step()` is too coarse for Mercury's roughly 88-day orbit and can eject Mercury from the Solar System. The UI's `Days / step` setting is a user-visible simulation interval, not the integrator step size.

The fix is:

- preserve the selected UI interval, such as 30 simulated days per playback update;
- internally split that interval into at most 1-day physics steps through `advance()`.

Regression coverage exists in `tests/test_physics.py`.

## Stability Expectations

For the bundled Solar System preset, repeated `advance(..., 30 * DAY)` calls should keep Mercury near its starting orbital radius instead of sending it multiple AU away.
