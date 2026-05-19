# Debug pkg-config module resolution.
#
# Usage:
#   cmake -DREPOCONFIGSMGR_PKG_CONFIG_MODULES="gmp;mpfr" -P debug_pkg_config.cmake

if(NOT DEFINED REPOCONFIGSMGR_PKG_CONFIG_MODULES OR REPOCONFIGSMGR_PKG_CONFIG_MODULES STREQUAL "")
    message(FATAL_ERROR "Set REPOCONFIGSMGR_PKG_CONFIG_MODULES to a semicolon-separated module list")
endif()

find_package(PkgConfig REQUIRED)

foreach(module IN LISTS REPOCONFIGSMGR_PKG_CONFIG_MODULES)
    string(MAKE_C_IDENTIFIER "${module}" module_prefix)
    string(TOUPPER "${module_prefix}" module_prefix)

    message(STATUS "")
    message(STATUS "pkg-config module: ${module}")
    pkg_check_modules("${module_prefix}" REQUIRED "${module}")

    foreach(field IN ITEMS
        FOUND
        VERSION
        PREFIX
        INCLUDEDIR
        LIBDIR
        INCLUDE_DIRS
        LIBRARY_DIRS
        LIBRARIES
        LDFLAGS
        LDFLAGS_OTHER
        CFLAGS
        CFLAGS_OTHER
    )
        message(STATUS "${module_prefix}_${field}: ${${module_prefix}_${field}}")
    endforeach()
endforeach()
