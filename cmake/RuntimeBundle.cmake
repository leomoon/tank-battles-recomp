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
add_test(NAME runtime_cpu_smoke COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/verify_runtime_smoke.py"
    --runner "$<TARGET_FILE:runtime_cpu_smoke>" --data "${TB_SYNTHETIC_DIR}")
add_subdirectory(tests/host_services "bundle-contracts")

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/generate_gpu.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/test_vulkan_draw_groups.py")
# Real backend fixtures are compiled on hosted CI, executed only on GPU hosts.
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/runtime_bundle/generate_gpu.py"
    "${CMAKE_CURRENT_BINARY_DIR}/gpu-fixtures" "${EXECUTION_SDK}" COMMAND_ERROR_IS_FATAL ANY)
find_package(SDL2 CONFIG REQUIRED)
find_package(glslang CONFIG REQUIRED)
foreach(fixture IN ITEMS draw_groups depth16 nv3089)
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
        target_link_libraries(vulkan_${fixture} PRIVATE m ${CMAKE_DL_LIBS} Threads::Threads)
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
"{\"schema\":1,\"compiler_id\":\"${CMAKE_CXX_COMPILER_ID}\",\"compiler_version\":\"${CMAKE_CXX_COMPILER_VERSION}\",\"compiler_frontend\":\"${CMAKE_CXX_COMPILER_FRONTEND_VARIANT}\",\"msvc_version\":\"${MSVC_VERSION}\",\"windows_sdk\":\"${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}\",\"architecture\":\"${CMAKE_SYSTEM_PROCESSOR}\",\"system\":\"${CMAKE_SYSTEM_NAME}\",\"crt\":\"${CMAKE_MSVC_RUNTIME_LIBRARY}\",\"archive_sources\":[${tb_archive_sources_json}]}\n")

add_custom_target(runtime_bundle
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/runtime_bundle.py" pack
      --build "${CMAKE_CURRENT_BINARY_DIR}" --configuration "$<CONFIG>" --output "${CMAKE_CURRENT_BINARY_DIR}/runtime-bundle"
      --host "$<TARGET_FILE:tank_battles_host_archive>" --runtime "$<TARGET_FILE:ps3recomp_runtime>"
      --boot "$<TARGET_FILE:tank_battles_boot_archive>" --cpu-boot "$<TARGET_FILE:tank_battles_cpu_boot>"
      --tool "$<TARGET_FILE:runtime_cpu_smoke>" --tool "$<TARGET_FILE:vulkan_draw_groups>"
      --tool "$<TARGET_FILE:vulkan_depth16>" --tool "$<TARGET_FILE:vulkan_nv3089>"
      --tool "$<TARGET_FILE:hle_fixture>" --tool "$<TARGET_FILE:positions_fixture>" --tool "$<TARGET_FILE:hotplug_fixture>"
      --tool "$<TARGET_FILE:keyboard_fixture>" --tool "$<TARGET_FILE:audio_fixture>" --tool "$<TARGET_FILE:cache_fixture>"
      --tool "$<TARGET_FILE:timeline_fixture>" --tool "$<TARGET_FILE:entitlement_fixture>"
      $<$<BOOL:${WIN32}>:--dependencies> $<$<BOOL:${WIN32}>:${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}>
    DEPENDS tank_battles_host_archive tank_battles_boot_archive tank_battles_cpu_boot ps3recomp_runtime
      runtime_cpu_smoke vulkan_draw_groups vulkan_depth16 vulkan_nv3089
      hle_fixture positions_fixture hotplug_fixture keyboard_fixture audio_fixture cache_fixture timeline_fixture entitlement_fixture
    COMMAND_EXPAND_LISTS VERBATIM)
add_test(NAME runtime_bundle_integrity COMMAND "${Python3_EXECUTABLE}" -m unittest discover
    -s "${CMAKE_CURRENT_SOURCE_DIR}/scripts" -p test_runtime_bundle.py)
add_test(NAME runtime_abi_compatibility COMMAND "${Python3_EXECUTABLE}" -m unittest discover
    -s "${CMAKE_CURRENT_SOURCE_DIR}/scripts" -p test_runtime_abi.py)
