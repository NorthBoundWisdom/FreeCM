include(CMakeParseArguments)

function(cppkit_find_rust_tools)
    set(_rust_hints "")
    if(DEFINED ENV{RUST_ROOT} AND NOT "$ENV{RUST_ROOT}" STREQUAL "")
        list(APPEND _rust_hints "$ENV{RUST_ROOT}" "$ENV{RUST_ROOT}/bin")
    endif()
    if(DEFINED ENV{HOME} AND NOT "$ENV{HOME}" STREQUAL "")
        list(APPEND _rust_hints "$ENV{HOME}/.cargo/bin")
    endif()
    if(DEFINED ENV{USERPROFILE} AND NOT "$ENV{USERPROFILE}" STREQUAL "")
        list(APPEND _rust_hints "$ENV{USERPROFILE}/.cargo/bin")
    endif()

    if(WIN32 AND NOT CARGO_EXECUTABLE AND NOT RUSTC_EXECUTABLE)
        find_program(_rustup_executable rustup HINTS "$ENV{USERPROFILE}/.cargo/bin")
        if(_rustup_executable)
            execute_process(
                COMMAND "${_rustup_executable}" which rustc
                RESULT_VARIABLE _rustup_result
                OUTPUT_VARIABLE _rustc_from_rustup
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET
                TIMEOUT 10
            )
            if(_rustup_result EQUAL 0 AND EXISTS "${_rustc_from_rustup}")
                get_filename_component(_rust_bin_dir "${_rustc_from_rustup}" DIRECTORY)
                if(EXISTS "${_rust_bin_dir}/cargo.exe")
                    set(CARGO_EXECUTABLE "${_rust_bin_dir}/cargo.exe" CACHE FILEPATH "Path to cargo executable" FORCE)
                    set(RUSTC_EXECUTABLE "${_rustc_from_rustup}" CACHE FILEPATH "Path to rustc executable" FORCE)
                endif()
            endif()
        endif()
    endif()

    find_program(CARGO_EXECUTABLE cargo HINTS ${_rust_hints} DOC "Path to cargo executable")
    find_program(RUSTC_EXECUTABLE rustc HINTS ${_rust_hints} DOC "Path to rustc executable")

    if(NOT CARGO_EXECUTABLE)
        message(FATAL_ERROR "cargo was not found. Set -DCARGO_EXECUTABLE=<path>.")
    endif()
    if(NOT RUSTC_EXECUTABLE)
        message(FATAL_ERROR "rustc was not found. Set -DRUSTC_EXECUTABLE=<path>.")
    endif()
endfunction()

