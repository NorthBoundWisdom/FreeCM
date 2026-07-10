include(CMakeParseArguments)

function(_cppkit_qt_bin_dir out_var)
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
    set(${out_var} "${_qt_bin_dir}" PARENT_SCOPE)
endfunction()

function(cppkit_deploy_qt_dependencies target_name)
    cmake_parse_arguments(
        CPPKIT_DEPLOY
        "OPTIONAL_TOOL;POST_BUILD"
        "QML_DIR;QT_BIN_DIR"
        "COPY_DIRECTORIES;WINDEPLOYQT_ARGS;MACDEPLOYQT_ARGS;LINUXDEPLOYQT_ARGS"
        ${ARGN}
    )

    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_deploy_qt_dependencies: target does not exist: ${target_name}")
    endif()

    if(NOT CPPKIT_DEPLOY_QML_DIR)
        set(CPPKIT_DEPLOY_QML_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
    endif()

    foreach(_dir IN LISTS CPPKIT_DEPLOY_COPY_DIRECTORIES)
        if(NOT EXISTS "${_dir}")
            message(FATAL_ERROR "cppkit_deploy_qt_dependencies: copy directory does not exist: ${_dir}")
        endif()
        add_custom_command(TARGET "${target_name}" POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_directory
                "${_dir}"
                "$<TARGET_FILE_DIR:${target_name}>"
            COMMENT "Copying ${_dir} next to ${target_name}"
        )
    endforeach()

    if(CPPKIT_DEPLOY_QT_BIN_DIR)
        set(_qt_bin_dir "${CPPKIT_DEPLOY_QT_BIN_DIR}")
    else()
        _cppkit_qt_bin_dir(_qt_bin_dir)
    endif()

    if(WIN32)
        set(_deploy_tool_name windeployqt)
    elseif(APPLE)
        set(_deploy_tool_name macdeployqt)
    elseif(UNIX)
        set(_deploy_tool_name linuxdeployqt)
    else()
        message(FATAL_ERROR "cppkit_deploy_qt_dependencies: unsupported platform")
    endif()

    _cppkit_resolve_deploy_tool(
        _deploy_tool "${_deploy_tool_name}" "${_qt_bin_dir}"
        "${CPPKIT_DEPLOY_OPTIONAL_TOOL}" "${target_name}"
    )
    if(NOT _deploy_tool)
        return()
    endif()

    if(WIN32)
        _cppkit_deploy_qt_windows("${target_name}" "${CPPKIT_DEPLOY_QML_DIR}" "${_deploy_tool}" "${CPPKIT_DEPLOY_POST_BUILD}" "${CPPKIT_DEPLOY_WINDEPLOYQT_ARGS}")
    elseif(APPLE)
        _cppkit_deploy_qt_macos("${target_name}" "${CPPKIT_DEPLOY_QML_DIR}" "${_deploy_tool}" "${CPPKIT_DEPLOY_POST_BUILD}" "${CPPKIT_DEPLOY_MACDEPLOYQT_ARGS}")
    else()
        _cppkit_deploy_qt_linux("${target_name}" "${CPPKIT_DEPLOY_QML_DIR}" "${_deploy_tool}" "${CPPKIT_DEPLOY_POST_BUILD}" "${CPPKIT_DEPLOY_LINUXDEPLOYQT_ARGS}")
    endif()
endfunction()

function(_cppkit_resolve_deploy_tool out_var tool_name qt_bin_dir optional_tool target_name)
    _cppkit_find_deploy_tool(_deploy_tool "${tool_name}" "${qt_bin_dir}")
    if(_deploy_tool)
        set(${out_var} "${_deploy_tool}" PARENT_SCOPE)
        return()
    endif()

    if(optional_tool)
        message(STATUS
            "cppkit_deploy_qt_dependencies(${target_name}) skipped explicitly: "
            "${tool_name} was not found (OPTIONAL_TOOL)."
        )
        set(${out_var} "" PARENT_SCOPE)
        return()
    endif()

    message(FATAL_ERROR
        "cppkit_deploy_qt_dependencies: ${tool_name} was not found; "
        "install the Qt deployment tool or pass OPTIONAL_TOOL to skip explicitly"
    )
endfunction()

function(_cppkit_find_deploy_tool out_var tool_name qt_bin_dir)
    find_program(_cppkit_deploy_tool
        NAMES "${tool_name}"
        HINTS "${qt_bin_dir}"
    )
    set(${out_var} "${_cppkit_deploy_tool}" PARENT_SCOPE)
    unset(_cppkit_deploy_tool CACHE)
endfunction()

function(_cppkit_deploy_qt_windows target_name qml_dir deploy_tool post_build extra_args)
    set(_command
        "${deploy_tool}"
        --verbose 0
        --qmldir "${qml_dir}"
        --no-translations
        --compiler-runtime
        ${extra_args}
        "$<TARGET_FILE:${target_name}>"
    )

    if(post_build)
        add_custom_command(TARGET "${target_name}" POST_BUILD
            COMMAND ${_command}
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
    else()
        add_custom_target("Deploy_Qt_${target_name}"
            COMMAND ${_command}
            DEPENDS "${target_name}"
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
        set_target_properties("Deploy_Qt_${target_name}" PROPERTIES FOLDER "Deployment")
    endif()
endfunction()

function(_cppkit_deploy_qt_macos target_name qml_dir deploy_tool post_build extra_args)
    set(_command
        "${deploy_tool}"
        "$<TARGET_BUNDLE_DIR:${target_name}>"
        -qmldir="${qml_dir}"
        ${extra_args}
    )

    if(post_build)
        add_custom_command(TARGET "${target_name}" POST_BUILD
            COMMAND ${_command}
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
    else()
        add_custom_target("Deploy_Qt_${target_name}"
            COMMAND ${_command}
            DEPENDS "${target_name}"
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
        set_target_properties("Deploy_Qt_${target_name}" PROPERTIES FOLDER "Deployment")
    endif()
endfunction()

function(_cppkit_deploy_qt_linux target_name qml_dir deploy_tool post_build extra_args)
    set(_command
        "${deploy_tool}"
        "$<TARGET_FILE:${target_name}>"
        -qmldir="${qml_dir}"
        ${extra_args}
    )

    if(post_build)
        add_custom_command(TARGET "${target_name}" POST_BUILD
            COMMAND ${_command}
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
    else()
        add_custom_target("Deploy_Qt_${target_name}"
            COMMAND ${_command}
            DEPENDS "${target_name}"
            COMMENT "Deploying Qt runtime for ${target_name}"
            VERBATIM
        )
        set_target_properties("Deploy_Qt_${target_name}" PROPERTIES FOLDER "Deployment")
    endif()
endfunction()
