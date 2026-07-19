# Reference Frames and Coordinate Transformations

Solar System Builder separates the coordinates used to store and integrate a
system from the coordinates used to inspect it. This is the most important rule
for understanding the feature:

- The **canonical reference frame** belongs to the saved system. Its Cartesian
  vectors are the input to N-body integration. Transforming it changes the
  system document.
- An **analysis frame** is a session-only view of the current state. It can
  translate or rotate over time without changing the saved vectors or the
  forces integrated by the physics engine.
- A **relative export frame** is a snapshot derived from the current state. JSON
  exports remain inertial and importable; CSV exports can describe the active
  non-inertial analysis frame.

All internal state uses SI units: kilograms, meters, meters per second, and
seconds. Every transformation is three-dimensional.

## Which Feature to Use

| Goal | Feature | Changes saved Cartesian state? | Persists? |
| --- | --- | --- | --- |
| Convert a system from ecliptic to equatorial coordinates | Transform Reference Frame, Verified Standard Frame | Yes | Yes, after Save |
| Move a system to another epoch | Transform Reference Frame, Verified Standard Frame | Yes; the full state is propagated | Yes, after Save |
| Recenter canonical vectors on a body or barycenter | Transform Reference Frame | Yes | Yes, after Save |
| Apply a known custom rotation or origin offset | Transform Reference Frame, Custom Rigid Transform | Yes | Yes, after Save |
| Watch motion from a body, barycenter, rotating frame, or target pair | Analysis Frame | No | No |
| Inspect Coriolis and other apparent accelerations | Frame Diagnostics | No | No |
| Create a portable current-state system centered on a target | Export Current System, JSON | No; creates a derived document | Exported file only |
| Export relative coordinates and acceleration terms | Export Current System or Frame Diagnostics, CSV | No | Exported file only |

## Frame Vocabulary

A complete canonical frame has four independent parts:

**Origin.** The point whose position and velocity are zero at the reference
epoch. An origin may be an authoritative JPL center, a body, a subsystem
barycenter, the whole-system barycenter, or a custom point.

**Axes.** The directions represented by the X, Y, and Z components. Registered
axes have a defined astronomical orientation. Custom axes have only the labels
and transform supplied by the user.

**Epoch.** The physical instant at which the stored vectors, origin, and
registered orientation are defined. Standard-frame epochs are ISO date-times
such as `2000-01-01 12:00:00`.

**Time scale.** The clock convention used to write the epoch. Converting `UTC`
to `TDB`, for example, changes the calendar representation of an instant; it
does not by itself advance the system.

`reference_frame.epoch` and `reference_frame.time_scale` are the structured,
machine-readable values. The top-level `SolarSystem.epoch` field is a
human-readable summary used by the interface and may also include the axes and
origin labels.

## Canonical Frame Semantics

Canonical positions and velocities are stored in one shared inertial frame.
Even when its registered name contains “of date,” its orientation is evaluated
at the frame epoch and then held fixed during ordinary playback. The physics
engine never continuously rotates canonical axes and never adds fictitious
forces to N-body integration.

This frozen-frame rule also applies to origins. A canonical frame centered on a
body means that the body was at position and velocity zero at the reference
epoch. It does not constrain the body to remain at the origin during later
integration.

The rule has two useful consequences:

1. Newtonian and post-Newtonian integration always receive inertial Cartesian
   state.
2. A later canonical transformation can clearly distinguish physical epoch
   propagation from a change in coordinate labels.

## Registered Standard Axes

Matrices in the standard-frame registry map components from the app's ICRF
basis into the requested axes. They are proper rotations: finite, orthonormal,
and determinant `+1`.

