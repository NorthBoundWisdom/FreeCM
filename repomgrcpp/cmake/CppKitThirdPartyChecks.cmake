function(cppkit_assert_dependency_header dependency_name dependency_root header_relative_path)
    if(NOT dependency_name)
        message(FATAL_ERROR "cppkit_assert_dependency_header: dependency_name is required")
    endif()
    if(NOT dependency_root)
        message(FATAL_ERROR "cppkit_assert_dependency_header(${dependency_name}): dependency_root is required")
    endif()
    if(NOT header_relative_path)
        message(FATAL_ERROR "cppkit_assert_dependency_header(${dependency_name}): header_relative_path is required")
    endif()

    set(_header_path "${dependency_root}/${header_relative_path}")
    if(NOT EXISTS "${_header_path}")
        message(FATAL_ERROR
            "Dependency '${dependency_name}' is missing required header '${header_relative_path}' "
            "under '${dependency_root}'.")
    endif()
endfunction()
