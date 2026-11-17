cmake_minimum_required(VERSION 3.20)
project(TankBattlesRecomp
    VERSION 0.1.0
    LANGUAGES C CXX
    DESCRIPTION "Tank Battles PS3 static recompilation port"
)

# ---------------------------------------------------------------------------
# Standards
# ---------------------------------------------------------------------------
set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Prefer Ninja if available
if(NOT CMAKE_GENERATOR MATCHES "Ninja" AND NOT DEFINED ENV{CMAKE_GENERATOR})
    message(STATUS "Tip: use -G Ninja for faster builds")
endif()

# ---------------------------------------------------------------------------
# The two directories this project is built from
# ---------------------------------------------------------------------------
# PS3RECOMP_DIR is the toolkit checkout: include/, runtime/, libs/, tools/.
# RECOMP_DIR is where the lifter's output for THIS game lives. They are
# separate on purpose -- a port keeps its generated code in its own tree and
# points at whatever toolkit checkout it is building against:
#
#   cmake -B build -G Ninja \
#         -DPS3RECOMP_DIR=/path/to/ps3recomp \
#         -DRECOMP_DIR=/path/to/my_game/recompiled
#
set(PS3RECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/external/ps3recomp" CACHE PATH
    "ps3recomp source directory (defaults to the vendored SDK)")
get_filename_component(PS3RECOMP_DIR "${PS3RECOMP_DIR}" ABSOLUTE)
if(NOT EXISTS "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp")
    message(FATAL_ERROR "Vendored ps3recomp source is missing. Restore external/ps3recomp from this repository, or set -DPS3RECOMP_DIR=/path/to/ps3recomp.")
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    option(PS3RECOMP_RSX_VULKAN "Build the Linux Vulkan renderer" ON)
endif()

set(RECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/recompiled" CACHE PATH
    "Directory holding the lifter's output: ppu_recomp.h and the lifted sources")
get_filename_component(RECOMP_DIR "${RECOMP_DIR}" ABSOLUTE)

message(STATUS "ps3recomp SDK:   ${PS3RECOMP_DIR}")
message(STATUS "Lifted output:   ${RECOMP_DIR}")

if(NOT EXISTS "${RECOMP_DIR}/ppu_recomp.h")
    message(FATAL_ERROR
        "No ppu_recomp.h in ${RECOMP_DIR}.\n"
        "That header and the lifted sources beside it are what "
        "tools/ppu_lifter.py writes. Run python3 scripts/port.py prepare, "
        "or point -DRECOMP_DIR= at wherever it already is.")
endif()

# The lifted translation units. A game's lift is hundreds of generated files,
# so they are globbed rather than listed; re-run CMake after a fresh lift.
file(GLOB RECOMP_SOURCES CONFIGURE_DEPENDS
    "${RECOMP_DIR}/*.c"
    "${RECOMP_DIR}/*.cpp"
)
if(NOT RECOMP_SOURCES)
    message(FATAL_ERROR
        "${RECOMP_DIR} has a ppu_recomp.h but no .c or .cpp beside it. The "
        "lifted functions and the function table live in those; without them "
        "there is nothing to run.")
endif()
list(LENGTH RECOMP_SOURCES RECOMP_SOURCE_COUNT)
message(STATUS "Lifted sources:  ${RECOMP_SOURCE_COUNT} file(s)")

# ---------------------------------------------------------------------------
# ps3recomp runtime library
# ---------------------------------------------------------------------------
# Built from source as a subdirectory rather than imported as a prebuilt
# archive. A static library carries none of its own link requirements, so an
# imported one arrives without SDL2, pthreads, the Metal frameworks or the
# Winsock/XInput imports -- the link then fails on every platform for a
# different reason. add_subdirectory hands all of that over with the target.
add_subdirectory("${PS3RECOMP_DIR}" "${CMAKE_BINARY_DIR}/ps3recomp_sdk"
                 EXCLUDE_FROM_ALL)

