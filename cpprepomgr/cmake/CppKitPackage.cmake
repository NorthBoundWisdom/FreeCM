include(CMakeParseArguments)

function(cppkit_qt_bin_dir out_var)
    set(_qt_bin_dir "")
    if(DEFINED QT_BIN_DIR AND EXISTS "${QT_BIN_DIR}")
        set(_qt_bin_dir "${QT_BIN_DIR}")
    elseif(TARGET Qt6::qmake)
        get_target_property(_qmake_location Qt6::qmake IMPORTED_LOCATION)
        get_filename_component(_qt_bin_dir "${_qmake_location}" DIRECTORY)
    elseif(TARGET Qt5::qmake)
        get_target_property(_qmake_location Qt5::qmake IMPORTED_LOCATION)
        get_filename_component(_qt_bin_dir "${_qmake_location}" DIRECTORY)
    endif()
    if(NOT _qt_bin_dir)
        message(FATAL_ERROR "cppkit_qt_bin_dir: Qt qmake target or QT_BIN_DIR is required")
    endif()
    set("${out_var}" "${_qt_bin_dir}" PARENT_SCOPE)
endfunction()

function(cppkit_add_package_deploy target_name)
    cmake_parse_arguments(
        CPPKIT_PACKAGE
        ""
        "CONFIG;PLATFORM;TARGET_NAME"
        "DEPENDS"
        ${ARGN}
    )

    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_add_package_deploy: target does not exist: ${target_name}")
    endif()
    if(NOT CPPKIT_PACKAGE_CONFIG)
        message(FATAL_ERROR "cppkit_add_package_deploy: CONFIG is required")
    endif()
    if(NOT CPPKIT_PACKAGE_PLATFORM)
        if(WIN32)
            set(CPPKIT_PACKAGE_PLATFORM "win")
        elseif(APPLE)
            set(CPPKIT_PACKAGE_PLATFORM "mac")
        elseif(UNIX)
            set(CPPKIT_PACKAGE_PLATFORM "linux")
        else()
            message(FATAL_ERROR "cppkit_add_package_deploy: unsupported platform")
        endif()
    endif()
    if(NOT CPPKIT_PACKAGE_TARGET_NAME)
        set(CPPKIT_PACKAGE_TARGET_NAME "AppDeploy")
    endif()

    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    set(_module_command "")
    if(CPPKIT_PACKAGE_PLATFORM STREQUAL "win")
        set(_module_command "deploy-win")
    elseif(CPPKIT_PACKAGE_PLATFORM STREQUAL "mac")
        set(_module_command "deploy-mac")
    elseif(CPPKIT_PACKAGE_PLATFORM STREQUAL "linux")
        set(_module_command "deploy-linux")
    else()
        message(FATAL_ERROR "cppkit_add_package_deploy: unsupported platform: ${CPPKIT_PACKAGE_PLATFORM}")
    endif()

    add_custom_target("${CPPKIT_PACKAGE_TARGET_NAME}"
        COMMAND "${CMAKE_COMMAND}" -E env "PYTHONPATH=${CMAKE_SOURCE_DIR}/FreeCM"
            "${Python3_EXECUTABLE}" -m cpprepomgr.package.cli "${_module_command}"
                --config "${CPPKIT_PACKAGE_CONFIG}"
        DEPENDS "${target_name}" ${CPPKIT_PACKAGE_DEPENDS}
        COMMENT "Deploying ${target_name} package payload"
        VERBATIM
        USES_TERMINAL
    )
    set_target_properties("${CPPKIT_PACKAGE_TARGET_NAME}" PROPERTIES FOLDER "Deployment")
endfunction()

function(cppkit_json_escape input output_var)
    set(_value "${input}")
    string(REPLACE "\\" "\\\\" _value "${_value}")
    string(REPLACE "\"" "\\\"" _value "${_value}")
    string(REPLACE "\n" "\\n" _value "${_value}")
    string(REPLACE "\r" "\\r" _value "${_value}")
    set("${output_var}" "${_value}" PARENT_SCOPE)
endfunction()

function(cppkit_json_string_array output_var)
    set(_json "[")
    set(_first TRUE)
    foreach(_list_name IN LISTS ARGN)
        foreach(_item IN LISTS ${_list_name})
            if(NOT _first)
                string(APPEND _json ", ")
            endif()
            cppkit_json_escape("${_item}" _escaped_item)
            string(APPEND _json "\"${_escaped_item}\"")
            set(_first FALSE)
        endforeach()
    endforeach()
    string(APPEND _json "]")
    set("${output_var}" "${_json}" PARENT_SCOPE)
endfunction()
