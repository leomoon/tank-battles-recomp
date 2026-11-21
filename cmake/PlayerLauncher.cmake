# "Play Tank Battles", the launcher of the movable player folder. It contains no
# game code and is built next to the player with the same compiler, so the
# folder needs nothing installed besides the system's graphics driver.
set(TB_LAUNCHER_DIR "${CMAKE_CURRENT_LIST_DIR}/../launcher")
add_executable(PlayTankBattles WIN32 "${TB_LAUNCHER_DIR}/play_tank_battles.cpp")
target_compile_features(PlayTankBattles PRIVATE cxx_std_20)
target_include_directories(PlayTankBattles PRIVATE "${TB_LAUNCHER_DIR}")
if(WIN32)
    target_compile_definitions(PlayTankBattles PRIVATE UNICODE _UNICODE NOMINMAX)
    target_link_libraries(PlayTankBattles PRIVATE shell32 user32)
    if(NOT MSVC)
        # A GUI program: no console window. Zig ignores -mwindows, so ask the linker.
        target_link_options(PlayTankBattles PRIVATE "-Wl,--subsystem,windows")
    endif()
endif()
