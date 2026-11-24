# Tank Battles render scale design

The native frame is 1280×720. The title already renders its main color and scene depth in a 2×2 RSX sample grid, with 2560×1440 backing images. It then issues a tiled point resolve into the 1280×720 display buffer. Scale the main scene before this resolve, and carry the extra resolution through a host display image. Window size and Vulkan drawable size remain independent of the scene.

## Captured frames

An offline X Zone Battle captured at Native on Linux contained the following guest draw-end records. Each row uses the middle complete scope for a stable scene. Color and depth addresses are local VRAM offsets from this run of NPUB30154. They identify the evidence, not runtime constants. The snapshot includes enabled texture descriptors that can be inherited by shaders that do not use them.

| Scene | Main color | Scene zeta | Scene draws | Shadow draws | Resolve destination |
| --- | --- | --- | ---: | ---: | --- |
| Main menu | `0x0dc40000` | `0x0b440000` | 142 | 0 | ID 1 |
| Pack 2 X Zone selection | `0x0dc40000` | `0x0b440000` | 315 | 0 | ID 1 |
| X Zone briefing | `0x0cde0000` | `0x0b440000` | 249 | 67 | ID 2 |
| X Zone gameplay | `0x0cde0000` | `0x0b440000` | 275 | 82 | ID 2 |
| X Zone midgame | `0x0eaa0000` | `0x0b440000` | 227 | 79 | ID 0 |
| X Zone debriefing | `0x0dc40000` | `0x0b440000` | 206 | 62 | ID 1 |

The scene selects color B with pitch 10,240 bytes and surface word `0x090a5148`. Its zeta pitch is also 10,240. The shadow pass has no guest color target, uses zeta `0x0ac40000` with pitch 4,096, and declares 2048×2048 at a 1×1 sample grid. The engine uses a dummy color attachment at offset zero for the depth-only pass. The sampled shadow descriptor names the zeta offset, with a 2048×2048 view. Treat the shadow depth, its converted view, and the dummy color as a separate native group. The captured menus and game UI compose into the same main targets as the scene. These scopes show no separate post-processing draw target between the scene and the display resolve. Any future intermediate needs its own producer and consumer evidence before it qualifies.

Display registrations use pitch 5,120 and offsets `0x0ca20000`, `0x0c660000`, and `0x0c2a0000` for IDs 0, 1, and 2. All three appear as selected surfaces in the scanout order log. The six NV3089 writes cover columns `[0,512)`, `[512,1024)`, and `[1024,1280)` over rows `[0,512)` and `[512,720)`. The edge widths/heights are clipped from the declared 512×512 tiles. Each source base includes twice the destination's row and column within the appropriate scene backing image. The point gather uses a two-texel step in each dimension and `0x00100010` input UV. A scope opens at the preceding guest flip. Its transfer destination can therefore differ from the buffer ID in the opening marker. Identify the target by the write range and correlate it with subsequent presentation.

The local evidence is under `analysis/rendering/display-step3-x-zone/`. The saved Native PPMs for the main menu, X Zone selection, briefing, snow gameplay, midgame, and debriefing are scene references. They were sampled near the corresponding trace request, without claiming a pixel match to a particular flip. The raw captures contain game data and stay local. To reproduce, use `scripts/capture_rendering.py --rsx-trace --capture --frame-order` with the X11 Battle navigation in `analysis/rendering/fxaa-navigation.json` up to the briefing confirmation, skipping the F4 actions. Send unique scene labels to the capture's `rsx/request` file after each screen settles. `sdk_overrides/rsx_trace.inc` captures three guest flip scopes, and `sdk_overrides/rsx_scanout.inc` provides the transfer-only display surface fallback. Run `python3 scripts/render_scale_evidence.py <capture-directory> <report.json>` inside the checkout for the scalar surface manifest, resolve coverage, and pixel hashes of complete reference images.

## Eligibility and identity

