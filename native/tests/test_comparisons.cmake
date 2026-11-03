if(NOT DEFINED COMPARISONS_EXE OR NOT EXISTS "${COMPARISONS_EXE}")
    message(FATAL_ERROR "Native comparison executable was not found.")
endif()
if(DEFINED RUNTIME_DIR AND IS_DIRECTORY "${RUNTIME_DIR}")
    set(ENV{PATH} "${RUNTIME_DIR};$ENV{PATH}")
endif()

foreach(workload IN ITEMS sorting searching array_operations aggregation hash_lookup file_parsing numeric)
    execute_process(
        COMMAND "${COMPARISONS_EXE}" --workload "${workload}" --items 128
        RESULT_VARIABLE result
        OUTPUT_VARIABLE output
        ERROR_VARIABLE error_output
        TIMEOUT 10
    )
    if(NOT result EQUAL 0)
        message(FATAL_ERROR "${workload} failed: ${result}\n${error_output}")
    endif()
    string(JSON actual_workload ERROR_VARIABLE json_error GET "${output}" workload)
    if(NOT json_error STREQUAL "NOTFOUND" OR NOT actual_workload STREQUAL workload)
        message(FATAL_ERROR "Invalid ${workload} result JSON: ${output}\n${json_error}")
    endif()
    string(JSON elapsed_ns ERROR_VARIABLE duration_error GET "${output}" elapsed_ns)
    if(NOT duration_error STREQUAL "NOTFOUND" OR NOT elapsed_ns MATCHES "^[0-9]+$")
        message(FATAL_ERROR "Invalid elapsed_ns for ${workload}: ${output}")
    endif()
    if(elapsed_ns LESS_EQUAL 0)
        message(FATAL_ERROR "${workload} elapsed_ns must be positive.")
    endif()
    string(JSON checksum ERROR_VARIABLE checksum_error GET "${output}" checksum)
    if(NOT checksum_error STREQUAL "NOTFOUND" OR NOT checksum MATCHES "^[0-9]+$")
        message(FATAL_ERROR "Invalid checksum for ${workload}: ${output}")
    endif()
endforeach()