function(cppkit_build_rust_library)
    cmake_parse_arguments(
        PARSE_ARGV 0
        CPPKIT_RUST
        "NO_DEFAULT_FEATURES"
        "NAME;ROOT_DIR;TARGET_DIR;BUILD_TYPE;PACKAGE;LIB_BASENAME;CRATE_TYPE"
        "CARGO_ARGS;FEATURES;LINK_LIBRARIES;DEPENDS"
    )

    if(NOT CPPKIT_RUST_NAME)
        message(FATAL_ERROR "cppkit_build_rust_library requires NAME")
    endif()
    if(NOT CPPKIT_RUST_ROOT_DIR)
        message(FATAL_ERROR "cppkit_build_rust_library(${CPPKIT_RUST_NAME}) requires ROOT_DIR")
    endif()
    if(NOT EXISTS "${CPPKIT_RUST_ROOT_DIR}/Cargo.toml")
        message(FATAL_ERROR "cppkit_build_rust_library(${CPPKIT_RUST_NAME}): ROOT_DIR must contain Cargo.toml")
    endif()

    cppkit_find_rust_tools()

    if(CPPKIT_RUST_BUILD_TYPE)
        string(TOLOWER "${CPPKIT_RUST_BUILD_TYPE}" _rust_build_type)
    elseif(CMAKE_BUILD_TYPE STREQUAL "Debug")
        set(_rust_build_type "debug")
    else()
        set(_rust_build_type "release")
    endif()

    if(_rust_build_type STREQUAL "release")
        set(_cargo_build_flags --release)
    elseif(_rust_build_type STREQUAL "debug")
        set(_cargo_build_flags "")
    else()
        message(FATAL_ERROR "Unsupported Rust build type '${_rust_build_type}'; expected debug or release")
    endif()

    if(NOT CPPKIT_RUST_TARGET_DIR)
        set(CPPKIT_RUST_TARGET_DIR "${CMAKE_CURRENT_BINARY_DIR}/rust_target")
    endif()
    if(NOT CPPKIT_RUST_CRATE_TYPE)
        set(CPPKIT_RUST_CRATE_TYPE "staticlib")
    endif()
    if(NOT CPPKIT_RUST_LIB_BASENAME)
        if(CPPKIT_RUST_PACKAGE)
            set(CPPKIT_RUST_LIB_BASENAME "${CPPKIT_RUST_PACKAGE}")
        else()
            set(CPPKIT_RUST_LIB_BASENAME "${CPPKIT_RUST_NAME}")
        endif()
    endif()

    set(_cargo_args rustc ${_cargo_build_flags} --lib "--crate-type=${CPPKIT_RUST_CRATE_TYPE}")
    if(CPPKIT_RUST_PACKAGE)
        list(APPEND _cargo_args --package "${CPPKIT_RUST_PACKAGE}")
    endif()
    if(CPPKIT_RUST_NO_DEFAULT_FEATURES)
        list(APPEND _cargo_args --no-default-features)
    endif()
    if(CPPKIT_RUST_FEATURES)
        string(REPLACE ";" "," _features "${CPPKIT_RUST_FEATURES}")
        list(APPEND _cargo_args --features "${_features}")
    endif()
    list(APPEND _cargo_args ${CPPKIT_RUST_CARGO_ARGS})

    if(WIN32)
        set(_rust_lib_path "${CPPKIT_RUST_TARGET_DIR}/${_rust_build_type}/${CPPKIT_RUST_LIB_BASENAME}.lib")
    else()
        set(_rust_lib_path "${CPPKIT_RUST_TARGET_DIR}/${_rust_build_type}/lib${CPPKIT_RUST_LIB_BASENAME}.a")
    endif()
    set(_rust_stamp_dir "${CPPKIT_RUST_TARGET_DIR}/.cppkit")
    set(_rust_stamp_path "${_rust_stamp_dir}/${CPPKIT_RUST_NAME}-${_rust_build_type}.stamp")
    set(_rust_ensure_script "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/CppKitEnsureRustArtifact.cmake")

    set(_rustflags "-C panic=unwind")
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|x64)$")
        set(_rustflags "${_rustflags} -C target-cpu=x86-64")
    endif()

    file(GLOB_RECURSE _rust_source_dependencies
        CONFIGURE_DEPENDS
        LIST_DIRECTORIES false
        "${CPPKIT_RUST_ROOT_DIR}/src/*.rs"
    )
    file(GLOB _rust_optional_dependencies
        CONFIGURE_DEPENDS
        LIST_DIRECTORIES false
        "${CPPKIT_RUST_ROOT_DIR}/Cargo.lock"
        "${CPPKIT_RUST_ROOT_DIR}/build.rs"
        "${CPPKIT_RUST_ROOT_DIR}/.cargo/config"
        "${CPPKIT_RUST_ROOT_DIR}/.cargo/config.toml"
    )
    set(_rust_dependencies
        "${CPPKIT_RUST_ROOT_DIR}/Cargo.toml"
        ${_rust_source_dependencies}
        ${_rust_optional_dependencies}
    )
    set(_rust_explicit_dependencies "")
    foreach(_rust_dependency IN LISTS CPPKIT_RUST_DEPENDS)
        get_filename_component(
            _rust_dependency_absolute
            "${_rust_dependency}"
            ABSOLUTE
            BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}"
        )
        list(APPEND _rust_explicit_dependencies "${_rust_dependency_absolute}")
    endforeach()
    list(REMOVE_DUPLICATES _rust_explicit_dependencies)
    list(APPEND _rust_dependencies ${_rust_explicit_dependencies})
    list(REMOVE_DUPLICATES _rust_dependencies)
    list(LENGTH _cargo_args _cargo_arg_count)
    list(LENGTH _rust_explicit_dependencies _rust_explicit_dependency_count)
    set(_rust_helper_args
        "-DCPPKIT_RUST_LIBRARY=${_rust_lib_path}"
        "-DCPPKIT_RUST_STAMP=${_rust_stamp_path}"
        "-DCPPKIT_RUST_ROOT_DIR=${CPPKIT_RUST_ROOT_DIR}"
        "-DCPPKIT_RUST_TARGET_DIR=${CPPKIT_RUST_TARGET_DIR}"
        "-DCPPKIT_RUST_RUSTFLAGS=${_rustflags}"
        "-DCPPKIT_RUST_RUSTC_EXECUTABLE=${RUSTC_EXECUTABLE}"
        "-DCPPKIT_RUST_CARGO_EXECUTABLE=${CARGO_EXECUTABLE}"
        "-DCPPKIT_RUST_CARGO_ARG_COUNT=${_cargo_arg_count}"
        "-DCPPKIT_RUST_EXPLICIT_DEPENDENCY_COUNT=${_rust_explicit_dependency_count}"
    )
    set(_cargo_arg_index 0)
    foreach(_cargo_arg IN LISTS _cargo_args)
        list(APPEND _rust_helper_args "-DCPPKIT_RUST_CARGO_ARG_${_cargo_arg_index}=${_cargo_arg}")
        math(EXPR _cargo_arg_index "${_cargo_arg_index} + 1")
    endforeach()
    set(_rust_dependency_index 0)
    foreach(_rust_dependency IN LISTS _rust_explicit_dependencies)
        list(APPEND
            _rust_helper_args
            "-DCPPKIT_RUST_EXPLICIT_DEPENDENCY_${_rust_dependency_index}=${_rust_dependency}"
        )
        math(EXPR _rust_dependency_index "${_rust_dependency_index} + 1")
    endforeach()

    add_custom_target(
        "${CPPKIT_RUST_NAME}_rust" ALL
        COMMAND ${CMAKE_COMMAND} ${_rust_helper_args}
            -P "${_rust_ensure_script}"
        BYPRODUCTS "${_rust_stamp_path}" "${_rust_lib_path}"
        WORKING_DIRECTORY "${CPPKIT_RUST_ROOT_DIR}"
        DEPENDS ${_rust_dependencies}
        COMMENT "Ensuring Rust library ${CPPKIT_RUST_NAME}"
        VERBATIM
    )
    add_library("${CPPKIT_RUST_NAME}" STATIC IMPORTED GLOBAL)
    set_target_properties("${CPPKIT_RUST_NAME}" PROPERTIES IMPORTED_LOCATION "${_rust_lib_path}")

    if(WIN32)
        set_property(TARGET "${CPPKIT_RUST_NAME}" APPEND PROPERTY INTERFACE_LINK_LIBRARIES ws2_32 userenv ntdll)
    elseif(UNIX AND NOT APPLE)
        set_property(TARGET "${CPPKIT_RUST_NAME}" APPEND PROPERTY INTERFACE_LINK_LIBRARIES dl)
    endif()
    if(CPPKIT_RUST_LINK_LIBRARIES)
        set_property(TARGET "${CPPKIT_RUST_NAME}" APPEND PROPERTY INTERFACE_LINK_LIBRARIES ${CPPKIT_RUST_LINK_LIBRARIES})
    endif()

    add_dependencies("${CPPKIT_RUST_NAME}" "${CPPKIT_RUST_NAME}_rust")
endfunction()