| `axes_id` | Displayed system / plane | Definition | Epoch dependence | Eligible for Horizons refresh* |
| --- | --- | --- | --- | --- |
| `icrf` | ICRF / FRAME | Identity relative to the canonical ICRF basis | Fixed | Yes |
| `fk5_j2000` | FK5 / mean equator/equinox J2000 | FK5 J2000 orientation using the ERFA Hipparcos/ICRS link | Fixed | Yes |
| `mean_equator_of_date` | IAU 2006 / mean equator/equinox of date | IAU 2006 bias-precession matrix | Evaluated at epoch | No |
| `true_equator_of_date` | IAU 2006/2000A / true equator/equinox of date | IAU 2006 precession with IAU 2000A nutation | Evaluated at epoch | No |
| `jpl_ecliptic_j2000` | ICRF / ECLIPTIC | JPL J2000 ecliptic using obliquity `84381.448` arcseconds | Fixed | Yes |
| `mean_ecliptic_of_date` | IAU 2006 / mean ecliptic/equinox of date | IAU 2006 equatorial-to-ecliptic matrix | Evaluated at epoch | No |
| `true_ecliptic_of_date` | IAU 2006/2000A / true ecliptic/equinox of date | IAU 2006/2000A true equator and true obliquity | Evaluated at epoch | No |
| `galactic_iau1958` | IAU Galactic / Galactic plane | Conventional fixed ICRS-to-Galactic orientation | Fixed | Yes |
| `custom` | User labels | No assumed astronomical orientation | User-defined and fixed | No |