# ---------------------------------------------------------------------------
# The HLE NID table
# ---------------------------------------------------------------------------
# Maps each firmware import NID onto the handler in the runtime that answers
# it. A port may keep a hand-curated one beside its lift; otherwise generate
# the canonical every-module table, which is what the toolkit's own boot
# harness uses.
if(EXISTS "${RECOMP_DIR}/ppu_hle_nids.cpp")
    set(HLE_NID_TABLE "${RECOMP_DIR}/ppu_hle_nids.cpp")
    message(STATUS "HLE NID table:   ${HLE_NID_TABLE}")
else()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(HLE_NID_TABLE "${CMAKE_CURRENT_BINARY_DIR}/ppu_hle_nids.cpp")
    # The generator discovers handlers from library definitions. Rebuild when
    # an existing module changes, and reconfigure when a new module is added.
    file(GLOB_RECURSE HLE_NID_SOURCES CONFIGURE_DEPENDS
        "${PS3RECOMP_DIR}/libs/*.c")
    # A removed input cannot make the output older than a remaining dependency.
    # Track the list itself so deletions also remove stale handler references.
    set(HLE_NID_SOURCE_LIST "${CMAKE_CURRENT_BINARY_DIR}/hle_nid_sources.txt")
    file(GENERATE OUTPUT "${HLE_NID_SOURCE_LIST}" CONTENT "${HLE_NID_SOURCES}\n")
    add_custom_command(
        OUTPUT "${HLE_NID_TABLE}"
        COMMAND "${Python3_EXECUTABLE}"
                "${PS3RECOMP_DIR}/tools/gen_hle_nids.py"
                --out "${HLE_NID_TABLE}" --all
        DEPENDS "${PS3RECOMP_DIR}/tools/gen_hle_nids.py"
                "${PS3RECOMP_DIR}/tools/nid_database.py"
                ${HLE_NID_SOURCES} "${HLE_NID_SOURCE_LIST}"
        COMMENT "Generating the HLE NID -> handler table")
    message(STATUS "HLE NID table:   generated (every module)")
endif()

# ---------------------------------------------------------------------------
# The game executable
# ---------------------------------------------------------------------------
# The PPU boot scaffold is compiled here rather than taken from the runtime
# library: every one of these files #includes the lifter's per-game
# ppu_recomp.h, so they can only be built against a particular lift.
set(PS3RECOMP_SCAFFOLD
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_hle.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs.cpp"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_sysprx.cpp"
)

# ---------------------------------------------------------------------------
# Lifted SPU images (optional)
# ---------------------------------------------------------------------------
# A title that runs SPU work gets its images lifted into <SPU_DIR>/<image>/ by
# tools/spu_lifter.py, with a registration TU generated by
# tools/build_spu_workloads.py. cellSpurs dispatches them by content
# fingerprint, so the generated table is keyed to the images THIS build ships:
# a table generated from another release registers signatures that can never
# match, which is worse than shipping none. Both the table and the job-module
# registry self-register from constructors and are therefore optional -- a tree
# that has not lifted its SPU images yet configures and links without them.
set(SPU_DIR "${CMAKE_CURRENT_SOURCE_DIR}/spu" CACHE PATH
    "Directory holding the lifted SPU images (<SPU_DIR>/<image>/spu_recomp.c)")
option(PORT_ENABLE_SPU "Compile and register the title's lifted SPU images" ON)

set(PORT_SPU_INPUTS "" CACHE STRING
    "Input files to scan for all embedded SPU ELF programs (semicolon-separated decrypted ELF/PRX/data paths)")
set(SPU_SOURCES "")
if(PORT_ENABLE_SPU AND PORT_SPU_INPUTS)
    include("${PS3RECOMP_DIR}/cmake/PS3RecompSPU.cmake")
    ps3recomp_generate_spu("${CMAKE_CURRENT_BINARY_DIR}/generated_spu" ${PORT_SPU_INPUTS})
    list(APPEND SPU_SOURCES ${SPU_GENERATED_SOURCES}
         "${PS3RECOMP_DIR}/runtime/spu/spu_dispatch_mt.c")
