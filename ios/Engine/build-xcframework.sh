#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
output="$repo/var/ios-engine"
mkdir -p "$output"
xcodebuild -version >/dev/null
python3 "$repo/android/app/src/main/cpp/fetch_llama.py"
for platform in iphoneos iphonesimulator; do
    sdk=$(xcrun --sdk "$platform" --show-sdk-path)
    compiler=$(xcrun --sdk "$platform" --find clang)
    cpp_compiler=$(xcrun --sdk "$platform" --find clang++)
    cmake -S "$here" -B "$output/$platform" -G "Unix Makefiles" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_SYSTEM_NAME=iOS \
        -DCMAKE_OSX_SYSROOT="$sdk" -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_C_COMPILER="$compiler" -DCMAKE_CXX_COMPILER="$cpp_compiler" \
        -DGGML_CCACHE=OFF -DCMAKE_OSX_DEPLOYMENT_TARGET=18.0 -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY
    cmake --build "$output/$platform" --target cyber_inspection_bundle --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"
done
stage=$(mktemp -d "$output/.staging.XXXXXX")
trap 'rm -rf "$stage"' EXIT
xcodebuild -create-xcframework \
    -library "$output/iphoneos/libCyberInspection.a" -headers "$here/include" \
    -library "$output/iphonesimulator/libCyberInspection.a" -headers "$here/include" \
    -output "$stage/CyberInspection.xcframework"
if [[ -d "$output/CyberInspection.xcframework" ]]; then
    mv "$output/CyberInspection.xcframework" "$stage/previous"
fi
if ! mv "$stage/CyberInspection.xcframework" "$output/CyberInspection.xcframework"; then
    [[ ! -d "$stage/previous" ]] || mv "$stage/previous" "$output/CyberInspection.xcframework"
    exit 1
fi
echo "$output/CyberInspection.xcframework"
