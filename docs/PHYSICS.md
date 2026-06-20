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

The UI derives the internal `max_step_s` from a scale policy in `src/scales.py` instead of always using the physics default. That policy estimates parent-child orbital periods or unparented root-body pair periods where possible, applies the selected accuracy fraction, and caps the result by profile without imposing a day-scale lower bound. The visible time step can be days, years, decades, centuries, millennia, or Myr; it must still be passed through `advance()` or `advance_with_samples()` with a bounded internal step.

## Physics Policy

Large systems can choose a physics policy before calling physics. `full_nbody` advances every body. `stellar_overview` advances root stars only. `system_overview` advances temporary group barycenters, such as Alpha Centauri AB and the Proxima system. `focused_subsystem` advances a selected root system and its descendants. `hybrid_focused_context` advances focused bodies and outside aggregate context separately.

`auto` estimates full-N-body work as `ceil(abs(visible_step) / max_internal_step) * body_count^2`. It begins at `0.02 ms` per work unit with a 200 ms budget, then updates that rate with a 25% EWMA from full jobs of at least 100 units. When full physics is too expensive, Auto selects focused/hybrid, system-barycenter, or root-star approximation in that order of applicability.

Physics and display indices are separate. A focused full-N-body job advances and applies every body while the canvas renders only focused indices. Its full-state samples are mass-aggregated into inset marker trails. Approximate results lock Auto to approximation until the state is reset or replaced because excluded orbital history cannot be recovered.

`SystemGroup` hierarchy guides approximation and display selection. A group such as `Alpha Centauri AB` or `Proxima Centauri System` is a semantic navigation unit; it does not isolate gravity or create a separate physics engine. `Body.parent_id` can also define focusable local systems, such as a star and its planets or, later, a planet and its moons.

In `system_overview`, group barycenters are computed from current body masses, positions, and velocities. The overview state is temporary: playback advances group markers and group trails, updates elapsed time, and does not write barycenter positions back into the real star and planet bodies.

In `hybrid_focused_context`, focused bodies are merged back into the full body state after playback. Context barycenters are temporary display state: they draw markers and trails in the overview inset and do not mutate real bodies or apply gravitational feedback into the focused subsystem.

## Trail Sampling

Dense trail rendering uses `advance_with_samples()` to collect positions after each bounded internal step. At a visible interval of 30 days, the physics and trail renderer may see many internal samples instead of one sparse 30-day line segment. This keeps zoomed inner-planet trails from looking polygonal or rosette-like solely because of display sampling.

For large visible time steps, the UI decimates those physics samples before appending trails. Trail cadence is a display policy and should not change integration accuracy. In scoped body modes, trails are appended only for active bodies. In `system_overview`, separate group trails are appended instead of body trails. In `hybrid_focused_context`, focused body trails and coarse inset context trails are both sampled from their own worker results. Focus and Fit uses the visible step as its trail cadence, including for sub-day compact orbits.

## Display Scale Helpers

`src/scales.py` also owns display-only scale helpers such as elapsed-time formatting, distance formatting, and body-to-body distance measurement for UI readouts. These helpers may use AU or light-years for presentation, but physics and model state still use meters.

## Stability Expectations

For the bundled Solar System preset, repeated `advance(..., 30 * DAY)` calls should keep Mercury near its starting orbital radius instead of sending it multiple AU away.
