include(CMakeParseArguments)

function(_cppkit_coverage_enabled out_var)
    set(_enabled OFF)
    if(DEFINED CPPKIT_ADD_COVERAGE)
        set(_enabled "${CPPKIT_ADD_COVERAGE}")
    elseif(DEFINED ADD_COVERAGE)
        set(_enabled "${ADD_COVERAGE}")
    endif()
    set(${out_var} "${_enabled}" PARENT_SCOPE)
endfunction()

function(cppkit_add_coverage_compile_options target_name)
    _cppkit_coverage_enabled(_cppkit_enabled)
    if(NOT _cppkit_enabled OR WIN32 OR APPLE)
        return()
    endif()

    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_add_coverage_compile_options: target does not exist: ${target_name}")
    endif()

    get_target_property(_options_applied "${target_name}" CPPKIT_COVERAGE_OPTIONS_APPLIED)
    if(_options_applied)
        return()
    endif()

    get_target_property(_target_type "${target_name}" TYPE)
    set(_is_object_target OFF)
    if(_target_type STREQUAL "OBJECT_LIBRARY")
        set(_is_object_target ON)
    endif()

    if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        target_compile_options("${target_name}" PRIVATE --coverage -O0 -fno-inline -fprofile-arcs -ftest-coverage)
        if(NOT _is_object_target)
            target_link_options("${target_name}" PRIVATE --coverage -lgcov)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        target_compile_options("${target_name}" PRIVATE -fprofile-instr-generate -fcoverage-mapping -O0 -fno-inline)
        if(NOT _is_object_target)
            target_link_options("${target_name}" PRIVATE -fprofile-instr-generate -fcoverage-mapping)
        endif()
    else()
        message(FATAL_ERROR "Coverage is not supported for compiler: ${CMAKE_CXX_COMPILER_ID}")
    endif()
    set_property(TARGET "${target_name}" PROPERTY CPPKIT_COVERAGE_OPTIONS_APPLIED TRUE)
endfunction()

function(cppkit_add_coverage target_name)
    _cppkit_coverage_enabled(_cppkit_enabled)
    if(NOT _cppkit_enabled)
        return()
    endif()
    if(WIN32 OR APPLE)
        message(FATAL_ERROR "Coverage is only supported on Linux for target ${target_name}.")
    endif()
    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_add_coverage: target does not exist: ${target_name}")
    endif()

    cmake_parse_arguments(
        CPPKIT_COVERAGE
        ""
        ""
        "COVERAGE_DIRS;COVERAGE_FILES"
        ${ARGN}
    )

    cppkit_add_coverage_compile_options("${target_name}")

    if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        _cppkit_add_gcc_coverage("${target_name}"
            COVERAGE_DIRS ${CPPKIT_COVERAGE_COVERAGE_DIRS}
            COVERAGE_FILES ${CPPKIT_COVERAGE_COVERAGE_FILES}
        )
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        _cppkit_add_clang_coverage("${target_name}"
            COVERAGE_DIRS ${CPPKIT_COVERAGE_COVERAGE_DIRS}
            COVERAGE_FILES ${CPPKIT_COVERAGE_COVERAGE_FILES}
        )
    else()
        message(FATAL_ERROR "Coverage is not supported for compiler: ${CMAKE_CXX_COMPILER_ID}")
    endif()
endfunction()

