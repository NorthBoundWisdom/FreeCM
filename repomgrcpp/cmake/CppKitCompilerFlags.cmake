include(CMakeParseArguments)

function(cppkit_apply_common_compile_flags)
    cmake_parse_arguments(
        CPPKIT_FLAGS
        "USE_AVX;MSVC_EMBEDDED_DEBUG_INFO;ENABLE_LINK_WHAT_YOU_USE"
        "EIGEN_MAX_ALIGN_BYTES"
        ""
        ${ARGN}
    )

    if(NOT MSVC)
        add_compile_options(-Wall)
    endif()

    add_compile_definitions($<$<CONFIG:Debug>:DEBUG>)

    if(NOT MSVC)
        add_compile_options(
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
            add_compile_options($<$<CONFIG:Debug>:-gdwarf-4>)
        endif()
    endif()

    if(CPPKIT_FLAGS_EIGEN_MAX_ALIGN_BYTES)
        add_compile_definitions(EIGEN_MAX_ALIGN_BYTES=${CPPKIT_FLAGS_EIGEN_MAX_ALIGN_BYTES})
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|x64)$")
        if(CPPKIT_FLAGS_USE_AVX AND MSVC AND NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
            add_compile_definitions(EIGEN_MAX_ALIGN_BYTES=32)
        else()
            add_compile_definitions(EIGEN_MAX_ALIGN_BYTES=16)
        endif()
    endif()

    set(_is_clang_cl OFF)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang" AND CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
        set(_is_clang_cl ON)
    endif()

    if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|x64)$")
        if(_is_clang_cl)
            add_compile_options(/clang:-msse3 /clang:-mssse3 /clang:-msse4.1 /clang:-msse4.2)
        elseif(CPPKIT_FLAGS_USE_AVX AND NOT MSVC)
            add_compile_options(-march=x86-64-v2 -mtune=generic)
        endif()
    endif()

    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        add_compile_definitions(COMPILER_CLANG)
        if(WIN32)
            add_compile_definitions(FMT_RUNTIME)
            add_compile_options(
                $<$<CONFIG:Debug>:-gcodeview>
                $<$<CONFIG:Debug>:-fstandalone-debug>
                $<$<CONFIG:Debug>:-fno-limit-debug-info>
            )
            if(_is_clang_cl)
                add_compile_options(
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
            add_compile_options(-stdlib=libc++)
            add_link_options(-stdlib=libc++)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU")
        add_compile_definitions(COMPILER_GCC)
        add_compile_options(-fdiagnostics-color=always)
        if(UNIX AND NOT APPLE)
            add_compile_options($<$<CONFIG:Debug>:-ggdb3>)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "IntelLLVM")
        add_compile_definitions(COMPILER_INTEL)
        add_compile_options(
            $<$<CONFIG:Debug>:-O1>
            $<$<CONFIG:Release>:-O3>
        )
    elseif(MSVC)
        add_compile_definitions(COMPILER_MSVC)
        set_property(GLOBAL PROPERTY USE_FOLDERS ON)
        set(CMAKE_SUPPRESS_REGENERATION true)
        add_compile_options(
            /utf-8
            /EHsc
            $<$<CONFIG:Debug>:/Od>
            $<$<CONFIG:Debug>:/RTC1>
            $<$<CONFIG:RelWithDebInfo>:/O2>
        )
        add_link_options(
            $<$<CONFIG:Debug>:/DEBUG:FULL>
            $<$<CONFIG:RelWithDebInfo>:/DEBUG:FULL>
        )
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
