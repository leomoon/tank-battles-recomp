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

The renderer emits each eligible draw and clear once into the selected-resolution attachments. Native color/depth handles retain guest identity but their contents can be stale. A complete proven resource group is required before routing begins; its initial contents are imported once from Native. Color aliases use the current authoritative view, and feedback takes an ordered immutable snapshot. Native NV3089 still selects exact point samples at 100%. A recognized non-Native resolve writes the selected-size display backing directly; Step 5 validates filtering and tile phase at 50%/75%. Guest uploads patch only their mapped rectangle after the staged upload completes. Other point transfers reconstruct the source and establish Native continuation before mutating an authoritative destination.

Draw groups retain immutable vertex, index, constant and descriptor slices until their slot's fence completes. The mirror queue and replay pass have been removed. Attachment changes end the current render pass within the batch, and a texture consumer of the preceding attachment completes its producer before drawing. The existing 64-draw bound, four submission slots and retirement fences still govern reuse.

The registered display route publishes scaled scanout after all six tiles complete. While the route is unavailable, the presenter uses its Native scanout. The scaled presentation image has its own lifetime and effective dimensions, and capture reports those dimensions before optional FXAA. Ordinary presentation performs no pixel readback; an explicit center-pixel probe reads four bytes after the presentation fence. Friends, invitations, and FPS are composed in swapchain coordinates after the window blit.

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
`vulkan_draw_groups` failed its presented-center check at this stage. Step 5
fixes that presentation check; the depth/stencil gate remains open. Linux
execution and broader gameplay validation are pending.

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
current Native image when needed. The presenter copies completed output
into its own storage before reuse. The following Step 5 work completes the
focused resolve/presentation checks.

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
The full fixture's existing depth/stencil pixel failure remains open; Step 5
fixes the separate presented-center failure. Local source/artifact hashes, original logs and captures are under
`analysis/performance/resolution-step4/`.

## Resolve and presentation (lower-resolution Step 5)

The six observed NV3089 tiles resolve directly from the selected scene image
into the selected display image. At Native the exact second-texel point gather
is unchanged. Every other preset uses linear filtering at the centre of each
physical 2x2 footprint, averaging its four texels. The title's tile boundaries,
including the clipped right and bottom tiles, remain integral at all six
presets. They therefore use the same global sampling phase without gaps,
overlap or independent edge clamping. This policy applies to the selected
raster image; it does not downsample a Native raster pass.

The engine requires six distinct tiles from one unchanged producer serial and
the same surface generations. The backend also verifies its source serial
through the resolve and publishes the completed destination serial. Incomplete
or interrupted resolves cannot become selected scanout images. A legitimate
guest display patch advances a published serial while retaining untouched
pixels. Display-buffer rotation keeps independent images and publication state.

Presentation copies the completed image into an owned image before the guest
can reuse its display buffer. The window path fits that image to the drawable,
preserving aspect ratio; reduced images are enlarged once on the GPU. Ordinary
presentation performs no pixel readback and allocates no host readback buffer.
An explicit centre-pixel diagnostic waits for the present copy and transfers
four bytes on demand. Dumps and frame history still opt into full selected-size
readback. RSX diagnostics save both `*-scaled.ppm` scene backing and the completed
`*-scaled-resolve.ppm` image before window scaling or FXAA.

Native handles retain their guest identities, dimensions and generations, but
release their physical images after selected backing owns their contents.
Native views are recreated if a later observer requires reconstruction. Partial
uploads use temporary Native storage, update only the mapped selected region,
then retire that storage. Initial-import staging is released too. The unused
fixed-function framebuffer, color/depth images and full-size readback buffer
are retired when the register-file engine establishes owned presentation.
Native proof frames, initialization and explicit observers still have a
temporary allocation cost; the steady selected path retains no duplicate
Native scene attachments. Recovery reconstructs current contents before Native
continuation.

Equal-size transfer rectangles use exact GPU copies. Overlapping transfers
snapshot their source first. On the Iris 580 Windows driver, barriers alone
lost texels between the two snapshot copies; a completed snapshot submission
fixes this rare overlap path. Ordinary tiled resolves remain batched.

The full-size GPU fixture measures the following on the NUC. Attachment totals
use Vulkan image memory requirements for the three scene/display pairs and
shared depth, plus the fixture's pre-existing engine images. The Native control
is 76,132,352 bytes. Peak figures track actual successful fixture-owned Vulkan
allocations, including initialization, staging and presentation; they exclude
pre-existing renderer allocations and are not whole-process memory usage.

| Scale | Steady attachment bytes | Transient peak bytes | Resolve output / owned copy, bytes each per frame |
| --- | ---: | ---: | ---: |
| 50% | 19,591,168 | 99,917,824 | 921,600 |
| 75% | 42,954,752 | 119,087,104 | 2,073,600 |
| Native | 76,132,352 | Native proof/control | 3,686,400 |
| 150% | 171,646,976 | 281,333,760 | 8,294,400 |
| 200% | 302,796,800 | 446,038,016 | 14,745,600 |
| 300% | 682,409,984 | 908,656,640 | 33,177,600 |

