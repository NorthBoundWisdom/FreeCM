include(CMakeParseArguments)

function(cppkit_parse_common_compile_flags_args out_prefix)
    cmake_parse_arguments(
        _CPPKIT_FLAGS
        "USE_AVX;MSVC_EMBEDDED_DEBUG_INFO;ENABLE_LINK_WHAT_YOU_USE"
        "EIGEN_MAX_ALIGN_BYTES"
        ""
        ${ARGN}
    )
    foreach(_flag IN ITEMS USE_AVX MSVC_EMBEDDED_DEBUG_INFO ENABLE_LINK_WHAT_YOU_USE)
        set("${out_prefix}_${_flag}" "${_CPPKIT_FLAGS_${_flag}}" PARENT_SCOPE)
    endforeach()
    set("${out_prefix}_EIGEN_MAX_ALIGN_BYTES" "${_CPPKIT_FLAGS_EIGEN_MAX_ALIGN_BYTES}" PARENT_SCOPE)
endfunction()

function(cppkit_common_compile_flags_values out_definitions out_compile_options out_link_options)
    cppkit_parse_common_compile_flags_args(CPPKIT_FLAGS ${ARGN})

    set(_definitions "$<$<CONFIG:Debug>:DEBUG>")
    set(_compile_options "")
    set(_link_options "")

    if(NOT MSVC)
        list(APPEND _compile_options
            -Wall
            $<$<CONFIG:Debug>:-g3>
            $<$<CONFIG:Debug>:-O0>
            $<$<CONFIG:Debug>:-fno-omit-frame-pointer>
            $<$<CONFIG:Debug>:-fno-inline>
            $<$<CONFIG:Debug>:-fno-optimize-sibling-calls>
            $<$<CONFIG:RelWithDebInfo>:-g>
            $<$<CONFIG:RelWithDebInfo>:-O2>
            $<$<CONFIG:RelWithDebInfo>:-fno-omit-frame-pointer>
        )
        if(NOT WIN32)
            list(APPEND _compile_options $<$<CONFIG:Debug>:-gdwarf-4>)
        endif()
    endif()

    if(CPPKIT_FLAGS_EIGEN_MAX_ALIGN_BYTES)
        list(APPEND _definitions EIGEN_MAX_ALIGN_BYTES=${CPPKIT_FLAGS_EIGEN_MAX_ALIGN_BYTES})
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|x64)$")
        if(CPPKIT_FLAGS_USE_AVX AND MSVC AND NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
            list(APPEND _definitions EIGEN_MAX_ALIGN_BYTES=32)
        else()
            list(APPEND _definitions EIGEN_MAX_ALIGN_BYTES=16)
        endif()
    endif()

    set(_is_clang_cl OFF)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang" AND CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
        set(_is_clang_cl ON)
    endif()

    if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|x64)$")
        if(_is_clang_cl)
            list(APPEND _compile_options /clang:-msse3 /clang:-mssse3 /clang:-msse4.1 /clang:-msse4.2)
        elseif(CPPKIT_FLAGS_USE_AVX AND NOT MSVC)
            list(APPEND _compile_options -march=x86-64-v2 -mtune=generic)
        endif()
    endif()

    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        list(APPEND _definitions COMPILER_CLANG)
        if(WIN32)
            list(APPEND _definitions FMT_RUNTIME)
            list(APPEND _compile_options
                $<$<CONFIG:Debug>:-gcodeview>
                $<$<CONFIG:Debug>:-fstandalone-debug>
                $<$<CONFIG:Debug>:-fno-limit-debug-info>
            )
            if(_is_clang_cl)
                list(APPEND _compile_options
                    /clang:-Wno-unused-parameter
                    /clang:-Wno-unused-private-field
                    /clang:-Wno-missing-field-initializers
                    /clang:-Wno-unknown-pragmas
                    /clang:-Wno-sign-compare
                    /clang:-Wno-unsafe-buffer-usage
                    /clang:-Wno-old-style-cast
                )
            endif()
        endif()
        if(APPLE)
            list(APPEND _compile_options -stdlib=libc++)
            list(APPEND _link_options -stdlib=libc++)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU")
        list(APPEND _definitions COMPILER_GCC)
        list(APPEND _compile_options -fdiagnostics-color=always)
        if(UNIX AND NOT APPLE)
            list(APPEND _compile_options $<$<CONFIG:Debug>:-ggdb3>)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "IntelLLVM")
        list(APPEND _definitions COMPILER_INTEL)
        list(APPEND _compile_options
            $<$<CONFIG:Debug>:-O1>
            $<$<CONFIG:Release>:-O3>
        )
    elseif(MSVC)
        list(APPEND _definitions COMPILER_MSVC)
        list(APPEND _compile_options
            /utf-8
            /EHsc
            $<$<CONFIG:Debug>:/Od>
            $<$<CONFIG:Debug>:/RTC1>
            $<$<CONFIG:RelWithDebInfo>:/O2>
        )
        list(APPEND _link_options
            $<$<CONFIG:Debug>:/DEBUG:FULL>
            $<$<CONFIG:RelWithDebInfo>:/DEBUG:FULL>
        )
    endif()

    set("${out_definitions}" "${_definitions}" PARENT_SCOPE)
    set("${out_compile_options}" "${_compile_options}" PARENT_SCOPE)
    set("${out_link_options}" "${_link_options}" PARENT_SCOPE)
