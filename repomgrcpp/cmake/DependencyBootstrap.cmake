function(cppkit_json_escape input output_var)
    set(_value "${input}")
    string(REPLACE "\\" "\\\\" _value "${_value}")
    string(REPLACE "\"" "\\\"" _value "${_value}")
    string(REPLACE "\n" "\\n" _value "${_value}")
    string(REPLACE "\r" "\\r" _value "${_value}")
    set(${output_var} "${_value}" PARENT_SCOPE)
endfunction()

function(cppkit_json_array output_var)
    set(_json "[")
    set(_first TRUE)
    foreach(_item ${ARGN})
        if(NOT _first)
            string(APPEND _json ", ")
        endif()
        cppkit_json_escape("${_item}" _escaped_item)
        string(APPEND _json "\"${_escaped_item}\"")
        set(_first FALSE)
    endforeach()
    string(APPEND _json "]")
    set(${output_var} "${_json}" PARENT_SCOPE)
endfunction()

function(cppkit_json_object_from_cache_vars output_var)
    set(_json "{")
    set(_first TRUE)
    foreach(_key ${ARGN})
        if(DEFINED ${_key})
            set(_value "${${_key}}")
        else()
            set(_value "")
        endif()
        if(NOT _first)
            string(APPEND _json ", ")
        endif()
        cppkit_json_escape("${_key}" _escaped_key)
        cppkit_json_escape("${_value}" _escaped_value)
        string(APPEND _json "\"${_escaped_key}\": \"${_escaped_value}\"")
        set(_first FALSE)
    endforeach()
    string(APPEND _json "}")
    set(${output_var} "${_json}" PARENT_SCOPE)
endfunction()

function(cppkit_collect_external_prefix_path output_var preset_name)
    if(DEFINED CPPKIT_DEPSMGR_MANAGED_PREFIX_ROOT)
        set(_managed_prefix_root "${CPPKIT_DEPSMGR_MANAGED_PREFIX_ROOT}")
    else()
        set(_managed_prefix_root "${CMAKE_SOURCE_DIR}/build/${preset_name}/dependency_installs/")
    endif()

    set(_external_prefixes)
    foreach(_prefix IN LISTS CMAKE_PREFIX_PATH)
        string(FIND "${_prefix}" "${_managed_prefix_root}" _prefix_match)
        if(_prefix_match EQUAL 0)
            continue()
        endif()
        list(APPEND _external_prefixes "${_prefix}")
    endforeach()
    list(JOIN _external_prefixes ";" _joined_prefixes)
    set(${output_var} "${_joined_prefixes}" PARENT_SCOPE)
endfunction()

function(cppkit_build_configurations_json output_var)
    if(CMAKE_CONFIGURATION_TYPES)
        cppkit_json_array(_configurations_json ${CMAKE_CONFIGURATION_TYPES})
        set(${output_var} "${_configurations_json}" PARENT_SCOPE)
        return()
    endif()

    if(CMAKE_BUILD_TYPE)
        cppkit_json_array(_configurations_json "${CMAKE_BUILD_TYPE}")
        set(${output_var} "${_configurations_json}" PARENT_SCOPE)
        return()
    endif()

    cppkit_json_array(_configurations_json "Release")
    set(${output_var} "${_configurations_json}" PARENT_SCOPE)
endfunction()

