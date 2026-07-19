# Next Steps

Potential follow-up work for future sessions.

## Reference Frames

- Add verified built-in transforms between standard astronomical frames such as ICRF, ecliptic, equatorial, and galactic coordinates.
- Add epoch-aware transformations, including state propagation, time-scale conversion, and precession/nutation where applicable.
- Add authoritative JPL-backed origin changes for compatible systems instead of requiring users to supply translation and rotation values manually.
- Add continuously translating or rotating analysis frames, with the required Coriolis, centrifugal, and other non-inertial terms kept explicit in the physics model.
- Add relative diagnostics and export presets centered on a selected body, subsystem barycenter, or whole-system barycenter.
- Validate that selected standard-frame metadata matches the applied transform instead of trusting arbitrary labels and matrices.

## Packaging And Quality

- Add Flatpak build verification to CI once CI exists.
- Add UI smoke tests if a GTK test harness is introduced.
- Replace placeholder AppStream metadata and screenshots.
- Expand README with Builder-specific setup notes.