endfunction()

function(cppkit_apply_common_compile_flags_to_target target_name)
    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_apply_common_compile_flags_to_target: target does not exist: ${target_name}")
    endif()

    cppkit_parse_common_compile_flags_args(CPPKIT_FLAGS ${ARGN})
    cppkit_common_compile_flags_values(_definitions _compile_options _link_options ${ARGN})
    target_compile_definitions("${target_name}" PRIVATE ${_definitions})
    target_compile_options("${target_name}" PRIVATE ${_compile_options})
    target_link_options("${target_name}" PRIVATE ${_link_options})

    if(CPPKIT_FLAGS_ENABLE_LINK_WHAT_YOU_USE)
        cppkit_enable_link_what_you_use("${target_name}")
    endif()
    if(CPPKIT_FLAGS_MSVC_EMBEDDED_DEBUG_INFO)
        cppkit_apply_msvc_embedded_debug_info_to_target("${target_name}")
    endif()
endfunction()

function(cppkit_apply_msvc_embedded_debug_info_to_target target_name)
    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_apply_msvc_embedded_debug_info_to_target: target does not exist: ${target_name}")
    endif()
    set_property(TARGET "${target_name}" PROPERTY MSVC_DEBUG_INFORMATION_FORMAT "Embedded")
endfunction()

function(cppkit_apply_common_compile_flags)
    cppkit_parse_common_compile_flags_args(CPPKIT_FLAGS ${ARGN})
    cppkit_common_compile_flags_values(_definitions _compile_options _link_options ${ARGN})
    add_compile_definitions(${_definitions})
    add_compile_options(${_compile_options})
    add_link_options(${_link_options})

    if(MSVC)
        set_property(GLOBAL PROPERTY USE_FOLDERS ON)
        set(CMAKE_SUPPRESS_REGENERATION true)
        if(CPPKIT_FLAGS_MSVC_EMBEDDED_DEBUG_INFO)
            set(CMAKE_MSVC_DEBUG_INFORMATION_FORMAT "Embedded" CACHE STRING "MSVC debug information format" FORCE)
        endif()
    endif()
endfunction()

function(cppkit_enable_link_what_you_use target_name)
    if(NOT TARGET "${target_name}")
        message(FATAL_ERROR "cppkit_enable_link_what_you_use: target does not exist: ${target_name}")
    endif()
    set_target_properties("${target_name}" PROPERTIES LINK_WHAT_YOU_USE TRUE)
endfunction()