- Group the live 3D scene-color generations with the scene zeta and a proven resolve route to the current display registrations. Use the color selector, format, sample grid, pitches, color/depth binding, and the explicit six-tile NV3089 mapping together. Offsets, image dimensions, and scanout registration alone cannot establish a role. Bind proof to the layout generation and invalidate it on re-registration, reuse with incompatible geometry or format, or a conflicting alias.
- Choose the policy before committing scaled contents for a frame. Start Native when the layout is unknown. Keep Native until the scene/resolve group has been observed, then switch resources at a safe frame boundary. For unexpected operations, preserve guest-coherent contents and use a Native path. Never reinterpret an in-flight image or leave mixed-size color and depth attachments.
- Give a display buffer scaled host backing only for a verified scene route and keep its guest representation at the registered size and pitch. For the first implementation, retain a Native representation or fall back if a buffer also receives an unhandled CPU/2D write, has an unresolved guest alias, or is read by an unsupported path. Apply the same rule to dependent post-process surfaces as a group after proving each required edge. Do not infer eligibility from graph reachability through arbitrary sampled textures.
- Keep asset/ordinary guest textures, shadow and utility images, and unsupported or ambiguous surfaces Native even with the same dimensions as an eligible surface. Preserve DMA location, guest address and pitch, tile layout, sample-grid size, and serial/dirty tracking. The planned fixture covers a same-size shadow, unrelated scanout, memory texture, orphan scene, and a conflicting route on shared depth.

## Coordinate and lifetime boundaries

| Path | Rules |
| --- | --- |
| Surface resolution in `rsx_draw_engine.c` | `rsx_dsp_get_surface` expands clip coordinates into guest sample coordinates. `eng_surface_get`, `eng_current_target_set`, and `eng_zdepth_get` resolve guest allocations and pitches. Keep that geometry for lookup, tile addressing, and materialization. Store actual host image extent and scale alongside the allocation. Compatible MRT and depth attachments must cover the same host render area. |
| Draws and clears in the Vulkan backend | `sink_draw` builds constants and sets viewport/scissor in guest sample units. Convert at the host raster boundary using the bound attachment's scale. Map shared edges consistently, intersect with the host extent, and handle partial clears in the same coordinates. Audit generated shader viewport constants, fragment position, derivatives, unnormalized coordinates, and pixel offsets separately from normalized UVs. Preserve depth range, stencil, depth-bias behavior, and native shadow filtering. |
| Texture consumers and cache | `sink_bind_textures` distinguishes compressed guest textures, color aliases, feedback snapshots, and a newer depth producer by serial. Keep those comparisons in guest space. `vk_eng_surface_view` remaps actual image views. Track host extents/scale in attachment/framebuffer reuse and in color/depth snapshots, view recreation, and retirement so an old image cannot back a new allocation. Convert texel-sensitive sampling and resolve aliases in the image's own coordinates. |
| GPU resolve and partial writes | `nv3089_gpu.inc` and `gpu_blit_engine.inc` find the source and destination via independent guest address/pitch and clip rectangles, then update destination serial, guest dirty state, and overlapping texture caches. `vulkan_surface_blit.inc` performs exact integer point gathers with in-flight per-slot scratch ranges. Native must keep that exact result. At higher scales map both rectangles independently, preserve shared tile boundaries, and use explicit sample/phase rules. Check scratch byte limits and resize or split the work if necessary. Do not route a normal scaled present through a full guest-memory round trip. |
| Color/depth bytes | `rsx_draw_engine_guest_wrote`, `eng_surface_to_guest`, and `vk_eng_readback` cross between copies in guest bytes and Vulkan image rows. Materialize to guest size and pitch before a guest observer sees the range, and apply partial writes only to their mapped host region. `depth_guest_read.inc` compares contained byte ranges and color/depth serials. Keep latest-writer selection and guest packing intact. `vulkan_depth16.inc` gathers native D32 depth into R16 and caps the conversion at 2048×2048. Keep the observed shadow conversion Native. Audit other scaled depth aliases and require guest-size materialization or Native fallback before supporting them. |
| Presentation | `eng_present_surface` selects a registered display range, and `tb_scanout_surface` imports a completed transfer-only buffer when needed. The presenter copies a complete scaled resolve to its own image before the next guest frame can overwrite the display buffer. FXAA uses that image's texel size, the window fits it to the drawable, and host overlays use swapchain dimensions. |

## Expected host extents

