# BTopo — Design Document

**CAD → game-ready hard-surface topology, inside Blender.**

## 1. Problem statement

Geometry exported from CAD packages (SolidWorks, Fusion 360, CATIA, STEP/IGES via
converters) arrives in Blender as tessellated meshes with topology that is hostile
to game pipelines:

- **Laddered triangulation** — long, thin triangle strips along cylindrical and
  filleted surfaces.
- **Triangle fans** radiating from a single vertex on planar faces and caps.
- **Wildly uneven density** — thousands of triangles on a tiny fillet, two
  triangles on a large planar face.
- **No edge flow** — feature edges (the actual design intent) are encoded in
  split normals and face angles, not in the topology itself.
- **Degenerate geometry** — duplicate vertices along patch seams, zero-area
  faces, T-junctions where NURBS patches met.

Game engines need the opposite: controlled polycount, quad-dominant topology with
flow that follows feature edges, clean shading via sharp edges + weighted normals,
and UV-able, bake-able surfaces.

Today artists bridge this gap with a grab-bag of generic tools (Limited Dissolve,
Tris-to-Quads, manual retopo) or organic-focused addons (RetopoFlow) that don't
understand hard-surface features. **BTopo's thesis: the CAD mesh already contains
the design intent — feature edges, surface regions, symmetry. Extract it, and use
it to drive both automated cleanup and fast manual authoring.**

## 2. Target user & scope

- **User:** game environment/prop/vehicle/weapon artists ingesting CAD or
  CAD-derived meshes; technical artists building ingest pipelines.
