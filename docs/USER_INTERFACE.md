# User Interface

Solar System Builder opens to a 2D, top-down simulation canvas with playback controls, simulation settings, and a right-side editor for saved systems and bodies.

## Canvas

The canvas shows the active simulation view.

- Colored dots represent visible bodies, or group markers when the app is showing a system overview.
- Faint colored lines show orbital trails collected during playback and manual stepping.
- A white ring marks the selected body or selected overview group.
- A small red dot marks the shared barycenter when the visible active set has enough mass data to compute one.
- In Hybrid Focus mode, a lower-left overview inset shows outside systems while the focused subsystem remains fitted in the main view. The focused system has a white ring in the inset; click another inset marker to select it and leave the focused view.

Move the pointer over a body or group marker to see its name. Click a body to select it in the body list and load its editable properties. In System Overview mode, click a group marker to select that group.

Scroll over the canvas to zoom. Scrolling up zooms in and scrolling down zooms out.

## Canvas Zoom Controls

The canvas has overlay zoom buttons in the upper-right corner.

- Zoom Out reduces the current zoom level.
- Reset Zoom returns the canvas to the fitted default zoom.
- Zoom In increases the current zoom level.

Zoom is clamped between the default fitted scale and the maximum zoom level. Zoom Out and Reset Zoom are disabled while the canvas is already at the default fitted scale.

## Playback Controls

The header bar contains the main simulation controls.

- Play or pause starts and stops continuous simulation playback.
- Step backward moves the simulation back by one visible time step.
- Reset to loaded state stops playback and restores the current system to the state it had when it was loaded or last saved.
- Step forward moves the simulation forward by one visible time step.

The label below the canvas shows the current elapsed simulation time. Playback and stepping append trails for active bodies or overview markers.

## Simulation Settings

Simulation settings are shown below the canvas. They are saved with the current system when you save or duplicate it.

### Time / step

Time / step controls the visible simulation interval used by Step Forward, Step Backward, and each playback update. The numeric field sets the amount, and the unit menu sets the scale.

Available time units:

- Days
- Years
- Decades
- Centuries
- Millennia
- Myr

This is a user-facing playback interval, not a single physics integrator step. The app internally breaks large visible steps into smaller substeps to keep the simulation stable and to draw smoother trails.

### Simulation Accuracy

The accuracy menu controls how small the internal simulation substeps should be.

- High uses smaller internal steps for more accurate motion.
- Balanced is the default tradeoff between speed and accuracy.
- Fast allows larger internal steps for quicker playback.

The window subtitle shows the effective physics policy, whether Auto is using an approximation, and the current maximum internal step in days.

### View Scale Mode

The view mode controls how the canvas is centered and scaled.

- Fit System centers the full active system around its mass-weighted center and fits it into the canvas.
- Follow Selected centers the view around the selected body, the selected body's parent, or the selected group.
- Log Overview compresses large distances so wide systems can be inspected more easily.

Changing the view mode clears existing trails because the active display context may change.

### Physics Policy

The physics policy controls which bodies or aggregate system markers participate in integration. Focus and Fit independently controls which detailed bodies are displayed.

- Full N-body simulates all bodies together.
- Auto predicts the cost of full N-body and uses it when one update should finish within approximately 200 ms.
- System Barycenters simulates high-level group barycenters instead of every body.
- Root Stars simulates root stars without their descendants.
- Focused Subsystem simulates the selected body or group context, such as a star and its descendants.
- Focus + Coarse Context simulates a focused subsystem and outside aggregate context separately.

Auto starts with a hardware-neutral estimate and refines it from measured full-physics worker times. If full N-body exceeds the budget, Auto chooses the best available approximation. Once approximate history has been applied, Auto remains approximate until Reset because omitted orbital phases cannot be reconstructed exactly. Selecting Full N-body after that history offers to reset first.

## Focus and Selection

The body list on the right shows groups and bodies in hierarchical order. Group rows show a group type and body count. Body rows show a color swatch, the body name, and relationship text such as the parent it orbits or the nearest other star.

Selecting a body loads its editable properties. Selecting a group loads a group focus view and disables body-specific edit fields.

The Focus and Fit button appears when the selected body or group has a focusable subsystem. Activating it:

- temporarily uses Follow Selected without changing the saved View Scale Mode or Physics Policy,
- resets canvas zoom,
- chooses a visible time step from the shortest focused orbit and current accuracy profile,
- clears existing trails.

The focused time step and trail cadence may be edited without changing the saved values. Changing accuracy recalculates an automatically selected focused step, while a manually edited focused step is retained until focus ends. Under Full N-body, hidden bodies continue evolving while only the focus is rendered. Click Focus and Fit again, select another hierarchy item, or change view/policy to leave focus and restore the stored view settings.

While focused, the fitted camera uses a rotation-independent radial extent with headroom. It expands as needed to keep the focused bodies visible and contracts gradually after a sustained decrease in system extent, avoiding rapid zoom changes as binaries and planets rotate.

## System Controls

The system menu in the header switches between bundled presets and saved systems.

- Duplicate current system creates a saved copy of the current system.
- Save current system writes the current system to the local library. Saving a bundled preset first creates a user-saved copy.
- Delete saved system removes the selected user-saved system. Bundled presets cannot be deleted.

The System Name field can rename user-saved systems. Bundled presets are read-only until saved or duplicated.

## Body Editor

The right-side editor shows fields for the selected body.

- Distance Unit changes the unit used by the X and Y position fields.
- Mass (kg) edits the body mass in kilograms.
- X and Y edit the body's 2D position in the selected distance unit.
- VX and VY edit the body's velocity in meters per second.

Position units available in the editor:

- km
- AU
- kAU
- ly

Changing mass, position, or velocity rebuilds the simulation state and clears existing trails. The physics model stores values internally in SI units: kilograms, meters, meters per second, and seconds.

When a body has a parent, the distance panel shows its distance to that parent. For root stars in multi-star systems, it shows distances to the other stars.

## Orbital Data

Bodies and groups show an Orbital Data section. Use it to enter published orbital parameters and generate the raw position and velocity fields used by the simulator.

- Semi-major Axis (AU) sets the orbit size directly.
- Period (days) can be used instead of semi-major axis; the app derives the orbit size from the parent and body masses.
- Eccentricity sets the ellipse shape.
- Inclination, Node, Periapsis, and Mean Anomaly set the 3D orientation and orbital phase.
- Epoch, Source, Source URL, and Notes preserve provenance and assumptions.
- Generate State Vector writes the derived position and velocity into the selected body, rebuilds the simulation state, and clears trails.
- For groups, Target selects the body or group the selected group barycenter should orbit.
- Generate Group Barycenter moves all bodies in the selected group so the group barycenter follows the entered orbit.
- Generate Binary Pair places two direct body members around their shared barycenter according to their masses.
- If a selected root star belongs to a two-star group, edit the orbital fields there and use Generate Binary Pair; the single-body Generate State Vector action remains disabled because the star has no parent body.

If a published exoplanet record does not include orientation or phase, leave those fields at their defaults. The resulting system is an approximate simulation seed, not a precise ephemeris.

## App Menu

The main menu contains Preferences, Keyboard Shortcuts, and About Solar System Builder. The current shortcuts dialog lists shortcuts for showing keyboard shortcuts and quitting the app.