Transfer columns count destination payload once per operation, rather than
physical memory-bus traffic. `scale_resolve_bytes` and `present_copy_bytes`
report these independently. `present_readback_bytes` is zero in ordinary
presentation. `alloc_memory_bytes` records allocation request volume, including
failed attempts; `scale_retired_bytes` records retired Native image requirements.
These counters complement actual fixture peak measurements and raster counts.

FXAA remains optional and off in initial scale diagnostics. Its shader derives
texel size from the actual filtered image. The draw-group fixture tests off/on
at 640x360, 960x540, 1280x720, 1920x1080, 2560x1440 and 3840x2160, preserving
source bytes and alpha while smoothing the diagonal test edge. Its GPU work is
reported separately as `fxaa` timestamp spans and `fxaa_texels`. Cold single
dispatch measurements on this NUC were 0.181 ms at 50%, 0.348 ms at 75%,
0.607 ms at Native, 1.320 ms at 150%, 2.361 ms at 200% and 5.952 ms at 300%.
These are diagnostic costs, not sustained gameplay results.

`vulkan_render_scale --resolve-only` checks every output pixel across all six
tiles, three rotating display buffers, partial patches, source changes,
incomplete publication, overlap snapshots, aspect-ratio fitting, present-source
reuse, allocation retirement and explicit full captures at every preset.
It passes with async and `TB_VK_ASYNC=0` submission on the NUC, as do ownership
and draw-group fixtures. Geometry, depth16 and NV3089 fixtures also pass. Broad
integration, Linux GPU execution and sustained 60 GAME FPS remain Steps 6-7.

The fresh Step 5 Windows player captured genuine 640x360 and 960x540 resolve
images in Battle, with corresponding 1280x720 and 1920x1080 scene backing.
The 75% resource diagnostic passed strict execution, visibility and overlay
checks and exited with code 0. Initial 50% resource attempts are retained as
rejected for overlay/focus checks; their captures are visual evidence only.
A separate 30-second 50% diagnostic without heavy image capture passed those
checks and exited normally at 29.7 completed GAME FPS. It recorded about
9.9 ms GPU work per frame and 22.3 ms on the main guest thread outside traced
blocking (wall time, rather than OS CPU time). Its CPU samples identify guest
execution and indirect dispatch as the remaining work. Lower pixel cost has
not met the NUC's 60 FPS objective. A separate full title rebuild with
`TB_TITLE_ARCH=x86-64-v3` and `TB_TITLE_RESTRICT_CONTEXT=ON` measured 29.83
completed GAME FPS in a matching 30-second diagnostic, with main guest time
about 21.79 ms/frame and GPU time about 10.15 ms/frame. Strict execution,
visibility, overlay consistency and normal exit passed; boundary OCR required
pending review. This single experiment establishes no sustained improvement,
and the compiler flags remain off by default. The renderer result is not a
performance acceptance claim. The active Windows Balanced policy allows 100%
maximum processor state and aggressive boost; these read-only settings checks
do not establish actual in-game clocks or thermal headroom.

The current trace's `scene_candidate_extents` contains both logical guest
candidate dimensions and selected backing dimensions. The requested/effective
scale, present-source extent, physical draw counts and retained image headers
establish the active path. The logical candidate entry alone is not a fallback
event. Source hashes, the frozen CPU baseline, captures and original reports
are retained locally under `analysis/performance/resolution-step5/`.


## Correctness and integration (lower-resolution Step 6)

The full Vulkan render-scale fixture now passes on the Iris Pro 580. Its old
`target 120 pixel 6,3` failure was in a pitched guest upload, before the
subsequent depth/stencil assertions: an unassociated Native upload could still
be queued when the patch sampled it. The patch now completes the upload before
sampling, just like the authoritative ownership path, and advances the destination
content serial while preserving a published display's validity.

Further checks exposed a diagnostic capture failure. Native fallback and resized
presentation images were correct on the GPU, but this driver returned stale
pixels when the presentation image copy and full-image readback shared one
command buffer. Explicit captures now run after the presentation fence in their
own submission. Readback storage is allocated lazily for both the fixed and owned
presentation paths, and the submitted frame retains its capture policy if the
environment changes before completion. Ordinary presentation still performs no
pixel readback. Equal-size presentation copies use `vkCmdCopyImage`.

`tests/vulkan_scale_raster.inc` adds exact depth LESS, stencil-reference and
partial depth/stencil-clear checks at 50/75/100/150/200/300%. Existing geometry,
ownership, resolve and draw-group fixtures cover WPOS, odd extents, fractional
viewports, partial writes and pitch, stale Native representations, cached guest
reads, immutable feedback, unsupported-operation recovery, generation reuse,
physical draw counts, all six resolve tiles, display rotation and FXAA. Full
capture checks compare every pixel and exercise capture-policy changes while a
presentation is in flight. Native fallback is tested through the engine-owned
presentation path used by the player.