function(_cppkit_add_gcc_coverage target_name)
    cmake_parse_arguments(
        CPPKIT_COVERAGE
        ""
        ""
        "COVERAGE_DIRS;COVERAGE_FILES"
        ${ARGN}
    )

    find_program(LCOV_PATH lcov REQUIRED)
    find_program(GENHTML_PATH genhtml REQUIRED)

    set(_coverage_target "Coverage_${target_name}")
    set(_filtered_info "${_coverage_target}_filtered.info")

    set(_filter_commands "")
    set(_temporary_info_files "")
    set(_combine_args "")
    if(CPPKIT_COVERAGE_COVERAGE_FILES)
        set(_filter_index 0)
        foreach(_file IN LISTS CPPKIT_COVERAGE_COVERAGE_FILES)
            math(EXPR _filter_index "${_filter_index} + 1")
            get_filename_component(_file_name "${_file}" NAME_WE)
            string(MAKE_C_IDENTIFIER "${_file_name}" _safe_file_name)
            string(SHA256 _file_hash "${_file}")
            string(SUBSTRING "${_file_hash}" 0 12 _short_file_hash)
            set(_temporary_info
                "${_coverage_target}_${_filter_index}_${_safe_file_name}_${_short_file_hash}_temp.info"
            )
            list(APPEND _filter_commands
                COMMAND "${LCOV_PATH}" -e "${_coverage_target}.info" "${_file}"
                        -o "${_temporary_info}" --rc branch_coverage=1 --ignore-errors unused
            )
            list(APPEND _temporary_info_files "${_temporary_info}")
            list(APPEND _combine_args -a "${_temporary_info}")
        endforeach()
        list(APPEND _filter_commands
            COMMAND "${LCOV_PATH}" ${_combine_args} -o "${_filtered_info}" --rc branch_coverage=1
            COMMAND ${CMAKE_COMMAND} -E rm -f ${_temporary_info_files}
        )
    elseif(CPPKIT_COVERAGE_COVERAGE_DIRS)
        set(_filter_index 0)
        foreach(_dir IN LISTS CPPKIT_COVERAGE_COVERAGE_DIRS)
            math(EXPR _filter_index "${_filter_index} + 1")
            string(MAKE_C_IDENTIFIER "${_dir}" _safe_dir_name)
            string(SHA256 _dir_hash "${_dir}")
            string(SUBSTRING "${_dir_hash}" 0 12 _short_dir_hash)
            set(_temporary_info
                "${_coverage_target}_${_filter_index}_${_safe_dir_name}_${_short_dir_hash}_temp.info"
            )
            list(APPEND _filter_commands
                COMMAND "${LCOV_PATH}" -e "${_coverage_target}.info" "${CMAKE_SOURCE_DIR}/${_dir}/*"
                        -o "${_temporary_info}" --rc branch_coverage=1 --ignore-errors unused
            )
            list(APPEND _temporary_info_files "${_temporary_info}")
            list(APPEND _combine_args -a "${_temporary_info}")
        endforeach()
        set(_combined_info "${_coverage_target}_combined.info")
        list(APPEND _filter_commands
            COMMAND "${LCOV_PATH}" ${_combine_args} -o "${_combined_info}" --rc branch_coverage=1
            COMMAND "${LCOV_PATH}" -r "${_combined_info}" "/usr/include/*" -o "${_filtered_info}" --rc branch_coverage=1 --ignore-errors unused
            COMMAND ${CMAKE_COMMAND} -E rm -f ${_temporary_info_files} "${_combined_info}"
        )
    else()
        list(APPEND _filter_commands
            COMMAND "${LCOV_PATH}" -r "${_coverage_target}.info" "/usr/include/*" "*/thirdparty/*"
                    -o "${_filtered_info}" --rc branch_coverage=1 --ignore-errors unused
        )
    endif()

    add_custom_target("${_coverage_target}"
        COMMAND "${LCOV_PATH}" -d . --zerocounters
        COMMAND "$<TARGET_FILE:${target_name}>"
        COMMAND "${LCOV_PATH}" -d . --capture -o "${_coverage_target}.info"
                --ignore-errors inconsistent,usage,version,mismatch --rc branch_coverage=1
        ${_filter_commands}
        COMMAND "${GENHTML_PATH}" -o "${_coverage_target}" "${_filtered_info}" --legend
                --ignore-errors inconsistent --branch-coverage --rc branch_coverage=1
        COMMAND ${CMAKE_COMMAND} -E rm -f "${_coverage_target}.info" "${_filtered_info}"
        WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
        COMMENT "Running GCC coverage for ${target_name}"
    )
    add_dependencies("${_coverage_target}" "${target_name}")
    set_target_properties("${_coverage_target}" PROPERTIES FOLDER "Testing/Coverage")
endfunction()

function(_cppkit_add_clang_coverage target_name)
    cmake_parse_arguments(
        CPPKIT_COVERAGE
        ""
        ""
        "COVERAGE_DIRS;COVERAGE_FILES"
        ${ARGN}
    )

    find_program(LLVM_COV_PATH llvm-cov REQUIRED)
    find_program(LLVM_PROFDATA_PATH llvm-profdata REQUIRED)

    set(_filter_args "")

    if(CPPKIT_COVERAGE_COVERAGE_FILES)
        list(APPEND _filter_args --sources ${CPPKIT_COVERAGE_COVERAGE_FILES})
    elseif(CPPKIT_COVERAGE_COVERAGE_DIRS)
        set(_sources "")
        foreach(_dir IN LISTS CPPKIT_COVERAGE_COVERAGE_DIRS)
            file(GLOB_RECURSE _dir_sources
                "${CMAKE_SOURCE_DIR}/${_dir}/*.c"
                "${CMAKE_SOURCE_DIR}/${_dir}/*.cc"
                "${CMAKE_SOURCE_DIR}/${_dir}/*.cpp"
                "${CMAKE_SOURCE_DIR}/${_dir}/*.h"
                "${CMAKE_SOURCE_DIR}/${_dir}/*.hpp"
            )
            list(APPEND _sources ${_dir_sources})
        endforeach()
        if(_sources)
            list(APPEND _filter_args --sources ${_sources})
        endif()
    else()
        list(APPEND _filter_args
            --ignore-filename-regex=/usr/include/.*
            --ignore-filename-regex=.*/thirdparty/.*
        )
    endif()

    set(_coverage_target "Coverage_${target_name}")
    add_custom_target("${_coverage_target}"
        COMMAND ${CMAKE_COMMAND} -E rm -f "${target_name}.profraw"
        COMMAND ${CMAKE_COMMAND} -E env "LLVM_PROFILE_FILE=${target_name}.profraw" "$<TARGET_FILE:${target_name}>"
        COMMAND "${LLVM_PROFDATA_PATH}" merge -sparse "${target_name}.profraw" -o "${target_name}.profdata"
        COMMAND "${LLVM_COV_PATH}" show "$<TARGET_FILE:${target_name}>" "-instr-profile=${target_name}.profdata"
                -format=html -output-dir="${_coverage_target}"
                --show-branches=percent --show-line-counts --show-regions --show-instantiations
                --show-expansions --tab-size=4 --coverage-watermark=85,50 ${_filter_args}
        COMMAND "${LLVM_COV_PATH}" report "$<TARGET_FILE:${target_name}>" "-instr-profile=${target_name}.profdata"
                --show-branch-summary --show-region-summary --show-instantiation-summary ${_filter_args}
        COMMAND ${CMAKE_COMMAND} -E rm -f "${target_name}.profraw" "${target_name}.profdata"
        WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
        COMMENT "Running Clang coverage for ${target_name}"
    )
    add_dependencies("${_coverage_target}" "${target_name}")
    set_target_properties("${_coverage_target}" PROPERTIES FOLDER "Testing/Coverage")
endfunction()
