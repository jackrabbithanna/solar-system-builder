# Next Steps

Potential follow-up work for future sessions.

## Simulator Features

- Add create/delete body controls.
- Add editable body radius, kind, color, visibility, and trail settings.
- Add explicit reset-to-bundled-preset and reset-to-last-save variants if users need separate commands beyond the current loaded-state reset.
- Add import/export of `.json` solar-system documents.

## Canvas And Interaction

- Add pan controls.
- Add fixed zoom or follow-selected-body modes.
- Add orbit/trail display options.
- Add visual indication while playback simulation is running.

## Physics

- Add configurable physics mode in the UI: Newtonian versus post-Newtonian.
- Add energy/angular-momentum diagnostics for debugging.
- Improve integrator options if long-range stability becomes a requirement.
- Add more regression tests around large masses, close approaches, and backwards playback.

## Details

Allow selection of systems from the canvas.
Zoom into systems, or stars, and automatically adjust simulation timescales / scope. 
Display a dot for baryocenters.
Add moons to solar system preset. New lower scale?
Exoplanet entry? Values like:
  "semi_major_axis_au": 1.0,
  "eccentricity": 0.0167,
  "inclination_deg": 0.0,
  "argument_of_periapsis_deg": 102.9,
  "longitude_of_ascending_node_deg": 0.0,
  "mean_anomaly_deg": 100.5,
  "epoch_jd": 2461205.5
Alpha Centuri 2 big system simulation looks very odd. "Wavy" 


## Packaging And Quality

- Add Flatpak build verification to CI once CI exists.
- Add UI smoke tests if a GTK test harness is introduced.
- Replace placeholder AppStream metadata and screenshots.
- Expand README with Builder-specific setup notes.