- **In scope:** hard-surface meshes — machined parts, products, vehicles, props.
- **Out of scope (explicitly):** organic retopo (sculpt → animation topology),
  CAD import itself (STEP parsing — users bring a tessellated mesh), auto-UV and
  baking (we *prepare* for them, we don't replace dedicated tools).

## 3. The two workflows

BTopo supports two strategies, which can be mixed per-part:

### A. Repair-in-place (primary)
The CAD tessellation's vertices already lie *exactly* on the true surface,
and its feature edges are exact design intent. Every vertex kept is perfect;
anything that re-projects can only degrade it. So the primary workflow keeps
the tessellation as the base and edits topology around it: detect features →
weld seams → dissolve coplanar ladders → quadify → collapse ladder rungs →
re-span bevels → finalize shading.

**The in-place invariant: no vertex ever slides.** Tools prefer keeping or
removing existing vertices (rail resampling, rung dissolving); when a new
vertex is unavoidable (re-spanning a bevel) it is placed by arc length on an
existing cross-section polyline — on-surface by construction.

### B. Retopo-over (secondary, for freeform/heavily reworked regions)
The CAD mesh becomes a read-only *reference surface* and the artist authors a
new mesh over it, guided by the extracted feature graph. The CAD mesh doubles
as the **bake high-poly**: source = highpoly, authored mesh = lowpoly.

**Deliberately no live shrinkwrap.** Continuous conformance slides vertices
and rounds hard corners — unacceptable for hard surface. Generators project
explicitly (one-shot BVH, at creation time, onto exact geometry); freehand
edits rely on face-nearest snapping, which only affects vertices being moved.
The session links retopo → source via an object property, not a modifier.

The addon's UI is organized around a pipeline that serves both:

```
ANALYZE  →  CLEANUP (in-place)  →  RETOPO (author-over)  →  FINALIZE  →  EXPORT
```

## 3b. Plasticity bridge integration (first-class input path)

The primary intake target is [Plasticity](https://www.plasticity.xyz/) via its
official Blender bridge. The bridge live-links tessellated BREP geometry into
Blender and — crucially — preserves CAD topology metadata on the mesh:

- `obj["plasticity_id"]` / `obj["plasticity_filename"]` identify linked objects.
- `mesh["groups"]` holds one `(loop_start, loop_count)` pair per CAD BREP face;
  every polygon maps to its source CAD face through its loop range.
- `mesh["face_ids"]` carries the parallel Plasticity face IDs.

BTopo treats this as ground truth wherever it exists:

- **Exact feature detection.** An edge whose two polygons belong to different
  CAD faces is a real BREP boundary; an edge interior to one CAD face never
  is. This kills both failure modes of angle-threshold detection: tessellation
  noise inside curved faces (false positives) and shallow fillet boundaries
  (false negatives). The angle test still gates which boundaries are *sharp*;
  an optional **Tangent Boundaries** mode also includes smooth G1 transitions
  (fillet rails) — invisible to any angle test, but exactly the curves you
  want to trace and retopologize along.
- **Refresh-friendly.** The bridge overwrites sharp/seam marks on refacet, so
  BTopo derives rather than depends on them: re-running Detect Features after
  a refresh reconstructs the same feature graph from the group data.
- **Future:** per-CAD-face patch segmentation comes free from `groups`
  (no flood fill needed); `face_ids` enable stable correspondences across
  refreshes for persistent per-patch settings.

When no Plasticity data is present (STEP via other importers, OBJ/FBX exports),
everything falls back to angle-based detection — the rest of the pipeline is
unchanged.

## 4. Tool inventory

### 4.1 Analyze
| Tool | Description |
|---|---|
| **Detect Features** | Build the *feature graph*: mark edges as sharp where face angle exceeds threshold, plus boundaries and non-manifold edges. With Plasticity bridge data, detection is exact (see §3b). Optionally mark as UV seams and bevel weights. This is the foundation every other tool consumes. |
| **Select Issues** | Select triangles, n-gons, poles (interior valence ≠ 4), or non-manifold geometry for QA passes. |
| **Topology Report** | Counts: tris/quads/ngons, poles, ladder strips, degenerate faces, islands. Shown in the panel after analysis. |
| **Heatmap Overlays** *(v0.5)* | GPU-drawn viewport overlays: face density, aspect-ratio (ladder) highlighting, curvature. |
| **Patch Segmentation** *(v0.5)* | Flood-fill faces bounded by feature edges into named regions (planar / cylindrical / freeform classification). Drives patch-fill and auto-quadify. |

### 4.2 Cleanup (repair-in-place)
| Tool | Description |
|---|---|
| **CAD Cleanup** | One-click pass: weld doubles (CAD patch seams) → limited dissolve *delimited by detected sharp edges* (kills ladders on flat/smooth regions without eating features) → tris-to-quads respecting sharps. Reports before/after counts. |
| **Simplify Strip** *(prototyped)* | Collapse ladder rungs along a quad strip: dissolve whole cross-sections, keeping a curvature-driven subset of original vertices (straight fillet → one segment). Rail verts of dropped rungs dissolve out of neighbouring faces too, so ladders stop propagating into adjacent patches. `strip_grid.py` + `btopo.simplify_strip`. |
| **Set Strip Spans** *(prototyped)* | Rebuild a bevel/fillet strip at a chosen span count: rails stay fixed (neighbours unaffected), new verts placed by arc length on existing cross-sections. Matching counts across adjacent bevels makes loops continuous. Selections expand to whole CAD patches via the `btopo_patch` face attribute baked by CAD Cleanup. `btopo.set_strip_spans`. |
| **Collapse Fans** *(v0.2)* | Detect fan vertices on planar caps; replace fan with grid fill. |
| **Density Equalize** *(v1.0)* | Planar-decimate / subdivide per segmented patch to hit a target edge length. |

### 4.3 Retopo (author-over)
| Tool | Description |
|---|---|
| **Start Retopo Session** | One click: creates `<name>_retopo` object, configures face-nearest snapping, adds a Shrinkwrap (above-surface) modifier targeting the source, sets in-front + wire display, makes the source unselectable. Mirror modifier if the source is symmetric. |
| **Trace Feature Loops** *(prototyped)* | Walk the source's feature graph and generate corresponding edge loops in the retopo mesh — the artist gets the structural "cage" for free, then fills between rails. Implemented in `feature_graph.py` (graph build + adaptive resampling, unit-tested) and `btopo.trace_features`. |
| **Quad Strip / Bridge Fill** *(prototyped)* | Select two rails, fill with an even quad strip projected to the surface: rails are auto-paired (reversal/rotation), cut count auto-chosen for square quads, interior verts BVH-projected, faces oriented to the surface. Mismatched vert counts fall back to Blender's bridge. Implemented in `strip_fill.py` + `btopo.bridge_fill`. |
| **Patch Fill (Coons)** *(prototyped)* | Select a closed boundary loop; it is split into 4 sides at its sharpest corners, filled with a quad grid via bilinearly blended Coons interpolation, and BVH-projected to the surface. Opposite sides must have matching counts. Implemented in `patch_fill.py` + `btopo.patch_fill`. |
| **Surface Relax** *(v0.5)* | Modal brush: Laplacian relax that re-projects to the reference surface each step, with feature-edge vertices constrained to slide along their feature curve only. |
| **Draw Strips** *(v1.0)* | Modal tool: draw a stroke on the surface, get a quad strip following it; strokes snap to feature edges magnetically. |

### 4.4 Finalize & Export
| Tool | Description |
|---|---|
| **Finalize Shading** | Shade smooth + sharp edges from feature angle + Weighted Normal modifier (keep-sharp). The standard game hard-surface shading recipe. |
| **Seams from Features** *(v0.2)* | Copy sharp/feature edges to UV seams (with island-count sanity heuristics). |
| **Triangulation Preview** *(v0.5)* | Toggle a triangulate modifier matching engine settings (fixed/beauty) so the artist sees what the engine will see. |
| **Export Presets** *(v0.5)* | FBX/glTF presets: apply modifiers, triangulate, tangents, unit scale, axis per engine (Unreal/Unity/Godot). |
| **LOD Chain** *(v1.0)* | Generate LODs via feature-aware decimation (features dissolve last), named per engine convention. |

## 5. UX design

- **Location:** 3D Viewport sidebar (N-panel), tab **BTopo**, one collapsible
  sub-panel per pipeline stage in pipeline order. The panel order *is* the
  recommended workflow — discoverable for newcomers, fast for repeat use.
- **Modal tools** (relax, draw strips) register as proper toolbar tools with
  their own keymaps in later versions; v0.x ships them as operators.
- **Non-destructive bias:** everything that can be a modifier is a modifier
  (shrinkwrap, weighted normals, triangulate preview, mirror). Destructive
  cleanup operators always report counts and respect undo.
- **Local adjustment over global re-runs.** Semi-automatic tools are never
  right everywhere, so every generator must be correctable where it's wrong:
  1. *Redo panel first* — generators expose their full parameter set as
     operator properties (trace density/corner angle, bridge cuts/twist/flip,
     patch grid rotation), so the artist adjusts the result they're looking
     at; Blender's redo undoes and re-runs, so tweaking never duplicates
     geometry. Validation errors name the parameter that fixes them.
  2. *The cage is plain mesh* — per-rail span edits are native
     subdivide/dissolve: the live shrinkwrap keeps edits glued to the
     surface, and fills consume whatever the cage says. BTopo deliberately
     does not own these edits.
  3. *Planned (v0.5): persistent source correspondence* — traced vertices
     remember their source feature curve (int attribute) and patches their
     Plasticity `face_id`, enabling "retrace just this curve denser",
     per-patch remembered overrides, and cages that survive bridge refreshes.
- **Conventions:** feature edges = Blender sharp edges (interoperable with
  vanilla tools), retopo objects suffixed `_retopo`, source set unselectable
  during a session rather than hidden (it's the visual reference and bake target).

## 6. Architecture

```
btopo/
  blender_manifest.toml   # Blender 4.2+ extension manifest
  __init__.py             # registration only
  properties.py           # Scene-level PropertyGroup (tool settings)
  ops_analyze.py          # feature detection, issue selection
  ops_cleanup.py          # in-place repair operators
  ops_retopo.py           # retopo session + authoring tools
  ops_finalize.py         # shading / export prep
  panels.py               # UI
  core/ (future)          # feature_graph.py, ladders.py, patchfill.py, overlay.py
```

Technical choices:

- **Pure Python / bpy + bmesh.** No compiled dependencies — installability beats
  raw speed. Hot paths (full-mesh attribute scans) use
  `foreach_get`/`foreach_set` with NumPy where bmesh iteration is too slow.
- **Blender 4.2+ extension** (`blender_manifest.toml`); `bl_info` retained for
  legacy-style installs.
- **Surface snapping:** Blender's built-in Face-Nearest snapping + Shrinkwrap
  for interactive editing; `mathutils.bvhtree.BVHTree` for tool-driven
  projection (patch fill, relax) so results don't depend on viewport state.
- **Feature graph:** stored as native sharp-edge flags (single source of truth,
  user-editable with vanilla tools), with a cached Python-side graph
  (curves = chains of sharp edges between corner vertices) rebuilt on demand
  for trace/fill tools.
- **Overlays:** `gpu` module batch drawing in a `draw_handler`, data cached and
  invalidated on depsgraph updates.
- **Modal tools:** standard modal-operator pattern; later promoted to
  `WorkSpaceTool`s with gizmos.

## 7. Key algorithms (sketches)

- **Feature detection:** edge is a feature if dihedral angle > threshold, OR
  boundary, OR non-manifold. Future refinement: hysteresis (lower threshold to
  *extend* an already-started feature curve) to survive noisy tessellation on
  shallow fillets.
- **Ladder detection:** quad/tri-pair strips where aspect ratio > k and the
  strip is bounded by parallel feature curves; collapse = merge edge rungs
  (un-subdivide along strip axis).
- **Patch fill:** boundary loop → split into 4 logical sides at corner vertices
  (high feature-graph valence or sharp turns) → Coons patch interpolation for
  interior verts → BVH re-project to surface → a few constrained smoothing
  iterations.
- **Constrained relax:** interior verts: uniform Laplacian + re-project to BVH;
  feature verts: slide along their feature polyline only; corner verts: pinned.

## 8. Roadmap

| Version | Contents |
|---|---|
| **v0.1 (this scaffold)** | Extension skeleton, settings, panel; Detect Features, Select Issues, CAD Cleanup pass, Start Retopo Session, Finalize Shading. The repair-in-place loop is usable end-to-end. |
| **v0.2** | Trace Feature Loops ✓, bridge fill ✓, Coons patch fill ✓ (pulled forward), redo-panel adjustability (twist/flip/rotate/density) ✓, Plasticity face-group detection ✓. Remaining: ladder/fan collapse, seams-from-features, topology report. |
| **v0.5** | Patch segmentation, surface relax, persistent source correspondence (retrace selected curves, refresh-proof cages, per-patch overrides), GPU overlays, triangulation preview, export presets. |
| **v1.0** | Draw-strips modal tool with toolbar integration, fillet rebuild, density equalize, LOD chain, docs + demo assets. |

## 9. Landscape & differentiation

- **RetopoFlow** — excellent, but organic-first; no feature-edge awareness.
- **Quad Remesher / Remesh modifiers** — automatic, but smears hard-surface
  features and gives no authoring control.
- **Mesh Machine / Hard Ops** — hard-surface *modeling*, not CAD ingest/retopo.
- **CAD-native tools (e.g. InstaLOD, Simplygon)** — pipeline-grade decimation,
  not artist-grade topology authoring; expensive; outside Blender.

BTopo's niche: **feature-graph-driven, artist-in-the-loop hard-surface retopo,
native in Blender, spanning quick repair to hero-asset authoring.**

## 10. Open questions / risks

- Performance ceiling of pure Python on multi-million-tri CAD meshes — mitigate
  with NumPy paths; a compiled core is a last resort.
- Feature detection robustness on sloppy tessellations (near-threshold fillets) —
  hysteresis + manual mark/clear tools as escape hatch.
- Whether Trace Feature Loops should generate geometry or guide curves —
  prototype both in v0.2.
