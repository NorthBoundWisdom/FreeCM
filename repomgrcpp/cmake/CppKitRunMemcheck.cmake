if(NOT DEFINED TARGET_EXECUTABLE OR TARGET_EXECUTABLE STREQUAL "")
    message(FATAL_ERROR "CppKitRunMemcheck.cmake requires -DTARGET_EXECUTABLE=<path>")
endif()

if(NOT DEFINED REPORT_PATH OR REPORT_PATH STREQUAL "")
    set(REPORT_PATH "${CMAKE_CURRENT_BINARY_DIR}/memcheck")
endif()

if(NOT DEFINED TARGET_NAME OR TARGET_NAME STREQUAL "")
    get_filename_component(TARGET_NAME "${TARGET_EXECUTABLE}" NAME_WE)
endif()

file(MAKE_DIRECTORY "${REPORT_PATH}")
string(TIMESTAMP _timestamp "%Y%m%d-%H%M%S")
set(_log_file "${REPORT_PATH}/${TARGET_NAME}-${_timestamp}.log")

if(DEFINED VALGRIND_EXECUTABLE AND NOT VALGRIND_EXECUTABLE STREQUAL "")
    execute_process(
        COMMAND "${VALGRIND_EXECUTABLE}"
                --leak-check=full
                --show-leak-kinds=all
                --error-exitcode=1
                "${TARGET_EXECUTABLE}"
        RESULT_VARIABLE _memcheck_result
        OUTPUT_FILE "${_log_file}"
        ERROR_FILE "${_log_file}"
    )
else()
    execute_process(
        COMMAND "${TARGET_EXECUTABLE}"
        RESULT_VARIABLE _memcheck_result
        OUTPUT_FILE "${_log_file}"
        ERROR_FILE "${_log_file}"
    )
endif()

if(NOT _memcheck_result EQUAL 0)
    message(FATAL_ERROR "Memory check failed for ${TARGET_NAME}. See ${_log_file}")
endif()

message(STATUS "Memory check passed for ${TARGET_NAME}. Log: ${_log_file}")
