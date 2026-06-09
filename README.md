# BTopo

Blender addon for turning CAD geometry (laddered, triangulated, uneven
tessellation) into clean, game-ready hard-surface meshes.

CAD exports encode their design intent in face angles and split normals, not in
topology. BTopo extracts that intent as a **feature-edge graph** and uses it to
drive both automated in-place cleanup and fast manual retopology over the
source surface. See [DESIGN.md](DESIGN.md) for the full design and roadmap.

## Status

**v0.1 — scaffold.** The repair-in-place workflow is usable end-to-end:

- **Detect Features** — mark feature edges sharp (angle + boundary + non-manifold).
- **Select Issues** — QA selection of tris, n-gons, poles, non-manifold geometry.
- **CAD Cleanup** — weld seam doubles, dissolve ladders without crossing
  features, tris-to-quads.
- **Start/End Retopo Session** — one-click author-over setup: snapped,
  shrinkwrapped `_retopo` object with the source locked as reference.
- **Trace Feature Loops** — walks the source's feature-edge graph and
  generates the resampled structural cage in the retopo mesh: straight
  runs collapse to single edges, circles become clean n-gons (segment
  angle controlled), junctions and hard corners are preserved and welded.
- **Finalize Shading** — smooth + sharps + keep-sharp Weighted Normal modifier.

## Requirements

Blender 4.2 or newer.

## Install

1. Zip the repository contents (or `git archive -o btopo.zip HEAD`).
2. In Blender: `Edit > Preferences > Add-ons > Install from Disk…` and pick the zip.
3. The tools appear in the 3D Viewport sidebar (`N`) under the **BTopo** tab.

## Workflow

The panel is ordered as the pipeline:

1. **Analyze** — set the feature angle, run Detect Features, inspect with
   Select Issues.
2. **Cleanup** — run CAD Cleanup for in-place repair (mid/background assets).
3. **Retopo** — or start a retopo session and author a new mesh over the CAD
   reference (hero assets; the source doubles as your bake high-poly).
4. **Finalize** — apply the shading recipe before export.
