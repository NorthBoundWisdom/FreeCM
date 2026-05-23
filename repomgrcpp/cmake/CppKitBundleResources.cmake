include(CMakeParseArguments)

function(cppkit_add_bundle_icons out_var)
    cmake_parse_arguments(
        CPPKIT_ICON
        ""
        "OUTFILE_BASENAME"
        "ICONS"
        ${ARGN}
    )

    if(NOT CPPKIT_ICON_ICONS)
        message(FATAL_ERROR "cppkit_add_bundle_icons: ICONS is required")
    endif()

    set(_sources "${${out_var}}")
    foreach(_icon IN LISTS CPPKIT_ICON_ICONS)
        get_filename_component(_icon_abs "${_icon}" ABSOLUTE)
        if(NOT EXISTS "${_icon_abs}")
            message(FATAL_ERROR "cppkit_add_bundle_icons: icon does not exist: ${_icon_abs}")
        endif()

        if(NOT APPLE)
            continue()
        endif()

        get_filename_component(_icon_ext "${_icon_abs}" EXT)
        if(NOT _icon_ext STREQUAL ".icns")
            continue()
        endif()

        if(CPPKIT_ICON_OUTFILE_BASENAME)
            set(_bundle_icon_name "${CPPKIT_ICON_OUTFILE_BASENAME}.icns")
        else()
            get_filename_component(_bundle_icon_name "${_icon_abs}" NAME)
        endif()

        set(_bundle_icon "${CMAKE_BINARY_DIR}/${_bundle_icon_name}")
        configure_file("${_icon_abs}" "${_bundle_icon}" COPYONLY)
        set_source_files_properties("${_bundle_icon}" PROPERTIES MACOSX_PACKAGE_LOCATION Resources)
        set(MACOSX_BUNDLE_ICON_FILE "${_bundle_icon_name}" PARENT_SCOPE)
        list(APPEND _sources "${_bundle_icon}")
    endforeach()

    set(${out_var} "${_sources}" PARENT_SCOPE)
endfunction()
