if(NOT DEFINED CPPKIT_RUST_LIBRARY OR CPPKIT_RUST_LIBRARY STREQUAL "")
    message(FATAL_ERROR "CPPKIT_RUST_LIBRARY is required")
endif()

if(
    NOT CPPKIT_RUST_FORCE_BUILD
    AND EXISTS "${CPPKIT_RUST_LIBRARY}"
    AND NOT IS_DIRECTORY "${CPPKIT_RUST_LIBRARY}"
)
    return()
endif()

foreach(_required_variable IN ITEMS
    CPPKIT_RUST_STAMP
    CPPKIT_RUST_ROOT_DIR
    CPPKIT_RUST_TARGET_DIR
    CPPKIT_RUST_RUSTC_EXECUTABLE
    CPPKIT_RUST_CARGO_EXECUTABLE
    CPPKIT_RUST_CARGO_ARG_COUNT
)
    if(NOT DEFINED ${_required_variable} OR "${${_required_variable}}" STREQUAL "")
        message(FATAL_ERROR "${_required_variable} is required")
    endif()
endforeach()

if(NOT CPPKIT_RUST_CARGO_ARG_COUNT MATCHES "^[0-9]+$")
    message(FATAL_ERROR "CPPKIT_RUST_CARGO_ARG_COUNT must be a non-negative integer")
endif()
set(_cargo_args "")
if(CPPKIT_RUST_CARGO_ARG_COUNT GREATER 0)
    math(EXPR _cargo_arg_last "${CPPKIT_RUST_CARGO_ARG_COUNT} - 1")
    foreach(_cargo_arg_index RANGE 0 ${_cargo_arg_last})
        set(_cargo_arg_variable "CPPKIT_RUST_CARGO_ARG_${_cargo_arg_index}")
        if(NOT DEFINED ${_cargo_arg_variable})
            message(FATAL_ERROR "${_cargo_arg_variable} is required")
        endif()
        list(APPEND _cargo_args "${${_cargo_arg_variable}}")
    endforeach()
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

get_filename_component(_stamp_dir "${CPPKIT_RUST_STAMP}" DIRECTORY)
file(MAKE_DIRECTORY "${_stamp_dir}")
file(TOUCH "${CPPKIT_RUST_STAMP}")
