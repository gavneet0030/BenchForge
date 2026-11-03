if(NOT DEFINED WORKLOADS_EXE OR NOT EXISTS "${WORKLOADS_EXE}")
    message(FATAL_ERROR "Native workload executable was not found.")
endif()
if(DEFINED RUNTIME_DIR AND IS_DIRECTORY "${RUNTIME_DIR}")
    set(ENV{PATH} "${RUNTIME_DIR};$ENV{PATH}")
endif()

foreach(workload IN ITEMS cpu memory file_io tcp_loopback)
    if(workload STREQUAL "cpu")
        set(arguments --iterations 10000)
    elseif(workload STREQUAL "memory")
        set(arguments --iterations 2 --working-set-kib 64)
    elseif(workload STREQUAL "file_io")
        set(arguments --iterations 1 --file-size-kib 8)
    else()
        set(arguments --iterations 10)
    endif()

    execute_process(
        COMMAND "${WORKLOADS_EXE}" --workload "${workload}" ${arguments}
        RESULT_VARIABLE result
        OUTPUT_VARIABLE output
        ERROR_VARIABLE error_output
        TIMEOUT 30
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
    string(JSON peak_memory ERROR_VARIABLE memory_error GET "${output}" peak_process_memory_bytes)
    if(NOT memory_error STREQUAL "NOTFOUND" OR NOT peak_memory MATCHES "^[0-9]+$")
        message(FATAL_ERROR "Invalid peak process memory for ${workload}: ${output}")
    endif()
    if(peak_memory LESS_EQUAL 0)
        message(FATAL_ERROR "Peak process memory must be positive for ${workload}.")
    endif()
    string(JSON memory_source ERROR_VARIABLE source_error GET "${output}" peak_process_memory_source)
    if(NOT source_error STREQUAL "NOTFOUND" OR memory_source STREQUAL "")
        message(FATAL_ERROR "Missing memory measurement source for ${workload}: ${output}")
    endif()
    string(JSON process_cpu ERROR_VARIABLE cpu_error GET "${output}" process_cpu_time_ns)
    if(NOT cpu_error STREQUAL "NOTFOUND")
        message(FATAL_ERROR "Could not read process CPU time for ${workload}: ${output}")
    endif()
    if(NOT "${process_cpu}" STREQUAL "" AND NOT process_cpu MATCHES "^[0-9]+$")
        message(FATAL_ERROR "Invalid process CPU time for ${workload}: ${output}")
    endif()
    string(JSON cpu_utilization ERROR_VARIABLE utilization_error GET "${output}" cpu_utilization_percent)
    if(NOT utilization_error STREQUAL "NOTFOUND")
        message(FATAL_ERROR "Could not read CPU utilization for ${workload}: ${output}")
    endif()
    if(NOT "${cpu_utilization}" STREQUAL ""
        AND NOT cpu_utilization MATCHES "^[0-9]+(\\.[0-9]+)?$")
        message(FATAL_ERROR
            "Invalid CPU utilization '${cpu_utilization}' "
            "(error '${utilization_error}') for ${workload}: ${output}")
    endif()
    foreach(field IN ITEMS current_working_set_bytes private_process_bytes thread_count)
        string(JSON optional_measurement ERROR_VARIABLE optional_error GET "${output}" "${field}")
        if(NOT optional_error STREQUAL "NOTFOUND")
            message(FATAL_ERROR "Could not read ${field} for ${workload}: ${output}")
        endif()
        if(NOT "${optional_measurement}" STREQUAL ""
            AND NOT optional_measurement MATCHES "^[0-9]+$")
            message(FATAL_ERROR "Invalid ${field} for ${workload}: ${output}")
        endif()
    endforeach()
endforeach()

execute_process(
    COMMAND "${WORKLOADS_EXE}" --workload file_io --iterations 17
    RESULT_VARIABLE invalid_result
    OUTPUT_QUIET
    ERROR_QUIET
    TIMEOUT 5
)
if(invalid_result EQUAL 0)
    message(FATAL_ERROR "Invalid workload limits were accepted.")
endif()