Settings, measurement tools and renderer geometry accept all six presets.
The 50% and 75% paths can now allocate and map reduced scene attachments.
Single-pass scene ownership is implemented. Downscale resolve validation and
gameplay performance acceptance remain pending.
Saving or logging a lower preset is not evidence of a smaller GPU scene image.

Percentages scale both axes. The host allocations preserve the observed guest sample grid. The guest continues to register a 1280×720 display at every setting.

| Preset | Resolved scene output | Main color and zeta backing | Shadow |
| --- | --- | --- | --- |
| 50% | 640x360 | 1280x720 | 2048x2048 |
| 75% | 960x540 | 1920x1080 | 2048x2048 |
| Native (100%) | 1280×720 | 2560×1440 | 2048×2048 |
| 150% | 1920×1080 | 3840×2160 | 2048×2048 |
| 200% | 2560×1440 | 5120×2880 | 2048×2048 |
| 300% | 3840×2160 | 7680×4320 | 2048×2048 |

A 300% backing image at four bytes per sample takes about 127 MiB per attachment before alignment, depth/stencil overhead, or temporary copies. Validate image and framebuffer dimensions, memory, and transfer scratch for the complete group before activating a preset. The first implementation should retain Native if those limits or the selected route cannot be satisfied. Use the Native references to gate behavior before comparing higher-resolution pre-present images. A larger final image alone cannot verify extra scene detail.

The allocation step reserves a complete Vulkan set once the render thread observes a full point resolve for every registered display buffer. `rsx_render_scale.inc` ties the proof to the current guest surface generations and `vulkan_render_scale.inc` stages compatible color, depth, resolve, and framebuffer resources. The group is discarded after a conflicting write or layout change. A failed reservation releases its temporary images and reports Native fallback.

A proven group supplies the scaled scene attachments for a frame. The renderer reuses the guest geometry, textures, and pipeline state for eligible scene and HUD draws, then maps the viewport, scissor, and clear rectangles to the bound Vulkan attachment. Scissors use shared half-open edges, with clipping applied before conversion. Native shadow and utility surfaces retain their own dimensions. Color feedback snapshots the authoritative image; unsupported depth/vertex aliases and geometry establish Native continuation.

The fragment shader maps WPOS back to the guest sample grid before applying the guest origin and pixel-center rules. Ordinary texture operations retain their normalized UVs and implicit LOD, and unnormalized samplers retain the dimensions from their guest texture descriptors. The generated shadow taps use the dimensions of the Native shadow image. The sampled depth-bounds fallback reads framebuffer pixel coordinates directly, so a frame that uses it cannot use the scaled draw. Dynamic depth bounds, stencil, and the depth range use the original state. Polygon offset retains its constant depth term. For equal X/Y host-to-guest ratios, its slope factor is multiplied by that actual ratio; a nonzero slope with unequal rounded ratios rejects the scaled frame.

The renderer emits each eligible draw and clear once into the selected-resolution attachments. Native color/depth handles retain guest identity but their contents can be stale. A complete proven resource group is required before routing begins; its initial contents are imported once from Native. Color aliases use the current authoritative view, and feedback takes an ordered immutable snapshot. Native NV3089 still selects exact point samples at 100%. A recognized non-Native resolve writes the selected-size display backing directly; filtering and tile-phase acceptance at 50%/75% remain Step 5 work. Guest uploads patch only their mapped rectangle after the staged upload completes. Other point transfers reconstruct the source and establish Native continuation before mutating an authoritative destination.

Draw groups retain immutable vertex, index, constant and descriptor slices until their slot's fence completes. The mirror queue and replay pass have been removed. Attachment changes end the current render pass within the batch, and a texture consumer of the preceding attachment completes its producer before drawing. The existing 64-draw bound, four submission slots and retirement fences still govern reuse.

The registered display route publishes scaled scanout after all six tiles complete. While the route is unavailable, the presenter uses its Native scanout. The scaled presentation image has its own lifetime and effective dimensions, and capture reports those dimensions before optional FXAA. Ordinary presentation reads only one diagnostic pixel back to the CPU. Friends, invitations, and FPS are composed in swapchain coordinates after the window blit.