elseif(PORT_ENABLE_SPU)
    file(GLOB SPU_LIFTED CONFIGURE_DEPENDS "${SPU_DIR}/*/spu_recomp.c")
    if(SPU_LIFTED)
        list(LENGTH SPU_LIFTED SPU_IMAGE_COUNT)
        message(STATUS "Lifted SPU:      ${SPU_IMAGE_COUNT} image(s)")
        # The musttail indirect-branch fast path. Excluded from the runtime
        # library on purpose (see the SDK root CMakeLists): it must be built
        # here, by clang, or the tail call degrades to a plain call and a guest
        # loop that branches indirectly walks the thread stack off its guard
        # page a few thousand iterations in.
        list(APPEND SPU_SOURCES ${SPU_LIFTED}
             "${PS3RECOMP_DIR}/runtime/spu/spu_dispatch_mt.c")
        # Generated beside the lift; each is present only once its generator
        # has been run, so each is included only if it is actually here.
        foreach(gen IN ITEMS spu_workloads.c jobmods_register.c taskset_policy_bytes.c)
            if(EXISTS "${SPU_DIR}/${gen}")
                list(APPEND SPU_SOURCES "${SPU_DIR}/${gen}")
            else()
                message(STATUS "  (no ${gen} -- not registered)")
            endif()
        endforeach()
    else()
        message(STATUS "Lifted SPU:      none found in ${SPU_DIR}")
    endif()
endif()

# Generate instrumented copies of selected SDK sources; the pinned checkout stays clean.
find_package(Python3 COMPONENTS Interpreter REQUIRED)
set(EXECUTION_SDK "${CMAKE_BINARY_DIR}/instrumented_sdk")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/rsx_memory_copy.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/rsx_texture_coherence.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/depth_path_trace.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/depth_guest_read.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/depth_cost_trace.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/color_readback_cache.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/index_remap_cache.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_depth16.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/shaders/depth16.comp"
    "${CMAKE_CURRENT_SOURCE_DIR}/shaders/depth16_spirv.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/rsx_trace.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/audio_clock.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/fps_vulkan.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/gpu_timing.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_draw_group.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/lifecycle_stats.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/sdl_event_gate.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/player_settings_hooks.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_fence_poll.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_present_mode.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/gcm_label_publication.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_async_batches.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/page_census_arm.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/gpu_blit_engine.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/nv3089_gpu.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/vulkan_surface_blit.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/submission_overlay.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/rsx_scanout.inc"
    "${CMAKE_CURRENT_SOURCE_DIR}/sdk_overrides/edat_cache.inc" "${PS3RECOMP_DIR}/libs/filesystem/edat.c")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrument_sdk.py"
    --sdk "${PS3RECOMP_DIR}" --output "${EXECUTION_SDK}" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrument_sdk.py"
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_hle.cpp" "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp" "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs.cpp"
    "${PS3RECOMP_DIR}/runtime/spu/spu_channels.c" "${PS3RECOMP_DIR}/runtime/spu/spu_interp.c"
    "${PS3RECOMP_DIR}/runtime/spu/spu_raw.c")
foreach(component ppu_hle.cpp ppu_loader.cpp ppu_fs.cpp)
    list(REMOVE_ITEM PS3RECOMP_SCAFFOLD "${PS3RECOMP_DIR}/runtime/ppu/${component}")
    list(APPEND PS3RECOMP_SCAFFOLD "${EXECUTION_SDK}/runtime/ppu/${component}")
endforeach()
list(REMOVE_ITEM SPU_SOURCES "${PS3RECOMP_DIR}/runtime/spu/spu_dispatch_mt.c")
if(PORT_ENABLE_SPU AND SPU_SOURCES)
    list(APPEND SPU_SOURCES "${EXECUTION_SDK}/runtime/spu/spu_dispatch_mt.c")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/runtime/spu/spu_dispatch_mt.c")
