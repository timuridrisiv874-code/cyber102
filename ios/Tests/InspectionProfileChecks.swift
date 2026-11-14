// Swift host test using the iOS photo preparation and inference code. From the repository root:
//   python3 tools/prepare_android_models.py --download
//   python3 android/app/src/main/cpp/fetch_llama.py
//   cmake -S ios/Engine -B var/ios-test-host -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
//   cmake --build var/ios-test-host --target cyber_inspection_bundle --parallel 4
//   swiftc -O -I ios/Engine/include ios/Cyber102/{InspectionEngine,PhotoCapture}.swift \
//     ios/Tests/InspectionProfileChecks.swift var/ios-test-host/libCyberInspection.a \
//     -lc++ -framework Accelerate -o var/ios-test-host/check-profiles
//   ln -s "$PWD"/var/models/lfm3-vl-2b/*.gguf var/ios-test-host/
//   var/ios-test-host/check-profiles /path/to/photo1.jpg /path/to/photo2.jpg
// JSON lines include the English output and request wall times. Translation runs in the UI.
import CyberInspection
import Foundation

private struct Sample {
    let name: String
    let jpeg: Data

    init(path: String) throws {
        let url = URL(fileURLWithPath: path)
        name = url.deletingPathExtension().lastPathComponent
        jpeg = try inspectionJPEG(Data(contentsOf: url))
    }
}

private struct Report: Encodable {
    let phase: String
    let round: Int
    let photo: String
    let profile: InspectionProfile
    let loadedBefore: Bool
    let preparationMs: Double
    let promptMs: Double
    let generationMs: Double
    let totalMs: Double
    let inputTokens: Int
    let generatedTokens: Int
    let truncated: Bool
    let text: String
}

@main struct InspectionProfileChecks {
    static func main() async throws {
        let paths = Array(CommandLine.arguments.dropFirst())
        guard paths.count > 1, Set(paths).count == paths.count else {
            throw InspectionError("Pass at least two distinct photos for the consecutive-photo check.")
        }
        let photos = try paths.map(Sample.init)
        let engine = InspectionEngine.shared
        func run(_ photo: Sample, _ profile: InspectionProfile, phase: String, round: Int = 0,
                 expectLoaded: Bool) async throws {
            guard cyber_lfm_loaded() == expectLoaded else { throw InspectionError("Unexpected initial model state.") }
            let value = try await engine.describe(photo.jpeg, profile: profile, epoch: engine.begin())
            guard cyber_lfm_loaded(), !value.text.isEmpty, value.profile == profile,
                  value.inputTokens > 0, value.generatedTokens > 0,
                  value.generatedTokens <= Int(profile.maximumTokens), value.totalMs > 0,
                  expectLoaded ? value.preparationMs == 0 : value.preparationMs > 0 else {
                throw InspectionError("Unexpected model load, timing, or generation result.")
            }
            let report = Report(phase: phase, round: round, photo: photo.name, profile: profile,
                                loadedBefore: expectLoaded, preparationMs: value.preparationMs,
                                promptMs: value.promptMs, generationMs: value.generationMs, totalMs: value.totalMs,
                                inputTokens: value.inputTokens, generatedTokens: value.generatedTokens,
                                truncated: value.truncated, text: value.text)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            FileHandle.standardOutput.write(try encoder.encode(report) + Data([10]))
        }

        try await run(photos[0], .detailed, phase: "firstPhoto", expectLoaded: false)
        try await run(photos[1], .quick, phase: "secondPhoto", expectLoaded: true)
        for round in 1...2 {
            for (index, photo) in photos.enumerated() {
                let profiles: [InspectionProfile] = (index + round).isMultiple(of: 2) ? [.quick, .detailed] : [.detailed, .quick]
                for profile in profiles {
                    try await run(photo, profile, phase: "paired", round: round, expectLoaded: true)
                }
            }
        }

        let staleEpoch = engine.begin()
        engine.cancel()
        do {
            _ = try await engine.describe(photos[0].jpeg, profile: .quick, epoch: staleEpoch)
            throw InspectionError("A cancelled photo request returned a result.")
        } catch is CancellationError {}
        try await run(photos[0], .quick, phase: "afterCancel", expectLoaded: true)

        await engine.releaseForMemoryPressure()
        try await run(photos[0], .quick, phase: "afterMemoryPressure", expectLoaded: false)
        await engine.releaseForMemoryPressure()
        guard !cyber_lfm_loaded() else { throw InspectionError("Final unload did not release the model.") }
    }
}
