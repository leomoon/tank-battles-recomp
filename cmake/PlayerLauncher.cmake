# "Play Tank Battles", the launcher of the movable player folder: play offline
# or online and manage RPCN accounts. It contains no game code and is built next
# to the player with the same compiler and the runtime's SDL2, so the folder
# needs nothing installed besides the system's graphics driver.
set(TB_LAUNCHER_DIR "${CMAKE_CURRENT_LIST_DIR}/../launcher")
add_executable(PlayTankBattles WIN32
    "${TB_LAUNCHER_DIR}/play_tank_battles.cpp" "${TB_LAUNCHER_DIR}/accounts.cpp"
    "${TB_LAUNCHER_DIR}/platform.cpp" "${TB_LAUNCHER_DIR}/ui.cpp")
target_compile_features(PlayTankBattles PRIVATE cxx_std_20)
target_include_directories(PlayTankBattles PRIVATE "${TB_LAUNCHER_DIR}")

if(NOT TARGET SDL2::SDL2-static AND NOT TARGET SDL2::SDL2)
    find_package(SDL2 CONFIG QUIET)
endif()
if(TARGET SDL2::SDL2-static)
    target_link_libraries(PlayTankBattles PRIVATE SDL2::SDL2-static)
elseif(TARGET SDL2::SDL2)
    target_link_libraries(PlayTankBattles PRIVATE SDL2::SDL2)
elseif(MSVC AND DEFINED TB_RUNTIME_ROOT AND EXISTS "${TB_RUNTIME_ROOT}/deps/lib/SDL2-static.lib")
    # The ClangCL runtime bundle carries vcpkg's static SDL2 without a CMake package.
    target_include_directories(PlayTankBattles PRIVATE "${TB_RUNTIME_ROOT}/deps/include/SDL2")
    target_link_libraries(PlayTankBattles PRIVATE "${TB_RUNTIME_ROOT}/deps/lib/SDL2-static.lib"
        user32 gdi32 winmm imm32 ole32 oleaut32 version uuid advapi32 setupapi shell32)
else()
    message(FATAL_ERROR "The player launcher needs SDL2")
endif()

if(WIN32)
    target_compile_definitions(PlayTankBattles PRIVATE UNICODE _UNICODE NOMINMAX)
    target_link_libraries(PlayTankBattles PRIVATE shell32 user32 advapi32)
    if(NOT MSVC)
        # A GUI program: no console window. Zig ignores -mwindows, so ask the linker.
        target_link_options(PlayTankBattles PRIVATE "-Wl,--subsystem,windows")
    endif()
else()
    find_package(Threads REQUIRED)
    target_link_libraries(PlayTankBattles PRIVATE Threads::Threads ${CMAKE_DL_LIBS})
endif()
