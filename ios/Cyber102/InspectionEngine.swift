import CyberInspection
import Foundation
import ImageIO

struct InspectionError: LocalizedError {
    var errorDescription: String?
    init(_ description: String) { errorDescription = description }
}

enum InspectionProfile: String, CaseIterable, Codable, Sendable {
    case quick, detailed

    var maximumPixels: Int { self == .quick ? 512 : 1024 }
    var maximumTokens: Int32 { self == .quick ? 64 : 320 }
    var prompt: String {
        switch self {
        case .quick:
            return "In English, name the visible industrial component, then state the clearest visible defect and its location. If none is clear or the component is uncertain, say so. Use at most 28 words."
        case .detailed:
            return "Inspect this image. Briefly identify the visible industrial equipment or component, then describe clearly visible damage or defects and their location. If no clear defect is visible, say so. Report only what the image supports; do not infer internal faults, causes, operating status or severity. Use at most 70 words. Answer in English."
        }
    }
}

struct InspectionResult: Sendable {
    let text: String
    let profile: InspectionProfile
    let preparationMs: Double
    let promptMs: Double
    let generationMs: Double
    let totalMs: Double
    let inputTokens: Int
    let generatedTokens: Int
    let truncated: Bool
}

actor InspectionEngine {
    static let shared = InspectionEngine()
    private init() {}

    nonisolated func begin() -> UInt64 { cyber_lfm_begin() }
    nonisolated func cancel() { cyber_lfm_cancel() }

    // Signal synchronous native work immediately, then drain it before releasing the mapped files.
    nonisolated func releaseForMemoryPressure() async {
        cancel()
        await unload()
    }

    private func unload() { cyber_lfm_unload() }

    private func check(_ epoch: UInt64) throws {
        try Task.checkCancellation()
        guard cyber_lfm_begin() == epoch else { throw CancellationError() }
    }

    private func elapsedMs(since start: UInt64) -> Double {
        Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000
    }

    // The UI keeps an oriented inspection JPEG; avoid another lossy encode if it already fits.
    private func normalized(_ jpeg: Data, profile: InspectionProfile) throws -> Data {
        guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int, min(width, height) > 0 else {
            throw InspectionError("Не удалось определить размер снимка.")
        }
        if max(width, height) > profile.maximumPixels {
            return try inspectionJPEG(jpeg, maximumPixels: profile.maximumPixels)
        }
        return jpeg
    }

    func describe(_ jpeg: Data, profile: InspectionProfile, epoch: UInt64) throws -> InspectionResult {
        let start = DispatchTime.now().uptimeNanoseconds
        try check(epoch)
        let image = try normalized(jpeg, profile: profile)
        try check(epoch)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jpg")
        defer { try? FileManager.default.removeItem(at: file) }
        try image.write(to: file, options: .atomic)
        try check(epoch)

        var preparationMs = 0.0
        if !cyber_lfm_loaded() {
            guard let model = Bundle.main.url(forResource: "LFM3-VL-2B-Q4_K_M", withExtension: "gguf"),
                  let projector = Bundle.main.url(forResource: "mmproj-LFM3-VL-2B-F16", withExtension: "gguf") else {
                throw InspectionError("В сборке отсутствуют файлы модели LFM3.")
            }
            let loadStart = DispatchTime.now().uptimeNanoseconds
            if let error = cyber_lfm_load(model.path, projector.path, Int32(min(4, ProcessInfo.processInfo.activeProcessorCount)), epoch) {
                defer { cyber_lfm_free(error) }
                try check(epoch)
                throw InspectionError(String(cString: error))
            }
            preparationMs = elapsedMs(since: loadStart)
        }
        try check(epoch)
        guard let buffer = cyber_lfm_analyze(file.path, profile.prompt, profile.maximumTokens, epoch) else {
            throw InspectionError("Движок не вернул результат.")
        }
        defer { cyber_lfm_free(buffer) }
        try check(epoch)
        let data = Data(String(cString: buffer).utf8)
        struct Status: Decodable { let error: String?; let cancelled: Bool? }
        struct Reply: Decodable {
            let text: String
            let promptMs: Double
            let generateMs: Double
            let inputTokens: Int
            let generatedTokens: Int
            let truncated: Bool
        }
        let decoder = JSONDecoder()
        let status = try decoder.decode(Status.self, from: data)
        if status.cancelled == true { throw CancellationError() }
        if let error = status.error { throw InspectionError(error) }
        let reply = try decoder.decode(Reply.self, from: data)
        let text = reply.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw InspectionError("Модель вернула пустое описание.") }
        return InspectionResult(text: text, profile: profile, preparationMs: preparationMs,
                                promptMs: reply.promptMs, generationMs: reply.generateMs,
                                totalMs: elapsedMs(since: start), inputTokens: reply.inputTokens,
                                generatedTokens: reply.generatedTokens, truncated: reply.truncated)
    }
}
