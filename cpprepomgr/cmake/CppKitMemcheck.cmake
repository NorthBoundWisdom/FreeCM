include(CMakeParseArguments)

function(cppkit_add_memcheck target_name)
    cmake_parse_arguments(
        CPPKIT_MEMCHECK
        "ADD_SANITIZER_FLAGS"
        "REPORT_DIR;SUPPRESSION_FILE;RUN_SCRIPT"
        ""
        ${ARGN}
    )

    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_add_memcheck: target does not exist: ${target_name}")
    endif()

    if(WIN32 OR APPLE)
        message(STATUS "cppkit_add_memcheck(${target_name}) skipped: only supported on Linux.")
        return()
    endif()

    if(NOT CPPKIT_MEMCHECK_REPORT_DIR)
        set(CPPKIT_MEMCHECK_REPORT_DIR "${CMAKE_BINARY_DIR}/memcheck")
    endif()
    if(NOT CPPKIT_MEMCHECK_RUN_SCRIPT)
        set(CPPKIT_MEMCHECK_RUN_SCRIPT "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/CppKitRunMemcheck.cmake")
    endif()

    if(CPPKIT_MEMCHECK_ADD_SANITIZER_FLAGS)
        target_compile_options("${target_name}" PRIVATE -fsanitize=address -fno-omit-frame-pointer)
        target_link_options("${target_name}" PRIVATE -fsanitize=address)
    endif()

    find_program(CPPKIT_VALGRIND_EXECUTABLE valgrind)
    if(NOT CPPKIT_VALGRIND_EXECUTABLE)
        set(CPPKIT_VALGRIND_EXECUTABLE "")
    endif()
    set(_env_args "ASAN_OPTIONS=detect_leaks=1:symbolize=1:detect_container_overflow=1")
    if(CPPKIT_MEMCHECK_SUPPRESSION_FILE)
        list(APPEND _env_args "LSAN_OPTIONS=suppressions=${CPPKIT_MEMCHECK_SUPPRESSION_FILE}")
    endif()

    add_custom_target(Memcheck_${target_name}
        COMMAND ${CMAKE_COMMAND} -E make_directory "${CPPKIT_MEMCHECK_REPORT_DIR}"
        COMMAND ${CMAKE_COMMAND} -E env
            ${_env_args}
            ${CMAKE_COMMAND}
                -DTARGET_NAME=${target_name}
                -DTARGET_EXECUTABLE=$<TARGET_FILE:${target_name}>
                -DREPORT_PATH=${CPPKIT_MEMCHECK_REPORT_DIR}
                -DVALGRIND_EXECUTABLE=${CPPKIT_VALGRIND_EXECUTABLE}
                -P "${CPPKIT_MEMCHECK_RUN_SCRIPT}"
        DEPENDS "${target_name}"
        WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
        COMMENT "Running memory check for ${target_name}"
        USES_TERMINAL
    )
    set_target_properties(Memcheck_${target_name} PROPERTIES FOLDER "Testing")
endfunction()
