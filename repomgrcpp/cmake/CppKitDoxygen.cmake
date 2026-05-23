include(CMakeParseArguments)

function(cppkit_add_doxygen_docs target_name)
    cmake_parse_arguments(
        CPPKIT_DOXYGEN
        "REQUIRED"
        "OUTPUT_DIR;HTML_HEADER;AWESOME_CSS_ARCHIVE"
        "INPUT_DIRS"
        ${ARGN}
    )

    if(NOT CPPKIT_DOXYGEN_INPUT_DIRS)
        message(FATAL_ERROR "cppkit_add_doxygen_docs(${target_name}): INPUT_DIRS is required")
    endif()

    find_package(Doxygen)
    if(NOT DOXYGEN_FOUND)
        if(CPPKIT_DOXYGEN_REQUIRED)
            message(FATAL_ERROR "Doxygen was not found")
        endif()
        message(STATUS "Doxygen not found. Documentation target ${target_name} will not be generated.")
        return()
    endif()

    if(NOT CPPKIT_DOXYGEN_OUTPUT_DIR)
        set(CPPKIT_DOXYGEN_OUTPUT_DIR "${CMAKE_BINARY_DIR}/docs/html")
    endif()

    set(DOXYGEN_GENERATE_HTML YES)
    set(DOXYGEN_HTML_OUTPUT "${CPPKIT_DOXYGEN_OUTPUT_DIR}")

    if(CPPKIT_DOXYGEN_AWESOME_CSS_ARCHIVE)
        cppkit_use_doxygen_awesome_css("${CPPKIT_DOXYGEN_AWESOME_CSS_ARCHIVE}")
    endif()
    if(CPPKIT_DOXYGEN_HTML_HEADER)
        set(DOXYGEN_HTML_HEADER "${CPPKIT_DOXYGEN_HTML_HEADER}")
    endif()

    doxygen_add_docs("${target_name}"
        ${CPPKIT_DOXYGEN_INPUT_DIRS}
        COMMENT "Generate documentation: ${target_name}"
    )
endfunction()

function(cppkit_use_doxygen_awesome_css archive_path)
    if(NOT EXISTS "${archive_path}")
        message(FATAL_ERROR "cppkit_use_doxygen_awesome_css: archive does not exist: ${archive_path}")
    endif()

    set(_css_root "${CMAKE_BINARY_DIR}/doxygen-awesome-css")
    if(NOT EXISTS "${_css_root}")
        file(MAKE_DIRECTORY "${_css_root}")
        execute_process(
            COMMAND ${CMAKE_COMMAND} -E tar xzf "${archive_path}"
            WORKING_DIRECTORY "${_css_root}"
            RESULT_VARIABLE _extract_result
            ERROR_VARIABLE _extract_error
        )
        if(NOT _extract_result EQUAL 0)
            message(FATAL_ERROR "Failed to extract doxygen-awesome-css: ${_extract_error}")
        endif()
    endif()

    file(GLOB _awesome_css "${_css_root}/*/doxygen-awesome.css")
    file(GLOB _awesome_js
        "${_css_root}/*/doxygen-awesome-darkmode-toggle.js"
        "${_css_root}/*/doxygen-awesome-fragment-copy-button.js"
        "${_css_root}/*/doxygen-awesome-paragraph-link.js"
        "${_css_root}/*/doxygen-awesome-interactive-toc.js"
    )

    set(DOXYGEN_GENERATE_TREEVIEW YES PARENT_SCOPE)
    set(DOXYGEN_HAVE_DOT YES PARENT_SCOPE)
    set(DOXYGEN_DOT_IMAGE_FORMAT svg PARENT_SCOPE)
    set(DOXYGEN_DOT_TRANSPARENT YES PARENT_SCOPE)
    set(DOXYGEN_HTML_EXTRA_STYLESHEET ${_awesome_css} PARENT_SCOPE)
    set(DOXYGEN_HTML_EXTRA_FILES ${_awesome_js} PARENT_SCOPE)
endfunction()
