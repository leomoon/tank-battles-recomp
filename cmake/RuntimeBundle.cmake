# Production libraries have no synthetic or title-generated source members.
add_library(tank_battles_cpu_boot STATIC main.cpp)
target_compile_definitions(tank_battles_cpu_boot PRIVATE TB_CPU_SMOKE=1)
target_include_directories(tank_battles_cpu_boot PRIVATE "${TB_ABI_DIR}" $<TARGET_PROPERTY:${PROJECT_NAME},INCLUDE_DIRECTORIES>)
target_compile_options(tank_battles_cpu_boot PRIVATE $<TARGET_PROPERTY:${PROJECT_NAME},COMPILE_OPTIONS>)
target_link_libraries(tank_battles_cpu_boot PRIVATE ps3recomp_runtime)
if(WIN32)
    target_compile_definitions(tank_battles_cpu_boot PRIVATE NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()
add_executable(runtime_cpu_smoke runtime_entry.cpp ${RECOMP_SOURCES} ${SPU_SOURCES})
target_compile_definitions(runtime_cpu_smoke PRIVATE TB_CPU_SMOKE=1)
target_include_directories(runtime_cpu_smoke PRIVATE $<TARGET_PROPERTY:${PROJECT_NAME},INCLUDE_DIRECTORIES>)
target_compile_options(runtime_cpu_smoke PRIVATE $<TARGET_PROPERTY:${PROJECT_NAME},COMPILE_OPTIONS>)
target_link_libraries(runtime_cpu_smoke PRIVATE tank_battles_cpu_boot tank_battles_host)
if(WIN32)
    target_compile_definitions(runtime_cpu_smoke PRIVATE NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()
enable_testing()
add_executable(runtime_shader_check "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/shader_translation.c")
target_include_directories(runtime_shader_check PRIVATE "${PS3RECOMP_DIR}/include" "${PS3RECOMP_DIR}/libs/video")
# Link the production archive: compiling a second translator in this fixture
# would conceal a disabled translator in the distributed runtime.
target_link_libraries(runtime_shader_check PRIVATE ps3recomp_runtime)
set_target_properties(runtime_shader_check PROPERTIES LINKER_LANGUAGE CXX)
add_test(NAME runtime_shader_translation COMMAND runtime_shader_check)
find_package(Threads REQUIRED)
add_executable(runtime_display_settings_check
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/display_settings.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/player_settings.cpp")
target_include_directories(runtime_display_settings_check PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(runtime_display_settings_check PRIVATE Threads::Threads)
add_test(NAME runtime_display_settings COMMAND runtime_display_settings_check
    "${CMAKE_CURRENT_BINARY_DIR}/display-settings-test")
add_executable(runtime_render_scale_check "${CMAKE_CURRENT_SOURCE_DIR}/tests/render_scale.c")
target_include_directories(runtime_render_scale_check PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
add_test(NAME runtime_render_scale_geometry COMMAND runtime_render_scale_check)
if(MSVC)
    target_compile_options(runtime_render_scale_check PRIVATE /UNDEBUG)
else()
    target_compile_options(runtime_render_scale_check PRIVATE -UNDEBUG)
endif()
add_executable(runtime_overlay_check "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/overlay.cpp")
target_include_directories(runtime_overlay_check PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(runtime_overlay_check PRIVATE tank_battles_host_archive Threads::Threads)
if(MSVC)
    target_compile_options(runtime_overlay_check PRIVATE /UNDEBUG)
else()
    target_compile_options(runtime_overlay_check PRIVATE -UNDEBUG)
endif()
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/overlay-screens")
add_test(NAME runtime_friends_overlay COMMAND runtime_overlay_check)
set_tests_properties(runtime_friends_overlay PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/overlay-screens"
    ENVIRONMENT "TB_FPS_OVERLAY=1;TB_SETTINGS_FILE="
    TIMEOUT 30)
add_test(NAME runtime_cpu_smoke COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/verify_runtime_smoke.py"
    --runner "$<TARGET_FILE:runtime_cpu_smoke>" --data "${TB_SYNTHETIC_DIR}")
add_test(NAME runtime_offline_network COMMAND "${CMAKE_COMMAND}" -E env
    "TB_TEST_RUNNER=$<TARGET_FILE:runtime_cpu_smoke>" "TB_TEST_ELF=${TB_SYNTHETIC_DIR}/smoke.elf"
    "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/test_offline_network.py")
set_tests_properties(runtime_offline_network PROPERTIES TIMEOUT 45)
add_subdirectory(tests/host_services "bundle-contracts")

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/generate_gpu.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/test_vulkan_draw_groups.py")
# Real backend fixtures are compiled on hosted CI, executed only on GPU hosts.
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/generate_gpu.py"
    "${CMAKE_CURRENT_BINARY_DIR}/gpu-fixtures" "${EXECUTION_SDK}" COMMAND_ERROR_IS_FATAL ANY)
find_package(SDL2 CONFIG REQUIRED)
add_executable(runtime_render_scale_submission_check
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/render_scale_submission.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/texture_decode.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/texture_decode_fast.cpp"
    "${CMAKE_CURRENT_BINARY_DIR}/gpu-fixtures/hooks.c")
target_include_directories(runtime_render_scale_submission_check PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}" "${PS3RECOMP_DIR}/include"
    "${PS3RECOMP_DIR}/libs/video" "${PS3RECOMP_DIR}/libs/video/tests")
target_link_libraries(runtime_render_scale_submission_check PRIVATE ps3recomp_runtime)
set_target_properties(runtime_render_scale_submission_check PROPERTIES LINKER_LANGUAGE CXX)
if(MSVC)
    target_compile_options(runtime_render_scale_submission_check PRIVATE /UNDEBUG /Gy /Gw)
    target_link_options(runtime_render_scale_submission_check PRIVATE /OPT:REF)
else()
    target_compile_options(runtime_render_scale_submission_check PRIVATE -UNDEBUG)
endif()
add_test(NAME runtime_render_scale_submission COMMAND runtime_render_scale_submission_check)
add_executable(runtime_display_window_check
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/player_window.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/player_settings.cpp")
target_include_directories(runtime_display_window_check PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(runtime_display_window_check PRIVATE SDL2::SDL2 Threads::Threads)
if(MSVC)
    target_compile_options(runtime_display_window_check PRIVATE /UNDEBUG)
else()
    target_compile_options(runtime_display_window_check PRIVATE -UNDEBUG)
endif()
add_test(NAME runtime_display_window COMMAND runtime_display_window_check)
set_tests_properties(runtime_display_window PROPERTIES
    ENVIRONMENT "SDL_VIDEODRIVER=dummy;TB_SETTINGS_FILE=${CMAKE_CURRENT_BINARY_DIR}/display-window.ini;PS3RECOMP_FULLSCREEN=0;TB_RENDER_SCALE=200"
    TIMEOUT 30)
foreach(scale IN ITEMS 50 75)
    add_test(NAME runtime_display_window_${scale} COMMAND runtime_display_window_check)
    set_tests_properties(runtime_display_window_${scale} PROPERTIES
        ENVIRONMENT "SDL_VIDEODRIVER=dummy;TB_SETTINGS_FILE=${CMAKE_CURRENT_BINARY_DIR}/display-window-${scale}.ini;PS3RECOMP_FULLSCREEN=0;TB_RENDER_SCALE=${scale}"
        TIMEOUT 30)
endforeach()
find_package(glslang CONFIG REQUIRED)
foreach(fixture IN ITEMS draw_groups depth16 nv3089 render_scale)
    add_executable(vulkan_${fixture} "${CMAKE_CURRENT_BINARY_DIR}/gpu-fixtures/${fixture}.c")
    if(WIN32)
        # COFF resolves references in the complete backend object before section
        # pruning. Link the real host/runtime closure and original synthetic
        # tables rather than replacing unused production entry points with stubs.
        target_sources(vulkan_${fixture} PRIVATE ${RECOMP_SOURCES} ${SPU_SOURCES})
        target_include_directories(vulkan_${fixture} PRIVATE $<TARGET_PROPERTY:${PROJECT_NAME},INCLUDE_DIRECTORIES>)
        target_link_libraries(vulkan_${fixture} PRIVATE tank_battles_cpu_boot tank_battles_host)
    else()
        target_sources(vulkan_${fixture} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/gpu-fixtures/hooks.c"
            "${PS3RECOMP_DIR}/libs/video/rsx_shader_spirv.cpp" "${PS3RECOMP_DIR}/libs/video/rsx_texture_layout.c")
    endif()
    target_include_directories(vulkan_${fixture} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${PS3RECOMP_DIR}/include" "${PS3RECOMP_DIR}/libs/video" "${Vulkan_INCLUDE_DIRS}")
    target_compile_definitions(vulkan_${fixture} PRIVATE PS3RECOMP_HAVE_SPIRV_TRANSLATION=1 NOMINMAX SDL_MAIN_HANDLED)
    target_link_libraries(vulkan_${fixture} PRIVATE glslang::glslang glslang::SPIRV SDL2::SDL2)
    if(TARGET glslang::glslang-default-resource-limits)
        target_link_libraries(vulkan_${fixture} PRIVATE glslang::glslang-default-resource-limits)
    endif()
    if(MSVC)
        target_compile_options(vulkan_${fixture} PRIVATE /UNDEBUG /Gy /Gw)
        target_link_options(vulkan_${fixture} PRIVATE /OPT:REF)
    else()
        target_compile_options(vulkan_${fixture} PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
        target_link_options(vulkan_${fixture} PRIVATE -Wl,--gc-sections)
        if(NOT WIN32)
            target_link_libraries(vulkan_${fixture} PRIVATE m ${CMAKE_DL_LIBS} Threads::Threads)
        endif()
    endif()
endforeach()

get_target_property(tb_runtime_sources ps3recomp_runtime SOURCES)
set(tb_archive_sources "")
foreach(source IN LISTS TB_HOST_SOURCES)
    get_filename_component(source "${source}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
    list(APPEND tb_archive_sources "\"${source}\"")
endforeach()
foreach(source IN LISTS tb_runtime_sources)
    if(NOT IS_ABSOLUTE "${source}")
        set(source "${PS3RECOMP_DIR}/${source}")
    endif()
    list(APPEND tb_archive_sources "\"${source}\"")
endforeach()
list(APPEND tb_archive_sources "\"${CMAKE_CURRENT_SOURCE_DIR}/main.cpp\"")
list(JOIN tb_archive_sources ",\n" tb_archive_sources_json)
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/runtime-build.json" CONTENT
"{\"schema\":1,\"compiler_id\":\"${CMAKE_CXX_COMPILER_ID}\",\"compiler_version\":\"${CMAKE_CXX_COMPILER_VERSION}\",\"compiler_frontend\":\"${CMAKE_CXX_COMPILER_FRONTEND_VARIANT}\",\"msvc_version\":\"${MSVC_VERSION}\",\"windows_sdk\":\"${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}\",\"architecture\":\"${CMAKE_SYSTEM_PROCESSOR}\",\"system\":\"${CMAKE_SYSTEM_NAME}\",\"crt\":\"${CMAKE_MSVC_RUNTIME_LIBRARY}\",\"toolchain\":\"${TB_TOOLCHAIN}\",\"target\":\"${TB_ZIG_TARGET}\",\"archive_sources\":[${tb_archive_sources_json}]}\n")

set(tb_audio_burst_tools "")
set(tb_audio_burst_dependency "")
if(WIN32)
    set(tb_audio_burst_tools --tool "$<TARGET_FILE:audio_burst_fixture>")
    set(tb_audio_burst_dependency audio_burst_fixture)
endif()
# Windows bundles carry the release static dependency libraries/headers and the
# vcpkg status of the tree they came from. The vcpkg toolchain (as in CI, with
# VCPKG_MANIFEST_DIR=ci/windows-runtime) provides both; any other prepared tree
# needs the same layout: <triplet dir>/{include,lib,share} beside vcpkg/status.
set(tb_bundle_dependency_default "")
if(VCPKG_INSTALLED_DIR AND VCPKG_TARGET_TRIPLET)
    set(tb_bundle_dependency_default "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}")
elseif(TB_TOOLCHAIN)
    # Standalone-builder route: the scripts/build_runtime_deps.py prefix (deps.json) on either OS.
    foreach(prefix IN LISTS CMAKE_PREFIX_PATH)
        if(EXISTS "${prefix}/deps.json")
            set(tb_bundle_dependency_default "${prefix}")
        endif()
    endforeach()
endif()
set(TB_BUNDLE_DEPENDENCIES "${tb_bundle_dependency_default}" CACHE PATH
    "Windows: installed dependency tree packed into the runtime bundle (default: the vcpkg triplet directory)")
if(WIN32 AND NOT TB_TOOLCHAIN AND NOT EXISTS "${TB_BUNDLE_DEPENDENCIES}/../vcpkg/status")
    message(WARNING "runtime_bundle needs TB_BUNDLE_DEPENDENCIES (a vcpkg triplet directory with ../vcpkg/status); "
                    "configure with the vcpkg toolchain and VCPKG_MANIFEST_DIR=ci/windows-runtime as README describes.")
endif()
add_custom_target(runtime_bundle
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/runtime_bundle.py" pack
      --build "${CMAKE_CURRENT_BINARY_DIR}" --configuration "$<CONFIG>" --output "${CMAKE_CURRENT_BINARY_DIR}/runtime-bundle"
      --host "$<TARGET_FILE:tank_battles_host_archive>" --runtime "$<TARGET_FILE:ps3recomp_runtime>"
      --boot "$<TARGET_FILE:tank_battles_boot_archive>" --cpu-boot "$<TARGET_FILE:tank_battles_cpu_boot>"
      --tool "$<TARGET_FILE:runtime_cpu_smoke>" --tool "$<TARGET_FILE:vulkan_draw_groups>"
      --tool "$<TARGET_FILE:runtime_shader_check>"
      --tool "$<TARGET_FILE:vulkan_depth16>" --tool "$<TARGET_FILE:vulkan_nv3089>" --tool "$<TARGET_FILE:vulkan_render_scale>"
      --tool "$<TARGET_FILE:runtime_display_settings_check>" --tool "$<TARGET_FILE:runtime_display_window_check>"
      --tool "$<TARGET_FILE:runtime_render_scale_check>"
      --tool "$<TARGET_FILE:runtime_render_scale_submission_check>"
      --tool "$<TARGET_FILE:hle_fixture>" --tool "$<TARGET_FILE:positions_fixture>" --tool "$<TARGET_FILE:hotplug_fixture>"
      --tool "$<TARGET_FILE:keyboard_fixture>" --tool "$<TARGET_FILE:audio_fixture>" --tool "$<TARGET_FILE:cache_fixture>"
      --tool "$<TARGET_FILE:timeline_fixture>" --tool "$<TARGET_FILE:entitlement_fixture>"
      --tool "$<TARGET_FILE:matching_rooms>" --tool "$<TARGET_FILE:peer_udp_fixture>" --tool "$<TARGET_FILE:sysnet_fixture>"
      ${tb_audio_burst_tools}
      $<$<BOOL:${TB_BUNDLE_DEPENDENCIES}>:--dependencies> $<$<BOOL:${TB_BUNDLE_DEPENDENCIES}>:${TB_BUNDLE_DEPENDENCIES}>
    DEPENDS tank_battles_host_archive tank_battles_boot_archive tank_battles_cpu_boot ps3recomp_runtime
      runtime_cpu_smoke vulkan_draw_groups vulkan_depth16 vulkan_nv3089 vulkan_render_scale
      runtime_display_settings_check runtime_display_window_check runtime_render_scale_check
      runtime_render_scale_submission_check
      runtime_shader_check
      ${tb_audio_burst_dependency}
      hle_fixture positions_fixture hotplug_fixture keyboard_fixture audio_fixture cache_fixture timeline_fixture entitlement_fixture
      matching_rooms peer_udp_fixture sysnet_fixture
    COMMAND_EXPAND_LISTS VERBATIM)
add_test(NAME runtime_bundle_integrity COMMAND "${Python3_EXECUTABLE}" -m unittest discover
    -s "${CMAKE_CURRENT_SOURCE_DIR}/scripts" -p test_runtime_bundle.py)
add_test(NAME runtime_abi_compatibility COMMAND "${Python3_EXECUTABLE}" -m unittest discover
    -s "${CMAKE_CURRENT_SOURCE_DIR}/scripts" -p test_runtime_abi.py)