if(PS3RECOMP_RSX_VULKAN)
    get_source_file_property(VULKAN_SHADER_DEFINITIONS "${PS3RECOMP_DIR}/libs/video/rsx_shader_spirv.cpp"
        DIRECTORY "${PS3RECOMP_DIR}" COMPILE_DEFINITIONS)
    if(NOT VULKAN_SHADER_DEFINITIONS MATCHES "PS3RECOMP_HAVE_SPIRV_TRANSLATION=1")
        message(FATAL_ERROR "Tank Battles Vulkan requires glslang guest-shader translation; configure the null backend for CPU-only diagnostics.")
    endif()
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/video/rsx_vulkan_backend.c")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/video/rsx_draw_engine.c" "${PS3RECOMP_DIR}/libs/video/rsx_texture_layout.c" "${PS3RECOMP_DIR}/libs/video/cellGcmSys.c")
get_target_property(EXECUTION_RUNTIME_SOURCES ps3recomp_runtime SOURCES)
foreach(component spu_channels.c spu_interp.c spu_raw.c)
    list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)${component}$")
    list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/runtime/spu/${component}")
endforeach()
if(PS3RECOMP_RSX_VULKAN)
    list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)rsx_vulkan_backend.c$")
    list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/video/rsx_vulkan_backend.c")
endif()
foreach(component rsx_draw_engine.c cellGcmSys.c rsx_texture_layout.c)
    list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)${component}$")
    list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/video/${component}")
endforeach()
list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)cellGame.c$")
list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/system/cellGame.c")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/system/cellGame.c")
list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)cellPad.c$")
list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/input/cellPad.c")
list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)cellAudio.c$")
list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/audio/cellAudio.c")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/audio/cellAudio.c")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/libs/input/cellPad.c")
foreach(relative runtime/syscalls/sys_fs.c libs/filesystem/cellFs.c)
    get_filename_component(component "${relative}" NAME)
    list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)${component}$")
    list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/${relative}")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${PS3RECOMP_DIR}/${relative}")
