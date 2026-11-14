import CyberInspection
import Foundation

struct InspectionError: LocalizedError {
    var errorDescription: String?
    init(_ description: String) { errorDescription = description }
}

actor InspectionEngine {
    private let prompt = "Inspect this image. Briefly identify the visible industrial equipment or component, then describe clearly visible damage or defects and their location. If no clear defect is visible, say so. Report only what the image supports; do not infer internal faults, causes, operating status or severity. Use at most 70 words. Answer in English."

    // begin/cancel operate on the native epoch independently of long-running inference.
    nonisolated func begin() -> UInt64 { cyber_lfm_begin() }
    nonisolated func cancel() { cyber_lfm_cancel() }

    func describe(_ jpeg: Data, epoch: UInt64) throws -> String {
        guard let model = Bundle.main.url(forResource: "LFM3-VL-2B-Q4_K_M", withExtension: "gguf"),
              let projector = Bundle.main.url(forResource: "mmproj-LFM3-VL-2B-F16", withExtension: "gguf")
        else { throw InspectionError("В сборке отсутствуют файлы модели LFM3.") }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jpg")
        try jpeg.write(to: file, options: .atomic)
        defer { try? FileManager.default.removeItem(at: file) }
        if !cyber_lfm_loaded() {
            if let error = cyber_lfm_load(model.path, projector.path, Int32(min(4, ProcessInfo.processInfo.activeProcessorCount)), epoch) {
                defer { cyber_lfm_free(error) }
                throw InspectionError(String(cString: error))
            }
        }
        guard let buffer = cyber_lfm_analyze(file.path, prompt, 320, epoch) else {
            throw InspectionError("Движок не вернул результат.")
        }
        defer { cyber_lfm_free(buffer) }
        struct Reply: Decodable { let text: String?; let error: String? }
        let reply = try JSONDecoder().decode(Reply.self, from: Data(String(cString: buffer).utf8))
        if let error = reply.error { throw InspectionError(error) }
        guard let text = reply.text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            throw InspectionError("Модель вернула пустое описание.")
        }
        return text
    }
}