\* Horizons compatibility also requires a JPL-addressable origin and a
supported time scale. See [JPL Horizons interoperability](#jpl-horizons-interoperability).

Astropy and PyERFA provide time and standard-orientation calculations. The app
disables network downloads for Earth-orientation data and uses the packaged
`astropy-iers-data` tables. A `UT1` conversion outside the available table can
therefore fail instead of silently accepting degraded accuracy.

### Supported Time Scales

The schema accepts `UTC`, `UT1`, `TAI`, `TT`, `TDB`, `TCB`, and `TCG`.
Astropy resolves the physical instant and leap-second or relativistic scale
offsets. Epoch differences are computed through TAI before numerical
propagation.

Time-scale conversion is not a spatial transformation. If source and target
epochs denote the same instant, the propagation interval is zero even if their
written timestamps differ.

## Transforming the Canonical Reference Frame

Transform Reference Frame is available in the system sidebar for editable
saved systems. Duplicate a bundled preset before using it.

### Verified Standard Frame Workflow

Use Verified Standard Frame when the current system already has registered
axes and the target axes are in the registry.

1. Stop playback at the state to transform. The app also stops it automatically
   when the dialog opens.
2. Open **Transform Reference Frame…**.
3. Select **Verified Standard Frame**.
4. Choose the new origin. Available choices include the current origin, any
   local body, any subsystem barycenter, the whole-system barycenter, common
   JPL centers, and centers inferred from the catalog IDs of imported bodies.
5. Choose the target axes, ISO epoch, and time scale.
6. Review the preview and select **Transform**.
7. Wait for the full-state propagation and transform to complete. The operation
   reports progress and can be cancelled.
8. Inspect the result, then Save to persist it.

The operation runs from a clone. Cancellation or any validation, propagation,
ephemeris, or metadata error discards the clone, leaving the active system
unchanged.

### What a Verified Transform Does

The app performs the following pipeline:

1. Materialize the currently displayed simulation state into a cloned complete
   body model. This includes elapsed playback and any applied approximation
   motion.
2. Compute the current physical epoch as the stored frame epoch plus elapsed
   simulation seconds.
3. Propagate the complete cloned N-body state from that instant to the target
   epoch. The propagation honors the system's gravity model, integrator, and
   accuracy-derived maximum internal step.
4. Resolve the target origin at the target epoch. Local body and barycenter
   origins come from the propagated clone. An external JPL origin change is
   fetched when required.
5. Compute and apply the registered axes rotation.
6. Rebuild optional osculating orbital metadata from the transformed canonical
   state, update frame metadata, validate the complete result, and replace the
   active model atomically.
7. Reset simulation elapsed time to zero because the target epoch now describes
   the materialized state.

Changing an epoch is therefore not a metadata-only operation. The bodies are
numerically propagated through the requested interval.

### Standard Axes Rotation

Let `A_s(t_s)` map ICRF components into the source registered axes evaluated at
the source frame epoch. Let `A_t(t_t)` do the same for the target axes at the
target epoch. The source-to-target component rotation is:

```text
R = A_t(t_t) A_s(t_s)ᵀ
```

Source axes are evaluated at their stored frame epoch, not at the current
playback time, because those canonical axes have remained frozen since that
epoch. Target axes are evaluated at the target epoch and become the new frozen
canonical orientation.

### Origin Translation

For an origin position `o_r` and velocity `o_v`, expressed in source
coordinates at the transform instant, every body is changed by:

```text
r_new = R (r_old - o_r)
v_new = R (v_old - o_v)
```

Mass, radius, hierarchy, and body identity are unchanged. Subtracting origin
velocity is essential: a position-only recentering would not create a valid
inertial state centered on a moving target.

Local origins are mass weighted where applicable:

```text
r_barycenter = sum(m_i r_i) / sum(m_i)
v_barycenter = sum(m_i v_i) / sum(m_i)
```

A group barycenter includes the bodies resolved by that subsystem; the
whole-system barycenter includes every body.

### Authoritative JPL Origin Changes

A canonical JPL origin is frozen at its reference epoch; it does not continue
following the cataloged body. This matters when both epoch and center change.

For source center barycentric state `(s_0, u_0)` at source epoch `t_0`, target
center barycentric state `(c_1, w_1)` at target epoch `t_1`, and
`dt = t_1 - t_0`, the translation relative to the inertial continuation of the
source origin is:

```text
o_r = c_1 - (s_0 + u_0 dt)
o_v = w_1 - u_0
```

The result is rotated into the frozen source axes before it is supplied to the
canonical transform. The Solar System barycenter (`500@0`) is handled as an
exact zero barycentric state and does not require its own vector query.

Even when the source and target use the same JPL center ID, a nonzero epoch
change generally requires source- and target-epoch states. The center's curved
ephemeris path differs from the straight inertial continuation of the original
canonical origin.

### Orbital and Flyby Metadata

Cartesian state remains authoritative. A rigid transform first rotates stored
orbit-plane and flyby orientation fields so their provenance is not left in the
old plane. A verified epoch transform then recomputes existing body and group
orbits as Newtonian osculating elements at the target state.

If an ordinary orbit is degenerate and cannot be recomputed, its optional orbit
record is removed rather than retained with misleading values. A flyby must
retain its required orbit record, so the canonical propagated state is kept and
the orbit receives a warning when osculating elements cannot be rebuilt.

Osculating metadata is Newtonian even when the selected propagation model uses
the app's practical first post-Newtonian correction.

## Custom Rigid Transforms

Use Custom Rigid Transform when the source axes are custom or when an exact
user-supplied coordinate operation is required. It transforms the materialized
current state immediately; it does not numerically propagate to a different
target epoch.

The origin may be the current origin, a local body or barycenter, or an explicit
position and velocity. A new JPL center cannot be established from an arbitrary
custom offset; use a verified transform from a JPL-addressable source for that.

There are two rotation inputs:

- **Guided X/Y/Z Angles** applies fixed X, then fixed Y, then fixed Z rotations.
  For column vectors, `R = Rz(z) Ry(y) Rx(x)`.
- **Expert 3 × 3 Matrix** accepts rows that map old components into new
  components. The matrix must contain finite values, be orthonormal within the
  app's tolerance, and have determinant `+1`. Reflections and scale/shear
  matrices are rejected.

The same position and velocity equations shown above are applied. The result is
always labeled `axes_id: "custom"`, even if custom labels resemble a registered
standard. This prevents an unverified matrix from gaining false astronomical
provenance or becoming eligible for automatic standard-frame conversion.

## Analysis Frames

Open **Analysis Frame…** from the main menu to change the coordinates used for
the main canvas and subsequent analysis. Applying a frame stops playback,
clears recorded trails, resets the canvas view, and invalidates a pending worker
result. These steps prevent samples from different coordinate systems from
being connected.

Analysis-frame settings are session-only. Loading or replacing a system returns
to System / Inertial. To leave an analysis frame manually, choose System /
Inertial, Current canonical axes, and Fixed, then select Apply.

### What Changes and What Does Not

When an analysis frame is active, it affects:

- main-canvas body positions;
- configured orbit guides on the main canvas;
- main-canvas system-overview markers and selected group centers;
- newly recorded main-canvas trails;
- Frame Diagnostics and diagnostic CSV exports.

It does not affect:

- canonical body positions or velocities;
- force calculation or integration;
- saved system documents;
- the navigation/context inset, which remains inertial;
- earlier trails, which are cleared instead of converted.

Trail Perspective is disabled while an analysis frame is active. Analysis-frame
trail coordinates take precedence over focused-parent trail coordinates.

### Analysis Origins

| UI choice | Kinematics |
| --- | --- |
| System / Inertial | Fixed zero position, velocity, and acceleration in canonical coordinates |
| Whole-System Barycenter | Mass-weighted state and acceleration of all bodies |
| Body | Current position, velocity, and physical acceleration of that body |
| Group | Mass-weighted current state and acceleration of the group's bodies |

The selected origin also serves as the primary target for Co-rotating Target
Pair. System / Inertial can be used as a fixed primary at canonical zero.

### Analysis Axes and Rotation Modes

The Axes menu offers the current canonical axes or any registered standard axes.
Registered analysis axes require a verified canonical source frame; a custom
canonical frame can use only its current axes. The Rotation menu determines
whether the chosen orientation is fixed or evolves.

**Fixed.** Holds the chosen orientation constant. Registered axes are evaluated
relative to the canonical axes at the stored canonical epoch. Angular velocity
and acceleration are zero.

**Standard Axes of Date.** Re-evaluates the selected standard axes at every
simulation instant. This is the continuously moving version of an equator- or
ecliptic-of-date frame. Angular velocity and angular acceleration are derived
from matrices sampled 60 seconds before and after the current instant.

**Prescribed Rate.** Rotates the chosen base orientation about a user-supplied
nonzero axis. The UI accepts the initial angular rate in degrees per day and
constant angular acceleration in degrees per day squared. The instant at which
Apply is selected becomes phase zero and the rate reference instant.

For elapsed time `tau` since Apply:

```text
rate(tau)  = rate_0 + alpha tau
angle(tau) = rate_0 tau + 1/2 alpha tau²
```

The frame axes rotate by the right-hand rule; coordinate components receive the
corresponding negative phase rotation.

**Co-rotating Target Pair.** Defines its own axes, so the registered Axes
selection is ignored and disabled. The selected origin is the primary and
Second target is the secondary:

```text
+X = unit vector from primary to secondary
+Z = unit vector along (relative position × relative velocity)
+Y = +Z × +X
```

The triad is re-orthogonalized as a right-handed frame. Instantaneous angular
velocity and acceleration are estimated from nearby kinematic samples. The
sample interval is chosen from the pair's separation and relative speed and is
bounded between 1 millisecond and 60 seconds. Diagnostics include the current
physical acceleration in those samples. Canvas and trail transforms use the
current positions and velocities without an additional force evaluation. None
of these evaluations runs extra playback steps on the GTK thread.

A target pair is undefined if the targets coincide or have effectively zero
relative angular momentum. The app rejects the frame at Apply time when already
degenerate.

### Position and Velocity in an Analysis Frame

Let `O`, `O_dot`, and `O_ddot` be the origin state, `R` map canonical components
into analysis axes, and `omega` be the analysis frame's angular velocity written
in analysis components. The displayed state is:

```text
x' = R (x - O)
v' = R (v - O_dot) - omega × x'
```

The rotational velocity term is why a body stationary in inertial coordinates
can have nonzero velocity in a rotating frame.

### Playback and Dynamic Degeneracy

Canonical integration still runs in its normal worker job. The job retains
elapsed, position, and velocity samples, and transforms trail samples into the
analysis frame in that background worker. GTK only applies the completed
arrays.

A target-pair frame can become degenerate later even if it was valid when
applied. If a worker cannot transform a sample, it returns no analysis trail
samples for that update rather than mixing inertial and relative coordinates.
If the current canvas evaluation is degenerate, that render falls back to
canonical inertial coordinates. Canonical simulation results are still valid
and continue to be applied.

## Apparent-Acceleration Diagnostics

Open **Frame Diagnostics…** from the main menu. The dialog reports frame-origin
position and velocity, angular velocity and acceleration, then the selected
body's relative state and each acceleration term. Reopen it to update the
snapshot. **Export CSV…** writes rows for all bodies.

For canonical physical acceleration `a`, the terms written in analysis
components are:

```text
physical/gravitational =  R a
translation            = -R O_ddot
Coriolis                = -2 omega × v'
centrifugal             = -omega × (omega × x')
Euler                   = -alpha × x'

total apparent = physical + translation + Coriolis + centrifugal + Euler
```

“Gravitational” uses the system's selected Newtonian or first post-Newtonian
acceleration implementation. It is the physical force-model term, not a claim
that the other displayed terms are physical forces.

For an inertial analysis frame with a fixed origin, the translation, Coriolis,
centrifugal, and Euler terms are zero. For a freely accelerating body origin,
the translational term subtracts that body's acceleration. For a uniformly
rotating frame, the Euler term is zero but Coriolis and centrifugal terms can be
nonzero.

## Exporting Frame Data

Export Current System pauses playback and materializes the latest applied
state. It offers two different output families.

### Importable JSON Snapshots

JSON exports preserve the canonical axes and create an inertial state at the
current simulation epoch. They do not serialize the active analysis-frame
rotation.

Available origins are:

- Current inertial snapshot;
- the selected body;
- any subsystem/group barycenter;
- the whole-system barycenter.

Body and barycenter snapshots subtract both target position and target velocity.
The target is at rest at the snapshot epoch, but the exported system does not
continuously follow it after import. Existing osculating metadata is recomputed
from the exported state.

For Current inertial snapshot, the vectors are unchanged after materialization,
but the epoch advances to the current instant. Its origin is deliberately stored
as `custom: inertial-snapshot-origin`. Retaining a JPL or body origin while
changing its epoch would falsely claim that the unchanged frozen origin had
followed that cataloged object to the new instant.

### Diagnostic CSV

CSV output uses the active analysis frame and contains one row per body. Numeric
values use up to 17 significant digits. Columns are:

- identity and context: `body_id`, `name`, `mass_kg`, `epoch`, `time_scale`,
  `analysis_frame`;
- relative position: `x_m`, `y_m`, `z_m`;
- relative velocity: `vx_mps`, `vy_mps`, `vz_mps`;
- physical/gravitational acceleration: `gravity_*_mps2`;
- origin-translation acceleration: `translation_*_mps2`;
- `coriolis_*_mps2`, `centrifugal_*_mps2`, and `euler_*_mps2`;
- the component sum: `apparent_total_*_mps2`.

CSV is diagnostic data, not an importable solar-system document.

## JPL Horizons Interoperability

Horizons requests are always made in an ICRF request frame for one resolved
physical instant. Returned vectors and orbital orientations are then rotated
into the system's fixed registered axes. This keeps JPL request conventions
separate from canonical storage conventions.

A system is compatible with Horizons import or refresh only when all of the
following are true:

- the origin kind is `jpl` with a valid `500@<id>` center;
- the time scale is one of the supported schema scales;
- axes are `icrf`, `fk5_j2000`, `jpl_ecliptic_j2000`, or
  `galactic_iau1958`.

Import additionally requires a root Sun/Sol body or a root star with JPL catalog
ID `10`.

Axes of date are intentionally excluded from refresh. Refresh can update the
Horizons-backed subset of a mixed system, while changing an epoch-dependent
canonical orientation would require rotating every body consistently. Use a
verified whole-system transform when a new of-date orientation is required.

Changing to a local body or barycenter origin also removes Horizons
compatibility because that origin no longer has an authoritative JPL center ID.
Changing back to a JPL center requires a verified transform from a current
JPL-addressable source; an arbitrary custom transform is not sufficient.

## Document Schema

Schema version 14 stores structured axes and origin identifiers. A standard
frame has this shape:

```json
{
  "reference_frame": {
    "epoch": "2000-01-01 12:00:00",
    "time_scale": "TDB",
    "axes_id": "jpl_ecliptic_j2000",
    "origin": {
      "kind": "jpl",
      "id": "500@0"
    }
  }
}
```

A frame centered on a linked local body uses the body's document ID:

```json
{
  "reference_frame": {
    "epoch": "2040-01-01 00:00:00",
    "time_scale": "TT",
    "axes_id": "mean_equator_of_date",
    "origin": {
      "kind": "body",
      "id": "body-uuid"
    }
  }
}
```

Custom axes also preserve user-facing labels:

```json
{
  "reference_frame": {
    "epoch": "2040-01-01 00:00:00",
    "time_scale": "TT",
    "axes_id": "custom",
    "origin": {
      "kind": "custom",
      "id": "user-defined"
    },
    "custom_reference_system": "spacecraft-local",
    "custom_reference_plane": "instrument XY"
  }
}
```

### Origin Kinds

| `kind` | `id` |
| --- | --- |
| `custom` | Required descriptive identifier |
| `jpl` | Required `500@<id>` center |
| `body` | Required body document ID |
| `group_barycenter` | Required group document ID |
| `system_barycenter` | Omitted |

Body and group origin IDs are validated against the containing document.
Duplicate and import workflows regenerate those linked IDs together with the
body/group IDs.

Schema v14 still reads older reference-frame metadata. Only legacy Horizons
frames explicitly identified as ICRF/FRAME or ICRF/ECLIPTIC are promoted to
registered axes. Arbitrary app-local labels migrate to `custom`; labels alone
are not treated as proof of a standard orientation. New serialization emits the
structured v14 form and emits custom system/plane labels only for custom axes.

## Developer API and Invariants

The implementation is GTK-free below `window.py`.

| Module | Responsibilities |
| --- | --- |
| `models.py` | Schema registry, `ReferenceOrigin`, `SystemReferenceFrame`, link validation, migrations |
| `standard_frames.py` | Epoch parsing/conversion, axes matrices, registered-frame rotations |
| `reference_frames.py` | Proper-matrix validation, rigid transforms, epoch propagation, barycenter origins, metadata rebuilding |
| `analysis_frames.py` | Frame kinematics, relative state, target-pair axes, apparent-acceleration decomposition |
| `frame_exports.py` | Importable relative JSON snapshots and diagnostic CSV serialization |
| `horizons.py` | ICRF request frames, canonical result rotations, authoritative JPL origin changes |
| `playback.py` | Worker-side transformation of time-stamped trail samples |
| `window.py` | Dialogs, background scheduling, cancellation, and GTK-main-thread application |

Important public contracts include:

- `axes_matrix(axes_id, epoch, time_scale)` returns an ICRF-to-axes proper
  rotation evaluated at one epoch.
- `rotation_between_frames(source, target)` requires verified source and target
  axes and returns `A_target @ A_source.T`.
- `transform_system_reference_frame(system, target, transform)` applies one
  already-resolved rigid transform to a clone. It does not perform epoch
  propagation or authoritative origin lookup.
- `transform_system_to_standard_frame(...)` propagates the full cloned state,
  resolves local or supplied external origins, transforms it, and recomputes
  optional osculating metadata.
- `frame_kinematics(...)` evaluates origin, rotation, angular velocity, and
  angular acceleration at one `SimulationState.elapsed_s`.
- `transform_state(...)` transforms position and velocity but never feeds the
  result back to canonical integration.
- `relative_diagnostics(...)` computes the physical acceleration and all
  explicit apparent terms.
- `relative_system_snapshot(...)` and `serialize_relative_csv(...)` are
  non-mutating export helpers.

An `external_origin` supplied to `transform_system_to_standard_frame` must be
the target origin's position and velocity relative to the inertial continuation
of the source origin, expressed in the frozen source axes at the materialized
transform instant. Passing a raw barycentric target vector is incorrect unless
the source origin is the stationary Solar System barycenter.

Display-only calls should pass `include_acceleration=False` to
`frame_kinematics(...)` when acceleration terms are unnecessary. Full
diagnostics use the default `True`. Trail transforms belong in the playback
worker because they operate over many samples. Mutable GTK widgets and the
active model are changed only on the GTK main thread through completed-result
callbacks.

## Accuracy and Scope

These features are intended for visualization, education, and simulation
experiments, not precision navigation or mission design.

- Epoch changes use the app's numerical full-state N-body propagator, not a
  high-precision ephemeris replacement for every body.
- JPL is used for authoritative center translation when requested, while system
  bodies continue to follow the app's propagated state unless separately
  refreshed.
- Coordinate-time scales identify instants; the transform does not implement a
  general relativistic coordinate transformation between spatial reference
  systems.
- Canonical transforms use Galilean position/velocity translation plus a rigid
  rotation. They intentionally omit moving-axis velocity terms because the new
  canonical axes become frozen.
- Of-date and target-pair angular derivatives are numerical estimates.
- The practical first post-Newtonian force option is not full relativity, and
  rebuilt osculating elements remain Newtonian.
- Custom matrices cannot be certified as registered axes by naming them.

## Troubleshooting

**“The current custom axes cannot be converted automatically.”** The source has
no verified relation to ICRF. Apply a known custom rigid transform, or begin
from data with registered frame provenance.

**“A JPL origin change requires a current JPL-addressable origin.”** The app
cannot derive an authoritative barycentric offset from a local or custom
origin. Transform from a canonical JPL origin or retain the local origin.

**A standard transform takes a long time.** The epoch interval is being
integrated with accuracy-bounded full N-body steps. A long interval or
short-period subsystem can require many steps. The progress dialog may be
cancelled without partially applying the result.

**Horizons actions are disabled after a transform.** Verify that the origin is
JPL-addressable and the axes are one of the four refresh-compatible fixed
registrations.

**A rotation matrix is rejected.** Rows must describe a pure right-handed
rotation. Check `R Rᵀ = I` and `det(R) = +1`; scaling, shear, reflections, and
rounded low-precision matrices can fail validation.

**A co-rotating frame cannot be applied or briefly falls back to inertial.** The
chosen targets are coincident or their relative position and velocity do not
define a stable angular-momentum direction at that instant.

**Trails disappeared after changing a frame.** This is intentional. Joining
samples expressed in different frames would draw a physically meaningless
path.

## Tests

Coverage is divided by layer:

- `tests/test_models.py`: schema, origins, compatibility, migrations, linked-ID
  duplication;
- `tests/test_standard_frames.py`: time scales, standard axes, proper rotations,
  precession/nutation, epoch-aware rotations;
- `tests/test_reference_frames.py`: rigid transforms, epoch propagation,
  origins, metadata, cancellation, and atomic failure;
- `tests/test_analysis_frames.py`: translating, prescribed, axes-of-date, and
  target-pair frames plus acceleration terms;
- `tests/test_frame_exports.py`: relative JSON and diagnostic CSV;
- `tests/test_horizons.py`: request-frame conversion, canonical rotations,
  origin changes, import, and refresh contracts;
- `tests/test_playback.py`: worker-side analysis trail sampling and generation
  safety.

Run the full suites after behavior or packaging changes:

```sh
.venv/bin/python -m unittest discover -s tests
meson test -C builddir
```
