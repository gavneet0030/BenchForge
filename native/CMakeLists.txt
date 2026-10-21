cmake_minimum_required(VERSION 3.20)
project(BenchForgeNative LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(benchforge_lookup src/lookup.cpp)

if(MSVC)
    target_compile_options(benchforge_lookup PRIVATE /O2 /W4)
else()
    target_compile_options(benchforge_lookup PRIVATE -O2 -Wall -Wextra -Wpedantic)
endif()
