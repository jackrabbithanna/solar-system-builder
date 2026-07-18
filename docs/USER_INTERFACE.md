# User Interface

Solar System Builder opens to a 2D, top-down simulation canvas with playback controls, simulation settings, and a right-side editor for saved systems and bodies.

## Canvas

The canvas shows the active simulation view.

- Colored dots represent visible bodies, or group markers when the app is showing a system overview.
- Moons use compact circles, asteroids use irregular markers, and comets have a short tail pointing away from their parent star.
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
- Focus + Coarse Context simulates a focused subsystem together with outside aggregate context so they exert gravity on each other.

Auto starts with a hardware-neutral estimate and refines it from measured full-physics worker times. If full N-body exceeds the budget, Auto chooses the best available approximation. Once approximate history has been applied, Auto remains approximate until Reset because omitted orbital phases cannot be reconstructed exactly. Selecting Full N-body after that history offers to reset first.

## Focus and Selection

The body list on the right shows groups and bodies in hierarchical order. Group rows show a group type and body count. Body rows show a color swatch, the body name, and relationship text such as the parent it orbits or the nearest other star.

Selecting a body loads its editable properties. Selecting a group loads a group focus view and disables body-specific edit fields. The selected name field can rename the selected body or star-system group.

The Focus and Fit button appears when the selected body or group has a focusable subsystem. Activating it:

- temporarily uses Follow Selected without changing the saved View Scale Mode or Physics Policy,
- resets canvas zoom,
- chooses a visible time step from the shortest focused orbit and current accuracy profile,
- clears existing trails.

Planets with moons are focusable subsystems. Under Focus + Coarse Context, the host star participates in the coupled worker simulation while only the planet and its descendants are rendered in the main canvas.

Outside planetary Focus and Fit, moon markers and trails are collapsed into their parent planets. Auto and scoped physics use a mass-weighted planet-and-moons proxy so moon periods do not force planetary-scale playback to use moon-scale internal steps. The window subtitle shows `moons collapsed` while this proxy is active. Explicit Full N-body still simulates every moon, but keeps moon-level rendering hidden until planetary focus.

The focused time step and trail cadence may be edited without changing the saved values. Changing accuracy recalculates an automatically selected focused step, while a manually edited focused step is retained until focus ends. Under Full N-body, hidden bodies continue evolving while only the focus is rendered. Click Focus and Fit again, select another hierarchy item, or change view/policy to leave focus and restore the stored view settings.

While focused, the fitted camera uses a rotation-independent radial extent with headroom. It expands as needed to keep the focused bodies visible and contracts gradually after a sustained decrease in system extent, avoiding rapid zoom changes as binaries and planets rotate.

## System Controls

The system menu in the header switches between bundled presets and saved systems.

- Create new system offers From Preset, Sol with JPL Horizons, and Custom System workflows.
- Duplicate current system creates and selects an editable saved copy, including the current unsaved simulation state.
- Save current system writes a dirty user system to the local library. Bundled presets require the explicit Duplicate to Edit action.
- Delete saved system removes the selected user-saved system. Bundled presets cannot be deleted.

The System Name and Description fields edit user-saved metadata. The reference-frame summary shows the shared epoch, time scale, center, plane, and reference system. Existing frame metadata is read-only because changing it correctly requires transforming every body vector.

Refresh from JPL Horizons updates all Horizons-backed bodies to one current instant. It works directly on editable systems and marks them dirty. For a bundled preset, it creates, saves, and selects an editable `<Preset Name> Updated` copy; installed preset files remain unchanged. Systems without compatible Horizons frame metadata or bodies disable the action.

The title shows `*` while a saved system has unsaved changes. Switching systems, creating another system, closing, or resetting offers Save, Discard, and Cancel. The header Save button writes changes without leaving the current system. Reset then restores the loaded or last-saved snapshot and clears unsaved edits.

### Creation Workflows

- From Preset duplicates the selected bundled system under a unique user name.
- Sol with JPL Horizons creates a Sun-only system in a heliocentric TDB/ICRF/ecliptic frame that can accept live Horizons bodies.
- Custom System creates a single-star, binary-star, or hierarchical starter. Primary and secondary star controls accept name, mass, radius, XYZ position, and XYZ velocity.

## Creation And Deletion

The body hierarchy Add menu creates a nested star system, star, planet, dwarf planet, moon, comet, or asteroid. Manual creation collects physical and appearance fields, then accepts either complete Cartesian XYZ/velocity state or orbital inputs that generate the canonical vector.

Adding a star system creates a semantic `SystemGroup` with one starter root star. Stars are roots. Planets, dwarf planets, comets, and asteroids require a star parent; moons require a planet or dwarf-planet parent.