Use `scripts/capture_rendering.py --build-dir build --output analysis/rendering/<run> --capture --rsx-trace --render-scale 150` to capture the resolved output. Traced frames also produce a pre-resolve `*-scaled.ppm` in the local RSX trace directory. The Vulkan fixtures compare Native and scaled raster detail through scanout, exercise fractional clear boundaries, WPOS, depth and stencil, check pitched partial copies and overlapping aliases, and verify FXAA at both output sizes.

## Bidirectional geometry (lower-resolution Step 3)

Guest surface identity retains location, address, pitch, format, dimensions,
sample grid and generation. Host color, depth and display extents are stored
separately and use `ceil(guest * percent / 100)` on each axis. Framebuffer reuse
checks the scale and matching guest/host extents, including when a Native image
happens to have the same host dimensions. Views and snapshots carry the source
extent metadata; a generation change replaces the resource group.

Integer rectangles map both half-open edges with `floor(edge * host / guest)`.
Scissors clip in guest space before mapping, using subtraction to avoid
overflow. Adjacent partial clears and all six clipped NV3089 tile rectangles
share edges. A valid rectangle may collapse below 100%: empty clears and
destination blits do nothing, and an empty scissor returns before reserving
descriptors, vertex slices or draw slots. A nonempty destination with an empty
source requires reconstruction and is rejected by the current blit path.
Floating viewport positions and sizes retain their fractions and signed
origins, using the actual ratios and cached Vulkan viewport limits.

`fp_scale.xy` stores independent guest/host ratios, with zero selecting identity
for the Native draw. It precedes the SDK's sample-grid division in WPOS and
leaves Z/W, window origin and pixel-center bias intact. The constant layout is
guest constants, `fp_alpha`, optional `fp_dbounds`, then `fp_scale`; CPU and GPU
buffers reserve all three extra vectors. Shader cache identity distinguishes
this layout. Native draws keep identity conversion, including when sharing a
shader with a scaled mirror.

Normalized UVs, explicit texture offsets and gradient expressions remain in
the texture's coordinates. Implicit derivatives/LOD follow the selected host
raster density; they are not multiplied by a second nominal scale. Unnormalized
sampling still divides by guest texture dimensions. Shadow images and their
texel offsets stay Native. Color feedback uses an authoritative snapshot.
Sampled depth-bounds and vertex aliases require Native continuation.
Dynamic depth bounds, stencil references/operations and constant depth bias do
not depend on image extent. Vulkan has one slope factor, so unequal rounded
axis ratios cannot preserve every primitive's maximum depth derivative with a
nonzero slope. That case uses the existing coherent Native result and increments
`scale_skip_geometry`; valid empty draws increment `scale_empty_draw` instead.

Windows validation on the Iris Pro 580 passes the CPU geometry, production
shader translation, CPU smoke and frame-budget CTest checks. The focused
`vulkan_render_scale --geometry-only` fixture passes all presets, covering odd
extents, partial clears, empty transfers/draws, WPOS/window conventions, signed
fractional viewports, slope fallback, incompatible attachments and allocation
rollback. Fractional viewport tests assert exact submitted state and compare
coverage with a Native attachment using that host viewport; this driver also
omits the analytic edge column in the Native control. The full fixture still
fails its previously recorded depth/stencil pixel check at `(6,3)`, and
`vulkan_draw_groups` still fails its presented-center check. These remain open
acceptance gates. Linux execution and gameplay validation are pending.

## Authoritative ownership (lower-resolution Step 4)

Each image in a proven generation has a stable Native handle, a selected-size
host handle, a content serial and a completed Native serial. The owner is Native
before activation and at 100%; after activation the selected-size image owns
the contents. A zero Native handle means no current Native association, including
a generation that has been retired.
Scaled serials can name ordered batch work; a Native serial becomes valid only
after reconstruction completes. Initialization and read/write barriers use the
existing slot fences. No second scene draw or retained mirror packet is needed.