endforeach()
list(FILTER EXECUTION_RUNTIME_SOURCES EXCLUDE REGEX "(^|/)edat.c$")
list(APPEND EXECUTION_RUNTIME_SOURCES "${EXECUTION_SDK}/libs/filesystem/edat.c")
set_property(TARGET ps3recomp_runtime PROPERTY SOURCES "${EXECUTION_RUNTIME_SOURCES}")
file(GLOB_RECURSE EXECUTION_PATCHED_SOURCES "${EXECUTION_SDK}/*.cpp" "${EXECUTION_SDK}/*.c")
set_source_files_properties(${EXECUTION_PATCHED_SOURCES} PROPERTIES INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(${PROJECT_NAME}
    main.cpp
    stubs.cpp
    boot_diagnostics.cpp
    text_conversion.cpp
    frame_history.cpp
    fps_overlay.cpp
    player_settings.cpp
    player_session.cpp
    vulkan_diagnostics.cpp
    input_events.cpp
    resource_diagnostics.cpp
    rendering_profile.cpp
    frame_budget.cpp
    page_census.cpp
    rsx_texture_diagnostics.cpp
    texture_decode.cpp
    texture_decode_fast.cpp
    execution_policy.cpp
    execution_spu_probe.c
    hle_inventory.cpp
    content_paths.cpp
    entitlement.cpp
    offline_network.cpp
    offline_network_probe.cpp
    entitlement_edat.c
    asset_probe.cpp
    ${PS3RECOMP_SCAFFOLD}
    ${HLE_NID_TABLE}
    ${RECOMP_SOURCES}
    ${SPU_SOURCES}
)

set_source_files_properties(texture_decode.cpp texture_decode_fast.cpp PROPERTIES INCLUDE_DIRECTORIES "${PS3RECOMP_DIR}/libs/video")

set_source_files_properties(asset_probe.cpp entitlement_edat.c PROPERTIES
    INCLUDE_DIRECTORIES "${PS3RECOMP_DIR}/libs/filesystem")

option(TANK_BATTLES_DIAGNOSTIC_TESTS "Compile synthetic-only override and stall fixtures" OFF)
if(TANK_BATTLES_DIAGNOSTIC_TESTS)
    target_compile_definitions(${PROJECT_NAME} PRIVATE TB_DIAGNOSTIC_TESTS)
endif()


# The lifted SPU sources use bare #include "spu_context.h". Scope that include
# directory (and their flags) to those TUs alone: adding it to the whole target
# changes the compile command of every lifted PPU TU, and those are large enough
# that the needless rebuild is measured in tens of minutes.
#   SPU_USE_DISPATCH_MT -- route SPU_IB_DISPATCH through the musttail path.
#   -O2                 -- the target-wide -O1 exists for the huge PPU TUs; the
#                          SPU sources are small and decode-hot, so they get
#                          full optimization. The last -O on the line wins.
if(SPU_SOURCES)
    set_source_files_properties(${SPU_SOURCES} PROPERTIES
        INCLUDE_DIRECTORIES "${PS3RECOMP_DIR}/runtime/spu"
        COMPILE_DEFINITIONS SPU_USE_DISPATCH_MT)
    if(NOT MSVC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        set_source_files_properties(${SPU_SOURCES} PROPERTIES COMPILE_OPTIONS "-O2")
    endif()
endif()

# app.rc carries the icon, the version block and the manifest, and needs a
# resource compiler. Listing it as a source on a host that has none is not a
# link error, it is a "no rule to make" for a file nothing can consume.
if(WIN32 AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/app.rc")
    target_sources(${PROJECT_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/app.rc")
endif()

target_include_directories(${PROJECT_NAME} PRIVATE
    "${RECOMP_DIR}"                            # the generated ppu_recomp.h
    "${PS3RECOMP_DIR}/include"
    "${PS3RECOMP_DIR}/runtime/ppu"
    "${PS3RECOMP_DIR}/runtime/spu"
    "${PS3RECOMP_DIR}/runtime/memory"
    "${PS3RECOMP_DIR}/runtime/syscalls"
    "${PS3RECOMP_DIR}/runtime/platform"        # win32_compat.h and friends
    "${PS3RECOMP_DIR}/libs/video"
    "${CMAKE_CURRENT_SOURCE_DIR}"
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    ps3recomp_runtime
)

# The runtime is C++, and on Apple also Objective-C. Letting CMake infer the
# link language from the generated C would leave the C++ standard library out.
set_target_properties(${PROJECT_NAME} PROPERTIES LINKER_LANGUAGE CXX)

# Windows system import libraries. dbghelp symbolizes crash-handler addresses
# against the PDB, user32 and gdi32 are what the backend's window and its GDI
# present are made of, and winmm is timeBeginPeriod. None of the four names
# anything a POSIX linker can find; the D3D12 backend's own imports come in
# through the #pragma comment(lib) in libs/video/rsx_d3d12_backend.c.
if(WIN32)
    # Port-owned input/audio source copies select SDL on Windows as on Linux.
    find_package(SDL2 CONFIG REQUIRED)
    target_link_libraries(ps3recomp_runtime PRIVATE SDL2::SDL2)
    target_link_libraries(${PROJECT_NAME} PRIVATE SDL2::SDL2 winmm)
    target_link_libraries(${PROJECT_NAME} PRIVATE
        dbghelp
        user32
        gdi32
        winmm
    )
endif()

# ---------------------------------------------------------------------------
# Compiler-specific flags
# ---------------------------------------------------------------------------
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    # Player Release keeps the accepted -O2 optimization, without debug data.
    foreach(player_target ${PROJECT_NAME} ps3recomp_runtime)
        target_compile_options(${player_target} PRIVATE "$<$<CONFIG:Release>:-O2;-g0>")
    endforeach()
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        target_link_options(${PROJECT_NAME} PRIVATE $<$<CONFIG:Release>:-s>)
    endif()
    target_compile_options(${PROJECT_NAME} PRIVATE
        -Wall -Wextra -Wno-unused-parameter
        -fno-strict-aliasing
        # The lift spells PPC arithmetic as signed C arithmetic, which must wrap;
        # without this, clang exploits overflow UB (e.g. folds "x*x < 0" to false).
        -fwrapv
        # The target-wide -O1 the SPU note above relies on, for a build whose
        # configuration sets no optimisation level (no CMAKE_BUILD_TYPE, or one
        # CMake has no defaults for). Without it every lifted PPU TU compiled at
        # -O0: each static inline helper (ppc_rlw64, vmx_w, vm_read32_inl) is a
        # real call that spills to the stack, and those calls were most of the
        # PPU thread's profile. -O1 keeps the huge TUs' build time sane. Debug
        # and the optimised configurations keep their own levels.
        $<$<NOT:$<CONFIG:Debug,Release,RelWithDebInfo,MinSizeRel>>:-O1>
    )
elseif(MSVC)
    target_compile_options(${PROJECT_NAME} PRIVATE
        /W3 /wd4100 /wd4244 /wd4267
        /Zc:__cplusplus         # report correct __cplusplus value
        # ppu_recomp.h defines PPU_FENCE, the lowering of the guest's
        # sync/lwsync/eieio/isync, and in C that is <stdatomic.h>. The lifter
        # writes C++ by default, but --single-file writes ppu_recomp.c, and a
        # port that took it would not compile without this. The runtime's own
        # build already carries the same flag for the same reason.
        /experimental:c11atomics
    )
    target_compile_definitions(${PROJECT_NAME} PRIVATE
        _CRT_SECURE_NO_WARNINGS
    )
endif()

# The lifted translation units are machine-generated and can run to hundreds of
# thousands of lines each, so they get their own flags:
#
#   - warnings off. They are about generated code and nobody will act on them,
#     and at this volume they bury the diagnostics that matter.
#   - no floating-point contraction. arm64 fuses a multiply and an add by
#     default; a PPC fmadd is spelt as one where the lifter means one.
#   - /bigobj on MSVC. A single lifted file routinely passes the 65,536-section
#     limit of the default object format.
if(MSVC)
    set_source_files_properties(${RECOMP_SOURCES} PROPERTIES
        COMPILE_OPTIONS "/w;/bigobj")
else()
    set_source_files_properties(${RECOMP_SOURCES} PROPERTIES
        COMPILE_OPTIONS "-w;-ffp-contract=off")
endif()

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION bin)

# ---------------------------------------------------------------------------
# The window launcher (macOS)
# ---------------------------------------------------------------------------
# A title that replaces its executable (sys_game_process_exitspawn, done by a
# port as an exec) loses its window with the old process image. The launcher
# holds the window for the whole session instead: it runs the port with
# PS3RECOMP_HOST_SURFACES set, the Metal backend renders into IOSurfaces it
# shares (libs/video/rsx_host_surface.h), and the image an exec brings up
# attaches to the same window again. One Objective-C file and no runtime
# library, so it builds in seconds:
#
#   ./MyGameRecomp_window [--title T] [--size WxH] -- ./MyGameRecomp <args>
if(APPLE)
    enable_language(OBJC)
    add_executable(${PROJECT_NAME}_window "${PS3RECOMP_DIR}/tools/host_window/host_window.m")
    target_include_directories(${PROJECT_NAME}_window PRIVATE "${PS3RECOMP_DIR}/libs/video")
    target_compile_definitions(${PROJECT_NAME}_window PRIVATE HOST_WINDOW_TITLE="${PROJECT_NAME}")
    target_compile_options(${PROJECT_NAME}_window PRIVATE -fobjc-arc -Wall -Wextra -Wno-unused-parameter)
    target_link_libraries(${PROJECT_NAME}_window PRIVATE
        "-framework AppKit" "-framework Metal" "-framework QuartzCore" "-framework IOSurface")
    install(TARGETS ${PROJECT_NAME}_window RUNTIME DESTINATION bin)
endif()
