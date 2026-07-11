foreach(_required_variable IN ITEMS
    CPPKIT_RUST_LIBRARY
    CPPKIT_RUST_STAMP
    CPPKIT_RUST_ROOT_DIR
    CPPKIT_RUST_TARGET_DIR
    CPPKIT_RUST_RUSTC_EXECUTABLE
    CPPKIT_RUST_CARGO_EXECUTABLE
    CPPKIT_RUST_CARGO_ARG_COUNT
    CPPKIT_RUST_EXPLICIT_DEPENDENCY_COUNT
)
    if(NOT DEFINED ${_required_variable} OR "${${_required_variable}}" STREQUAL "")
        message(FATAL_ERROR "${_required_variable} is required")
    endif()
endforeach()

foreach(_count_variable IN ITEMS
    CPPKIT_RUST_CARGO_ARG_COUNT
    CPPKIT_RUST_EXPLICIT_DEPENDENCY_COUNT
)
    if(NOT ${_count_variable} MATCHES "^[0-9]+$")
        message(FATAL_ERROR "${_count_variable} must be a non-negative integer")
    endif()
endforeach()

function(_cppkit_read_indexed_arguments _output _prefix _count)
    set(_values "")
    if(_count GREATER 0)
        math(EXPR _last_index "${_count} - 1")
        foreach(_index RANGE 0 ${_last_index})
            set(_variable "${_prefix}_${_index}")
            if(NOT DEFINED ${_variable})
                message(FATAL_ERROR "${_variable} is required")
            endif()
            list(APPEND _values "${${_variable}}")
        endforeach()
    endif()
    set(${_output} "${_values}" PARENT_SCOPE)
endfunction()

_cppkit_read_indexed_arguments(
    _cargo_args
    CPPKIT_RUST_CARGO_ARG
    "${CPPKIT_RUST_CARGO_ARG_COUNT}"
)
_cppkit_read_indexed_arguments(
    _explicit_dependencies
    CPPKIT_RUST_EXPLICIT_DEPENDENCY
    "${CPPKIT_RUST_EXPLICIT_DEPENDENCY_COUNT}"
)

get_filename_component(_stamp_dir "${CPPKIT_RUST_STAMP}" DIRECTORY)
file(MAKE_DIRECTORY "${_stamp_dir}")
file(
    LOCK "${CPPKIT_RUST_STAMP}.lock"
    GUARD PROCESS
)

set(_signature_schema "cppkit-rust-signature-v1")
set(_signature_material "schema:${_signature_schema}\n")
macro(_cppkit_append_signature_field _tag _value)
    string(SHA256 _field_hash "${_value}")
    string(APPEND _signature_material "${_tag}:${_field_hash}\n")
endmacro()

_cppkit_append_signature_field("root_dir" "${CPPKIT_RUST_ROOT_DIR}")
_cppkit_append_signature_field("target_dir" "${CPPKIT_RUST_TARGET_DIR}")
_cppkit_append_signature_field("library" "${CPPKIT_RUST_LIBRARY}")
_cppkit_append_signature_field("cargo" "${CPPKIT_RUST_CARGO_EXECUTABLE}")
_cppkit_append_signature_field("rustc" "${CPPKIT_RUST_RUSTC_EXECUTABLE}")
_cppkit_append_signature_field("rustflags" "${CPPKIT_RUST_RUSTFLAGS}")
file(SHA256 "${CMAKE_CURRENT_LIST_FILE}" _helper_hash)
_cppkit_append_signature_field("helper" "${_helper_hash}")

set(_cargo_arg_index 0)
foreach(_cargo_arg IN LISTS _cargo_args)
    _cppkit_append_signature_field("cargo_arg_${_cargo_arg_index}" "${_cargo_arg}")
    math(EXPR _cargo_arg_index "${_cargo_arg_index} + 1")
endforeach()
_cppkit_append_signature_field("cargo_arg_count" "${_cargo_arg_index}")