function(cppkit_write_dependency_build_context output_path)
    get_filename_component(_preset_name "${CMAKE_BINARY_DIR}" NAME)
    cppkit_collect_external_prefix_path(_external_prefix_path "${_preset_name}")
    cppkit_build_configurations_json(_build_configurations_json)

    if(NOT DEFINED CPPKIT_DEPSMGR_CACHE_VARIABLE_KEYS)
        set(CPPKIT_DEPSMGR_CACHE_VARIABLE_KEYS
            CMAKE_C_COMPILER
            CMAKE_CXX_COMPILER
            CMAKE_C_COMPILER_LAUNCHER
            CMAKE_CXX_COMPILER_LAUNCHER
            CMAKE_OSX_SYSROOT
            CMAKE_MAKE_PROGRAM
            CMAKE_TOOLCHAIN_FILE
            CMAKE_BUILD_TYPE
            CMAKE_C_FLAGS
            CMAKE_CXX_FLAGS
            CMAKE_C_FLAGS_RELEASE
            CMAKE_CXX_FLAGS_RELEASE
            CMAKE_C_FLAGS_DEBUG
            CMAKE_CXX_FLAGS_DEBUG
        )
    endif()
    cppkit_json_object_from_cache_vars(
        _cache_variables_json
        ${CPPKIT_DEPSMGR_CACHE_VARIABLE_KEYS}
    )

    cppkit_json_escape("${_preset_name}" CPPKIT_DEPSMGR_PRESET_NAME)
    cppkit_json_escape("${CMAKE_GENERATOR}" CPPKIT_DEPSMGR_GENERATOR)
    cppkit_json_escape("${CMAKE_GENERATOR_PLATFORM}" CPPKIT_DEPSMGR_GENERATOR_PLATFORM)
    cppkit_json_escape("${CMAKE_GENERATOR_TOOLSET}" CPPKIT_DEPSMGR_GENERATOR_TOOLSET)
    cppkit_json_escape("${_external_prefix_path}" CPPKIT_DEPSMGR_EXTERNAL_PREFIX_PATH)
    set(CPPKIT_DEPSMGR_BUILD_CONFIGURATIONS_JSON "${_build_configurations_json}")
    set(CPPKIT_DEPSMGR_CACHE_VARIABLES_JSON "${_cache_variables_json}")

    if(DEFINED CPPKIT_DEPSMGR_CONTEXT_TEMPLATE)
        set(_context_template "${CPPKIT_DEPSMGR_CONTEXT_TEMPLATE}")
    else()
        set(_context_template "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/DependencyBuildContext.json.in")
    endif()
    if(NOT EXISTS "${_context_template}")
        message(FATAL_ERROR "CppKit dependency build context template not found: ${_context_template}")
    endif()

    configure_file("${_context_template}" "${output_path}" @ONLY)
endfunction()

function(cppkit_ensure_dependency_installs_for_current_preset)
    if(NOT PROJECT_IS_TOP_LEVEL)
        return()
    endif()

    find_package(Python3 REQUIRED COMPONENTS Interpreter)

    if(DEFINED CPPKIT_DEPSMGR_WORKFLOW_SCRIPT)
        set(_workflow_script "${CPPKIT_DEPSMGR_WORKFLOW_SCRIPT}")
    elseif(EXISTS "${CMAKE_SOURCE_DIR}/repomgrcpp/source_root_workflow.py")
        set(_workflow_script "${CMAKE_SOURCE_DIR}/repomgrcpp/source_root_workflow.py")
    else()
        set(_workflow_script "${CMAKE_SOURCE_DIR}/configs/source_root_workflow.py")
    endif()
    if(NOT EXISTS "${_workflow_script}")
        message(FATAL_ERROR "CppKit dependency workflow script not found: ${_workflow_script}")
    endif()

    if(DEFINED CPPKIT_DEPSMGR_CONTEXT_PATH)
        set(_context_path "${CPPKIT_DEPSMGR_CONTEXT_PATH}")
    else()
        set(_context_path "${CMAKE_BINARY_DIR}/cppkit_dependency_build_context.json")
    endif()
    cppkit_write_dependency_build_context("${_context_path}")

    execute_process(
        COMMAND
            "${Python3_EXECUTABLE}"
            "${_workflow_script}"
            "--build-dependencies-from-cmake"
            "${_context_path}"
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        RESULT_VARIABLE _bootstrap_result
        COMMAND_ECHO STDOUT
    )
    if(NOT _bootstrap_result EQUAL 0)
        message(FATAL_ERROR "Failed to build dependency SDKs for preset context: ${_context_path}")
    endif()
endfunction()
