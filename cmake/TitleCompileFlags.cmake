# Compile semantics the lifted PPU/SPU translation units depend on. Shared by the
# full-source build (CMakeLists.txt) and the Windows CI-runtime consumer
# (cmake/player-consumer), so a player built either way compiles the guest code
# the same way.
#
#   -fwrapv              PPC arithmetic is spelt as signed C arithmetic and must
#                        wrap; without it clang folds overflow UB (x*x < 0).
#   -fno-strict-aliasing guest memory is reinterpreted freely.
#   -ffp-contract=off    no spontaneous fused multiply-add: a PPC fmadd is spelt
#                        as one where the lifter means one, and every other
#                        multiply/add must round separately.
#   ISA                  SSE4.1 is the baseline (the runtime's SPU helpers use
#                        pshufb/blendv). TB_TITLE_ARCH selects a higher level
#                        such as x86-64-v3 for code compiled on the player's own
#                        machine; the reusable runtime keeps the baseline.
#
# clang-cl takes the GCC-style spellings through /clang:.
set(TB_TITLE_ARCH "" CACHE STRING
    "ISA level for the locally compiled title code: empty for the SSE4.1 baseline, or an -march value such as x86-64-v3")

set(TB_TITLE_PGO "" CACHE STRING
    "Title PGO: empty (off), 'generate' (instrumented build), or the path of a merged .profdata to optimize with")

function(tb_title_compile_flags target)
    if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
        message(FATAL_ERROR "The lifted title code requires clang or GCC semantics (-fwrapv)")
    endif()
    if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
        set(prefix "/clang:")
    else()
        set(prefix "")
    endif()
    set(flags "${prefix}-fwrapv" "${prefix}-fno-strict-aliasing" "${prefix}-ffp-contract=off")
    if(TB_TITLE_ARCH)
        if(NOT TB_TITLE_ARCH MATCHES "^[A-Za-z0-9_.-]+$")
            message(FATAL_ERROR "Invalid TB_TITLE_ARCH: ${TB_TITLE_ARCH}")
        endif()
        list(APPEND flags "${prefix}-march=${TB_TITLE_ARCH}")
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(AMD64|x86_64|amd64)$")
        list(APPEND flags "${prefix}-msse4.1")
    endif()
    # Profile-guided optimization of the locally compiled title code. The
    # profile is derived from the purchased game: keep it local, like the
    # translated sources, and regenerate it whenever they change.
    if(TB_TITLE_PGO STREQUAL "generate")
        list(APPEND flags "${prefix}-fprofile-instr-generate")
        target_sources(${target} PRIVATE "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../pgo_dump.cpp")
        if(prefix)
            get_filename_component(llvm_bin "${CMAKE_CXX_COMPILER}" DIRECTORY)
            file(GLOB profile_runtime "${llvm_bin}/../lib/clang/*/lib/windows/clang_rt.profile-x86_64.lib")
            if(NOT profile_runtime)
                message(FATAL_ERROR "clang_rt.profile-x86_64.lib not found next to ${CMAKE_CXX_COMPILER}")
            endif()
            list(GET profile_runtime 0 profile_runtime)
            target_link_libraries(${target} PRIVATE "${profile_runtime}")
        else()
            target_link_options(${target} PRIVATE -fprofile-instr-generate)
        endif()
    elseif(TB_TITLE_PGO)
        if(NOT EXISTS "${TB_TITLE_PGO}")
            message(FATAL_ERROR "TB_TITLE_PGO profile not found: ${TB_TITLE_PGO}")
        endif()
        list(APPEND flags "${prefix}-fprofile-instr-use=${TB_TITLE_PGO}" "${prefix}-Wno-profile-instr-unprofiled"
             "${prefix}-Wno-profile-instr-out-of-date")
    endif()
    target_compile_options(${target} PRIVATE ${flags})
    set_property(TARGET ${target} PROPERTY TB_TITLE_COMPILE_FLAGS "${flags}")
    message(STATUS "Title code flags: ${flags}")
endfunction()

# Build-time refinements of the lifted sources (scripts/title_codegen.py). The
# lifter's output is left untouched; the build compiles transformed copies.
option(TB_TITLE_RESTRICT_CONTEXT
    "Compile lifted PPU functions with a __restrict context parameter" OFF)

function(tb_title_sources out_var)
    if(NOT TB_TITLE_RESTRICT_CONTEXT)
        set(${out_var} ${ARGN} PARENT_SCOPE)
        return()
    endif()
    find_package(Python3 3.11 COMPONENTS Interpreter REQUIRED)
    set(script "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../scripts/title_codegen.py")
    set(outputs "")
    foreach(source ${ARGN})
        get_filename_component(name "${source}" NAME)
        if(name MATCHES "^ppu_recomp_[0-9]+[.](c|cpp)$")
            set(output "${CMAKE_CURRENT_BINARY_DIR}/title_codegen/${name}")
            add_custom_command(OUTPUT "${output}"
                COMMAND "${Python3_EXECUTABLE}" "${script}" --input "${source}" --output "${output}"
                DEPENDS "${source}" "${script}" VERBATIM)
            list(APPEND outputs "${output}")
        else()
            list(APPEND outputs "${source}")
        endif()
    endforeach()
    message(STATUS "Title codegen: restrict-context")
    set(${out_var} ${outputs} PARENT_SCOPE)
endfunction()
