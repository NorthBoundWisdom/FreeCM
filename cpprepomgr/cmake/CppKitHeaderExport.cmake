function(cppkit_register_exported_headers base_dir)
    get_property(_claimed_headers GLOBAL PROPERTY CPPKIT_CLAIMED_HEADER_SRCS)
    if(NOT _claimed_headers)
        set(_claimed_headers)
    endif()

    foreach(_header IN LISTS ARGN)
        get_filename_component(_src "${base_dir}/${_header}" ABSOLUTE)
        list(APPEND _claimed_headers "${_src}")
    endforeach()

    list(REMOVE_DUPLICATES _claimed_headers)
    set_property(GLOBAL PROPERTY CPPKIT_CLAIMED_HEADER_SRCS "${_claimed_headers}")
endfunction()

function(cppkit_export_headers_tree export_target base_dir output_dir)
    cppkit_register_exported_headers("${base_dir}" ${ARGN})

    set(_outputs)
    foreach(_header IN LISTS ARGN)
        set(_src "${base_dir}/${_header}")
        set(_dst "${output_dir}/${_header}")
        get_filename_component(_dst_dir "${_dst}" DIRECTORY)
        add_custom_command(
            OUTPUT "${_dst}"
            COMMAND ${CMAKE_COMMAND} -E make_directory "${_dst_dir}"
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_src}" "${_dst}"
            DEPENDS "${_src}"
            VERBATIM
        )
        list(APPEND _outputs "${_dst}")
    endforeach()

    add_custom_target("${export_target}" DEPENDS ${_outputs})
endfunction()

function(cppkit_export_headers_flat export_target base_dir output_dir)
    cppkit_register_exported_headers("${base_dir}" ${ARGN})

    set(_outputs)
    foreach(_header IN LISTS ARGN)
        get_filename_component(_header_name "${_header}" NAME)
        set(_src "${base_dir}/${_header}")
        set(_dst "${output_dir}/${_header_name}")
        add_custom_command(
            OUTPUT "${_dst}"
            COMMAND ${CMAKE_COMMAND} -E make_directory "${output_dir}"
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_src}" "${_dst}"
            DEPENDS "${_src}"
            VERBATIM
        )
        list(APPEND _outputs "${_dst}")
    endforeach()

    add_custom_target("${export_target}" DEPENDS ${_outputs})
endfunction()

function(cppkit_export_headers_tree_glob export_target base_dir output_dir)
    get_property(_claimed_headers GLOBAL PROPERTY CPPKIT_CLAIMED_HEADER_SRCS)
    if(NOT _claimed_headers)
        set(_claimed_headers)
    endif()

    file(GLOB_RECURSE _headers CONFIGURE_DEPENDS
        RELATIVE "${base_dir}"
        "${base_dir}/*.h"
        "${base_dir}/*.hpp"
    )

    set(_export_headers)
    foreach(_header IN LISTS _headers)
        if(_header MATCHES "(^|[/\\\\])test([/\\\\]|$)")
            continue()
        endif()

        get_filename_component(_src "${base_dir}/${_header}" ABSOLUTE)
        if(_src IN_LIST _claimed_headers)
            continue()
        endif()

        list(APPEND _export_headers "${_header}")
    endforeach()

    cppkit_export_headers_tree("${export_target}" "${base_dir}" "${output_dir}" ${_export_headers})
endfunction()
