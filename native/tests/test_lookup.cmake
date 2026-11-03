if(NOT DEFINED LOOKUP_EXE OR NOT EXISTS "${LOOKUP_EXE}")
    message(FATAL_ERROR "Native lookup executable was not found.")
endif()

if(DEFINED RUNTIME_DIR AND IS_DIRECTORY "${RUNTIME_DIR}")
    set(ENV{PATH} "${RUNTIME_DIR};$ENV{PATH}")
endif()

execute_process(
    COMMAND "${LOOKUP_EXE}" --iterations 1000 --extra-work 0
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error_output
    TIMEOUT 30
)

if(NOT result EQUAL 0)
    message(FATAL_ERROR
        "Native lookup execution failed: ${result}\n${error_output}")
endif()

string(JSON schema_version ERROR_VARIABLE json_error
    GET "${output}" schema_version)
if(NOT json_error STREQUAL "NOTFOUND")
    message(FATAL_ERROR "Invalid JSON or missing schema_version: ${json_error}")
endif()

string(JSON workload GET "${output}" workload)
if(NOT workload STREQUAL "hash_lookup")
    message(FATAL_ERROR "Expected hash_lookup workload, got ${workload}")
endif()

string(JSON iterations GET "${output}" iterations)
if(NOT iterations EQUAL 1000)
    message(FATAL_ERROR "Expected 1000 iterations, got ${iterations}")
endif()

string(JSON elapsed_ns ERROR_VARIABLE duration_error
    GET "${output}" elapsed_ns)
if(NOT duration_error STREQUAL "NOTFOUND")
    message(FATAL_ERROR "Missing or invalid elapsed_ns: ${duration_error}")
endif()
if(NOT elapsed_ns MATCHES "^[0-9]+$" OR elapsed_ns LESS_EQUAL 0)
    message(FATAL_ERROR "elapsed_ns must be positive, got ${elapsed_ns}")
endif()

string(JSON checksum ERROR_VARIABLE checksum_error GET "${output}" checksum)
if(NOT checksum_error STREQUAL "NOTFOUND")
    message(FATAL_ERROR "Missing or invalid checksum: ${checksum_error}")
endif()

# Sum of (key * 17 + 3) for keys 0..999, for 1000 iterations.
if(NOT checksum EQUAL 8494500)
    message(FATAL_ERROR "Unexpected checksum for 1000 iterations: ${checksum}")
endif()

function(expect_rejected label)
    execute_process(
        COMMAND "${LOOKUP_EXE}" ${ARGN}
        RESULT_VARIABLE invalid_result
        OUTPUT_QUIET
        ERROR_QUIET
        TIMEOUT 5
    )
    if(invalid_result EQUAL 0)
        message(FATAL_ERROR "Native lookup accepted invalid ${label} arguments")
    endif()
endfunction()

expect_rejected("negative integer" --iterations -1)
expect_rejected("malformed integer" --iterations 10junk)
expect_rejected("iteration limit" --iterations 100000001)
expect_rejected("extra-work limit" --extra-work 100001)
expect_rejected("delay limit" --delay-ns 5000000001)
expect_rejected("operation-volume limit"
    --iterations 100000000 --extra-work 10)

message(STATUS
    "Native correctness test passed; checksum=${checksum}; elapsed_ns=${elapsed_ns}")