file(GLOB_RECURSE _source_dependencies LIST_DIRECTORIES false "${CPPKIT_RUST_ROOT_DIR}/src/*.rs")
set(_input_dependencies "${CPPKIT_RUST_ROOT_DIR}/Cargo.toml" ${_source_dependencies})
foreach(_optional_dependency IN ITEMS
    "${CPPKIT_RUST_ROOT_DIR}/Cargo.lock"
    "${CPPKIT_RUST_ROOT_DIR}/build.rs"
    "${CPPKIT_RUST_ROOT_DIR}/.cargo/config"
    "${CPPKIT_RUST_ROOT_DIR}/.cargo/config.toml"
)
    if(EXISTS "${_optional_dependency}" AND NOT IS_DIRECTORY "${_optional_dependency}")
        list(APPEND _input_dependencies "${_optional_dependency}")
    endif()
endforeach()
list(APPEND _input_dependencies ${_explicit_dependencies})
list(REMOVE_DUPLICATES _input_dependencies)
list(SORT _input_dependencies)

set(_input_index 0)
foreach(_input_dependency IN LISTS _input_dependencies)
    if(NOT EXISTS "${_input_dependency}" OR IS_DIRECTORY "${_input_dependency}")
        file(REMOVE "${CPPKIT_RUST_STAMP}")
        message(FATAL_ERROR "Rust build input is not a regular file: ${_input_dependency}")
    endif()
    file(REAL_PATH "${_input_dependency}" _input_real_path)
    file(SHA256 "${_input_dependency}" _input_hash)
    _cppkit_append_signature_field("input_path_${_input_index}" "${_input_real_path}")
    _cppkit_append_signature_field("input_content_${_input_index}" "${_input_hash}")
    math(EXPR _input_index "${_input_index} + 1")
endforeach()
_cppkit_append_signature_field("input_count" "${_input_index}")
string(SHA256 _signature "${_signature_material}")
set(_expected_stamp "${_signature_schema}:${_signature}\n")

set(_published_stamp "")
if(EXISTS "${CPPKIT_RUST_STAMP}" AND NOT IS_DIRECTORY "${CPPKIT_RUST_STAMP}")
    file(READ "${CPPKIT_RUST_STAMP}" _published_stamp)
endif()
if(
    EXISTS "${CPPKIT_RUST_LIBRARY}"
    AND NOT IS_DIRECTORY "${CPPKIT_RUST_LIBRARY}"
    AND "${_published_stamp}" STREQUAL "${_expected_stamp}"
)
    return()
endif()

file(REMOVE "${CPPKIT_RUST_STAMP}")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env
        "CARGO_TARGET_DIR=${CPPKIT_RUST_TARGET_DIR}"
        "RUSTFLAGS=${CPPKIT_RUST_RUSTFLAGS}"
        "RUSTC=${CPPKIT_RUST_RUSTC_EXECUTABLE}"
        "${CPPKIT_RUST_CARGO_EXECUTABLE}" ${_cargo_args}
    WORKING_DIRECTORY "${CPPKIT_RUST_ROOT_DIR}"
    RESULT_VARIABLE _cargo_result
)
if(NOT "${_cargo_result}" STREQUAL "0")
    message(FATAL_ERROR "Cargo failed while building Rust artifact: ${_cargo_result}")
endif()
if(NOT EXISTS "${CPPKIT_RUST_LIBRARY}" OR IS_DIRECTORY "${CPPKIT_RUST_LIBRARY}")
    message(FATAL_ERROR "Cargo did not publish expected Rust library: ${CPPKIT_RUST_LIBRARY}")
endif()

set(_stamp_temporary "${CPPKIT_RUST_STAMP}.tmp")
file(REMOVE "${_stamp_temporary}")
file(WRITE "${_stamp_temporary}" "${_expected_stamp}")
file(RENAME "${_stamp_temporary}" "${CPPKIT_RUST_STAMP}")