Configure a runtime build with `-DTB_TEST_VULKAN=ON` to register the actual GPU
fixtures with CTest, then run `ctest --test-dir BUILD -C Release -L gpu
--output-on-failure`. The 16 cases run serially to avoid overlapping GPU tests;
each fixture runs with asynchronous submissions and with `TB_VK_ASYNC=0`, both
retaining grouped draws. Hosted machines without a GPU leave this option off.

On this Windows host, all 16 GPU cases plus ten trophy, display/settings, geometry,
submission, shader, CPU-smoke and frame-budget checks pass (26 total). The new
runtime was packed and verified, then linked into the player while reusing 77
unchanged translated objects. Current player SHA-256:
`2cfe75f8d221abe655b9071ae5e1d60cf1bfec1d743fcdd6964d4e2f51cee4df`.
The builder resource tree also passes file-hash, allowlist and runtime ABI
verification. A separate synthetic player and launcher compiled from those
assembled resources using local MSVC ClangCL passed installed-file hashes,
all seven launcher screens, save/cancel/defaults, keyboard-selected 50%,
controller-selected 75%, and relaunch at both reduced scales with the same
executable. This verifies the local consumer path; it does not validate a
downloaded standalone builder or its bundled Zig toolchain. Packaging tests
now avoid requiring Windows symlink privilege for POSIX-only AppRun fixtures.

The fresh 75% Battle route completed menus, map selection, briefing, gameplay,
pause/resume, 960x540/1024x768/1280x720 window sizes, a 2560x1440 borderless
round trip, and debriefing. Strict execution, visibility, overlay consistency,
renderer checks and normal exit passed. Reviewed captures preserve HUD/menu
content and 16:9 letterboxing at 1024x768. Its short timing interval remains
pending boundary OCR review and is not performance acceptance. The earlier
transition attempt timed out and remains rejected. A separate 50% Battle run
exited normally with strict execution and reviewed live gameplay, but failed
countdown OCR; retain its images as visual evidence only.

The 50% Campaign route also passes gameplay, pause/resume, the same resize and
borderless checks, strict execution, visibility, renderer checks and normal
exit. Its initial attempt stopped at briefing because reduced-resolution OCR
lost the stylized CONTROLS heading. The route now recognizes the first mission's
objective and Continue together, with negative cases, and uses the same cue to
reject briefing screens as gameplay boundaries. The failed navigation attempt
is retained separately. The 15-second Campaign diagnostic completed 43.4 GAME
FPS; the different workload and short interval do not establish a speedup over
Battle or sustained performance acceptance.

Evidence, exact source/artifact hashes and original logs are retained under
`analysis/performance/resolution-step6/`.

Closing the 75% Campaign window exposed the earlier Intel driver access
violation again. The walker had returned immediately after requesting quit,
while the presenter could still use its native window. Windows releases windows
owned by a terminating thread ([thread termination semantics](https://learn.microsoft.com/en-us/windows/win32/procthread/terminating-a-thread)).
The Vulkan owner now stays alive during the quit grace period, waits for the
present worker to stop, then completes renderer/swapchain/window cleanup on
its owner thread. The present worker also checks the stop flag after waking.

Allowing guest cleanup to progress exposed a missing `sceNpTrophyAbortHandle`
import. The port now registers that call against the trophy module's real
handle state. Aborting marks a handle unusable until destruction/recreation;
later operations return ABORT, and an abort during the installed callback
prevents registration completion from being queued. This follows the permanent
handle-abort model in [RPCS3's trophy implementation](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Emu/Cell/Modules/sceNpTrophy.cpp).
The CPU fixture covers initialization, invalid handles, repeated abort, later
operations, callback abort and handle reuse. Strict failure reporting remains
enabled. The new fixture is included in bundle builds and the game-free tool
allowlist.

On the final player, active-window closes at 50%, 75% and Native all pass with
strict execution, exit code 0, owner-thread renderer cleanup and all render
workers joined. These close probes deliberately have no gameplay timing marker;
their parent measurement harness rejects them as benchmarks, while their child
session records establish successful shutdown. The previously crashing 75%
Campaign route also now exits normally with strict execution and no renderer
failure. Earlier driver-crash, missing-HLE and OCR-rejected attempts remain
rejected in the retained evidence. All 56 Python cases pass with two platform
skips; 31 affected cases were rechecked after the cleanup changes.

The final 50% Battle route also completes pause/resume, three window sizes,
a borderless round trip, debriefing and normal shutdown with strict execution
and no renderer failures. The final local resource tree verifies, and the
synthetic consumer relinked against the final runtime passes launcher
save/relaunch checks again at 50% and 75%.

Windows gameplay coverage includes Battle and Campaign at both reduced presets;
window transitions are covered at both presets. The earlier successful 75%
Battle and 50% Campaign transition runs used the initial Step 6 player; final
shutdown fixes were checked in 50% Battle, 75% Campaign and all three close
probes. Step 6 remains open for Linux gameplay/window validation and a newly
built/downloaded standalone builder. Linux execution is unavailable
on this Windows host (WSL is not installed). These correctness checks do not
establish the sustained 60 GAME FPS performance target.