Deleting a selected body or star-system group shows a destructive confirmation with affected descendants. Confirming removes the selected item and its child planets, moons, and nested groups. A system must keep at least one body.

## Body Editor

The right-side editor shows fields for the selected body.

- Name edits the selected body name. When a star-system group is selected, the same field edits the group name.
- Distance Unit changes the unit used by the X, Y, and Z position fields.
- Kind and Parent change the body type and valid orbital/display parent.
- Mass (kg) edits the body mass in kilograms.
- Radius (m), Color, Visible, and Trail edit physical and display properties.
- X, Y, and Z edit the body's 3D position in the selected distance unit.
- VX, VY, and VZ edit the body's velocity in meters per second.
- Apply Body Changes validates and commits the inspector fields atomically.

Position units available in the editor:

- km
- AU
- kAU
- ly

Changing kind, parent, mass, radius, position, or velocity rebuilds simulation state, clears trails, and marks the canonical source Cartesian. The physics model stores values internally in SI units: kilograms, meters, meters per second, and seconds.

When a body has a parent, the distance panel shows its distance to that parent. For root stars in multi-star systems, it shows distances to the other stars.

Selecting a group replaces body fields with Group Properties. Kind and Parent Group can be changed, subject to hierarchy-cycle and orbit-target validation. Group names and orbital generation remain available in the same inspector.

## Orbital Data

Bodies and groups show an Orbital Data section. Use it to enter published orbital parameters and generate the raw position and velocity fields used by the simulator.

- Semi-major Axis (AU) sets the orbit size directly. Hyperbolic trajectories use a negative axis.
- Period (days) can be used instead of semi-major axis; the app derives the orbit size from the parent and body masses.
- Eccentricity sets the conic shape. Values below 1 are elliptic, values above 1 are hyperbolic, and exactly 1 is unsupported. Hyperbolic trajectories cannot use Period.
- Inclination, Node, Periapsis, and Mean Anomaly set the 3D orientation and orbital phase.
- Epoch, Source, Source URL, and Notes preserve provenance and assumptions.
- Generate State Vector writes the derived position and velocity into the selected body, rebuilds the simulation state, and clears trails.
- For groups, Target selects the body or group the selected group barycenter should orbit.
- Generate Group Barycenter moves all bodies in the selected group so the group barycenter follows the entered orbit.
- Generate Binary Pair places two direct body members around their shared barycenter according to their masses.
- If a selected root star belongs to a two-star group, edit the orbital fields there and use Generate Binary Pair; the single-body Generate State Vector action remains disabled because the star has no parent body.

If a published exoplanet record does not include orientation or phase, leave those fields at their defaults. The resulting system is an approximate simulation seed, not a precise ephemeris.

## JPL Horizons

Search JPL Horizons appears in the Add menu for editable Sol systems with compatible frame metadata. Search results exclude unsupported records such as spacecraft and barycenters. Fetching runs in the background and shows progress.

Starting a Horizons search pauses playback and invalidates any unfinished playback step. Requests use the system epoch plus the displayed elapsed simulation time. When the selected parent has a Horizons catalog id, vectors are fetched relative to that parent and translated onto its current system-frame position and velocity; moon imports require such a cataloged parent. Adding the body preserves the displayed elapsed time.

The review dialog shows the resolved body, type, parent, parent-relative and resulting system-frame XYZ/velocity vectors, source, and SPK catalog id. It prefills mass and radius when JPL supplies GM, mean-radius, radius, or small-body diameter data; GM is converted to mass and diameter to radius in SI units. Any unavailable physical value remains required before Add Body. Imported bodies retain their Horizons source, retrieval date, catalog id, optional osculating elements, and `horizons` canonical-state origin. Duplicate catalog ids are rejected.

After any body or star-system addition, compatible systems offer to refresh all Horizons bodies. The same workflow is available from the system-level Refresh button. It stops playback, shows cancellable per-request progress, fetches every vector in the shared system frame at one captured current UTC instant, converts that instant to the Horizons TDB epoch, and resets playback to zero elapsed time after a successful atomic update. A failed or cancelled body request applies nothing. Mass, radius, colors, visibility, trail settings, names, and hierarchy are preserved; non-Horizons bodies keep their entered Cartesian state.

## Narrow Windows

At widths up to 760 px, the canvas/editor split and the simulation-setting controls reflow vertically. The inspector remains scrollable.

## App Menu

The main menu contains Preferences, Keyboard Shortcuts, and About Solar System Builder. The current shortcuts dialog lists shortcuts for showing keyboard shortcuts and quitting the app.
