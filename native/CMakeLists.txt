cmake_minimum_required(VERSION 3.20)
project(BenchForgeNative LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

function(add_benchforge_lookup_target target_name source_file)
    add_executable(${target_name} ${source_file})

    if(MSVC)
        target_compile_options(${target_name} PRIVATE /O2 /W4)
    else()
        target_compile_options(
            ${target_name} PRIVATE -O2 -Wall -Wextra -Wpedantic
        )
    endif()
endfunction()

add_benchforge_lookup_target(benchforge_lookup src/lookup.cpp)
add_benchforge_lookup_target(
    benchforge_lookup_optimized src/lookup_optimized.cpp
)

include(CTest)

if(BUILD_TESTING)
    get_filename_component(
        BENCHFORGE_COMPILER_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY
    )

    add_test(
        NAME BenchForgeBaselineCorrectness
        COMMAND ${CMAKE_COMMAND}
            -DLOOKUP_EXE=$<TARGET_FILE:benchforge_lookup>
            -DRUNTIME_DIR=${BENCHFORGE_COMPILER_DIR}
            -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_lookup.cmake
    )

    add_test(
        NAME BenchForgeOptimizedCorrectness
        COMMAND ${CMAKE_COMMAND}
            -DLOOKUP_EXE=$<TARGET_FILE:benchforge_lookup_optimized>
            -DRUNTIME_DIR=${BENCHFORGE_COMPILER_DIR}
            -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_lookup.cmake
    )
endif()