| Operation | Ownership behavior |
| --- | --- |
| Eligible scene/HUD draw or clear | Select color/depth once; update the authoritative content serial. Shadow and utility targets retain their independent Native path. |
| Color alias / feedback | View the authoritative image; snapshot feedback before later writes. Views/snapshots retain actual extent metadata and retire with their source generation. |
| Guest-sized color/depth observer | Reconstruct a full valid Native region once per content serial. Color readback bytes also cache by engine write serial for tracked images (an explicit `TB_COLOR_READBACK_CACHE=0` disables the byte cache). |
| Partial color upload | Upload the changed Native rectangle, finish its staging operation, then patch only the mapped authoritative rectangle. Untouched Native pixels remain stale; the changed rectangle does not validate the whole Native image. |
| Byte-column NV0039 transfer | Resolve the surface pitch separately from the transfer's byte stride. Preserve partial destination pixels before writing and notify the exact aligned pixel range afterward. |
| Guest Z24S8 write | Reconstruct the current frame, retain Native for subsequent frames, then upload the changed depth and stencil region. Preserve untouched color/depth/stencil and advance the engine write serial. |
| Unsupported attachment, alias, viewport, slope or route change | Reconstruct current attachments before Native continuation; invalidate scaled publication and retain Native until relaunch. Recovery failures stop rendering rather than expose stale contents. |
| Generation retirement | Finish queued consumers, preserve the other current attachments, then release the old images/views. A new handle cannot inherit old generation ownership. |

Reconstruction has an explicit sampling phase: destination pixel `(x,y)` selects
`floor((x+0.5)*source_width/destination_width)` and the corresponding Y value.
RGBA8 bytes, D32 values and stencil bytes use this same center-point nearest
rule. The waited staging implementation avoids driver-dependent nearest-blit
ties and devices without depth/stencil blits. It runs for initialization,
observers and recovery, rather than routine scene rendering. Guest color rows
retain the SDK endian/tiling conversion; guest depth export retains its Z16 or
Z24 quantization and zero stencil-byte ABI. Stencil is preserved independently
in GPU continuation and explicit guest Z24S8 writes. Non-Native reconstruction
does not promise the raster values an original-resolution draw would have made.

Recognized resolves update selected-size display backing without a routine
Native gather. Unhandled transfers and presentation fallback reconstruct a
current Native image when needed. The presenter still copies completed output
into its own storage before reuse. Downscale filtering, full tile publication,
FXAA and presentation acceptance remain Step 5 work.

`scale_materialize` counts completed Native reconstructions; `scale_recovery`
counts transitions to Native continuation and logs their reason. Existing
logical and physical draw counters remain distinct. The production FIFO-engine
fixture checks 145 eligible draws per preset, one clear per operation, unknown
routes, utility draws, repeated byte reads, byte writes and depth writes. The
GPU `vulkan_render_scale --ownership-only` fixture checks physical emissions,
stale Native poison, exact reconstruction phase, cached Native copies, partial
writes, feedback sampling, depth/stencil recovery and generation retirement.

A newly linked Windows player (`a4e7a4fea3fd07e23519d9cea8f6b7d70fc8768b62ad2c5bc85d3f5fbf8ffa7c`)
was checked in short Battle diagnostics on the NUC using the current runtime.
Scene emissions match logical scene candidates within trace-window boundaries;
Native emissions match the independent depth-only work.

| Requested/effective scale | Actual present source | Logical scene draws/frame | Scaled engine draws/frame |
| --- | --- | --- | --- |
| 50% / 50% | 640x360 | 314.25 | 314.25 |
| 75% / 75% | 960x540 | 312.24 | 312.24 |
| 150% / 150% | 1920x1080 | 305.47 | 305.48 |

All three captured intervals recorded zero scale recoveries, Native fallback
presents and routine materializations, and their player processes exited with
code 0. Boundary captures were visually inspected. These are 15-second
instrumented, pending-review diagnostics, with different scene workloads;
they are not timing comparisons or 60 FPS acceptance. The 150% run also failed
the overlay-consistency gate, so its timing report was rejected. The first 50%
attempt timed out at map OCR and hit a shutdown access violation; it was rejected
before live gameplay. Map recognition was corrected and its rerun completed.
The full fixture's existing depth/stencil pixel and presented-center failures
remain open. Local source/artifact hashes, original logs and captures are under
`analysis/performance/resolution-step4/`.
