include(CMakeParseArguments)
include("${CMAKE_CURRENT_LIST_DIR}/CppKitCoverage.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/CppKitMemcheck.cmake")
include("${CMAKE_CURRENT_LIST_DIR}/CppKitCompilerFlags.cmake")

function(cppkit_add_executable target_name)
    cmake_parse_arguments(
        PARSE_ARGV 1
        CPPKIT_EXE
        "IS_TEST;ENABLE_MEMCHECK;ENABLE_QT_AUTOGEN;LINK_WHAT_YOU_USE"
        "TEST_WORKING_DIRECTORY;MEMCHECK_SUPPRESSION_FILE"
        "COVERAGE_DIRS;COVERAGE_FILES;LINK_LIBRARIES"
    )

    add_executable("${target_name}" ${CPPKIT_EXE_UNPARSED_ARGUMENTS})

    if(CPPKIT_EXE_LINK_LIBRARIES)
        target_link_libraries("${target_name}" PRIVATE ${CPPKIT_EXE_LINK_LIBRARIES})
    endif()

    if(CPPKIT_EXE_LINK_WHAT_YOU_USE)
        cppkit_enable_link_what_you_use("${target_name}")
    endif()

    if(CPPKIT_EXE_ENABLE_MEMCHECK)
        cppkit_add_memcheck("${target_name}" SUPPRESSION_FILE "${CPPKIT_EXE_MEMCHECK_SUPPRESSION_FILE}")
    endif()

    if(CPPKIT_EXE_IS_TEST)
        enable_testing()
        set_target_properties("${target_name}" PROPERTIES FOLDER "Testing/Binaries")
        if(NOT CPPKIT_EXE_ENABLE_QT_AUTOGEN)
            set_target_properties("${target_name}" PROPERTIES
                AUTOMOC OFF
                AUTOUIC OFF
                AUTORCC OFF
            )
        endif()

        cppkit_add_coverage("${target_name}"
            COVERAGE_DIRS ${CPPKIT_EXE_COVERAGE_DIRS}
            COVERAGE_FILES ${CPPKIT_EXE_COVERAGE_FILES}
        )

        if(NOT CPPKIT_EXE_TEST_WORKING_DIRECTORY)
            set(CPPKIT_EXE_TEST_WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")
        endif()

        add_test(NAME "${target_name}" COMMAND "$<TARGET_FILE:${target_name}>")
        set_tests_properties("${target_name}" PROPERTIES WORKING_DIRECTORY "${CPPKIT_EXE_TEST_WORKING_DIRECTORY}")

        set(_run_target "Run_${target_name}")
        add_custom_target("${_run_target}"
            COMMAND "$<TARGET_FILE:${target_name}>"
            DEPENDS "${target_name}"
            WORKING_DIRECTORY "${CPPKIT_EXE_TEST_WORKING_DIRECTORY}"
            COMMENT "Running test executable ${target_name}"
            USES_TERMINAL
        )
        set_target_properties("${_run_target}" PROPERTIES FOLDER "Testing")

        if(NOT TARGET CppKitUnitTests)
            add_custom_target(CppKitUnitTests COMMENT "Build and run all CppKit unit test executables")
            set_target_properties(CppKitUnitTests PROPERTIES FOLDER "Testing")
        endif()
        add_dependencies(CppKitUnitTests "${_run_target}")
    endif()
endfunction()
