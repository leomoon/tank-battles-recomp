find_package(Python3 3.11 COMPONENTS Interpreter REQUIRED)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/runtime_abi.py"
    "${PS3RECOMP_DIR}/tools/ppu_lifter.py" "${RECOMP_DIR}/ppu_recomp.h")
set(TB_ABI_DIR "${CMAKE_CURRENT_BINARY_DIR}/runtime-abi")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/runtime_abi.py"
    --output "${TB_ABI_DIR}" --sdk "${PS3RECOMP_DIR}" --check-title-header "${RECOMP_DIR}/ppu_recomp.h"
    COMMAND_ERROR_IS_FATAL ANY)
add_library(tank_battles_host OBJECT ${TB_HOST_SOURCES})
add_library(tank_battles_boot OBJECT main.cpp)
add_library(tank_battles_host_archive STATIC $<TARGET_OBJECTS:tank_battles_host>)

add_library(tank_battles_boot_archive STATIC $<TARGET_OBJECTS:tank_battles_boot>)
