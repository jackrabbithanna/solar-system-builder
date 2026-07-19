# Orbital Data

Solar System Builder simulates Cartesian state vectors. Every body still needs:

- mass in kilograms
- radius in meters
- position in meters
- velocity in meters per second

Orbital data is optional metadata used to generate those state vectors. This is useful for exoplanet systems and hierarchical star systems, where published catalogs often provide orbital period, semi-major axis, eccentricity, and physical properties instead of a complete position and velocity at a known epoch.

## Canonical Simulation State

`Body.position_m` and `Body.velocity_mps` remain the canonical simulation state. `physics.py` reads those vectors through `SimulationState.from_bodies()` and does not read orbital metadata.

When a user generates a body state vector from orbital data, the app:

1. reads the selected body's parent body;
2. converts the orbital metadata into a relative Cartesian position and velocity;
3. adds the parent body's current position and velocity;
4. writes the result back to `position_m` and `velocity_mps`;
5. rebuilds the simulation state and clears trails.

Playback then advances the generated vectors directly.

When orbit guides are enabled, the canvas samples this stored metadata as a dashed reference conic around the anchor's current position. The guide is explanatory provenance: it does not affect fitting or physics and is not recomputed as a live osculating orbit after N-body playback changes the canonical vectors.

When a user generates group orbital state, the app computes the selected group's barycenter from its member bodies, generates a desired barycenter state around the selected target body or target group, and applies the same position/velocity delta to every body in the group. This preserves the group's internal layout while moving the whole subsystem.

Binary pair generation is different: for a group with exactly two direct bodies, the app generates both bodies around their shared barycenter and splits positions and velocities by mass so total momentum remains centered on the original group barycenter.

## Stored Metadata

Schema v9 stores optional orbital metadata on both bodies and groups and records canonical-state provenance:

- `orbit`: source orbital parameters used to generate a simulation seed.
- `data_source`: source name, URL, catalog id, retrieval date, and citation metadata.
- `orbit_target_type` and `orbit_target_id` on groups: the body or group that a group barycenter orbits.
- `state_origin` on bodies: `cartesian`, `orbital`, or `horizons`.
- `reference_frame` on systems: epoch, time scale, center, reference plane/system, and source.

Old systems migrate without orbital metadata. Existing preset and saved-system vectors remain valid.

## Supported Orbital Inputs

The converter supports bound Keplerian ellipses and unbound hyperbolic trajectories around a selected parent body or group/body target.

- Semi-major axis may be entered directly.
- Orbital period may be entered instead; the app derives semi-major axis from the parent and body masses.
- Elliptic trajectories use `0 <= eccentricity < 1` and a positive semi-major axis.
- Hyperbolic trajectories use `eccentricity > 1`, a conventional negative semi-major axis, and no orbital period.
- Exactly parabolic trajectories (`eccentricity = 1`) are not supported.
- Eccentricity defaults to `0`.
- Inclination, longitude of ascending node, argument of periapsis, and mean anomaly default to `0`.
- The reference plane is the app-local XY plane. Hyperbolic mean anomaly is not angle-wrapped.

The converter and canvas preserve full 3D vectors. The 2D renderer projects X/Y, while the scientific 3D renderer projects the configured orbital plane through its orbit camera.

## Body Orbits

Body orbital data is for a concrete body orbiting a concrete parent body. It is appropriate for planets, moons, and any star that has been intentionally modeled as orbiting a parent body. Root bodies can still expand the Orbital Data section, but generation is disabled until they have a parent.

## Group Orbits

Group orbital data is for barycenter-based systems, such as binary stars or a distant subsystem orbiting another subsystem. Groups can generate:

- a group barycenter orbit around a selected body or group target;
- a binary pair state for groups with exactly two direct bodies.

This is the preferred way to model Alpha Centauri A and B orbiting a common barycenter. It also supports moving a subsystem such as `Proxima Centauri System` around a target such as `Alpha Centauri AB`.

Groups still do not constrain runtime gravity. They store metadata and generate initial state vectors; playback remains a flat N-body simulation.

## Exoplanet Limits

Generated exoplanet systems are approximate simulation seeds, not authoritative ephemerides.

Common exoplanet catalogs often lack enough information to know the true 3D orientation and current orbital phase. Transit and radial-velocity discoveries can provide period, radius, mass or minimum mass, semi-major axis, eccentricity, inclination, transit time, or argument of periastron, but not always all of them. When users leave fields at defaults, the generated system is deterministic and useful for exploration, but it should be labeled as approximate.

## JPL Horizons Import

Sun-only systems created with the Sol workflow can search JPL Horizons. The client requests a Cartesian vector and osculating elements centered on the selected Horizons parent at the system epoch plus current simulation elapsed time. On review, the app translates the relative position and velocity onto the parent's current shared-frame state; that translated vector becomes canonical while the elements remain explanatory provenance. Moons therefore require a parent with a Horizons catalog id.

Horizons does not provide every physical field required by the simulator, so the review step requires the user to supply mass and radius. Imports record source URL, catalog id, retrieval date, and citation. Network requests are serialized on a background worker and stale/cancelled results are ignored on the GTK main thread.

The system-level Refresh from JPL Horizons action updates every Horizons-backed body at one captured current instant. Cartesian vectors use the system's shared heliocentric or solar-system-barycentric center rather than each body's parent; parent-centered requests are used only for refreshed osculating elements. Horizons supplies the TDB-UT offset used to store the exact shared TDB epoch. The client returns an immutable batch, and the app validates a cloned system before applying any result, so one failed or cancelled request leaves the entire system unchanged.

Refresh changes positions, velocities, orbital elements, epochs, and Horizons provenance. It preserves physical and display properties. Non-Horizons bodies keep their user-entered Cartesian states at the new epoch.

NASA Exoplanet Archive import remains out of scope. Its data can still be entered manually with source metadata.
