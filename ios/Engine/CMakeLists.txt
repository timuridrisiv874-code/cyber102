cmake_minimum_required(VERSION 3.22)
project(CyberInspection LANGUAGES C CXX)
get_filename_component(REPO "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Share the pinned llama.cpp, mtmd, model prompt, and cancellation implementation with Android.
add_subdirectory("${REPO}/android/app/src/main/cpp" core EXCLUDE_FROM_ALL)
add_library(cyber_inspection_abi STATIC CyberInspection.cpp)
target_include_directories(cyber_inspection_abi PUBLIC include)
target_link_libraries(cyber_inspection_abi PRIVATE cyber_smol_core)

# A static XCFramework slice must contain all transitive archives.
find_program(APPLE_LIBTOOL libtool PATHS /usr/bin NO_DEFAULT_PATH REQUIRED)
set(members cyber_inspection_abi cyber_smol_core mtmd llama ggml ggml-cpu ggml-base vendor-hash)
set(member_files "")
foreach(member IN LISTS members)
    list(APPEND member_files "$<TARGET_FILE:${member}>")
endforeach()
add_custom_command(OUTPUT "${CMAKE_BINARY_DIR}/libCyberInspection.a"
    COMMAND "${APPLE_LIBTOOL}" -static -o "${CMAKE_BINARY_DIR}/libCyberInspection.a" ${member_files}
    DEPENDS ${members} ${member_files} VERBATIM)
add_custom_target(cyber_inspection_bundle DEPENDS "${CMAKE_BINARY_DIR}/libCyberInspection.a")
