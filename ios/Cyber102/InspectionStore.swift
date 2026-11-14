import Foundation
import SwiftUI

struct InspectionTiming: Codable {
    let profile: InspectionProfile
    let preparationMs: Double
    let promptMs: Double
    let generationMs: Double
    let totalMs: Double
    let inputTokens: Int
    let generatedTokens: Int
    let truncated: Bool

    init(_ output: InspectionResult) {
        profile = output.profile
        preparationMs = output.preparationMs
        promptMs = output.promptMs
        generationMs = output.generationMs
        totalMs = output.totalMs
        inputTokens = output.inputTokens
        generatedTokens = output.generatedTokens
        truncated = output.truncated
    }
}

struct InspectionEntry: Identifiable, Codable {
    var id: UUID
    var date: Date
    var english: String
    var russian: String?
    var qr: String?
    var note: String
    var videoSeconds: Double? = nil
    var equipment: RegisteredEquipment? = nil
    var measurement: InspectionMeasurement? = nil
    var measuredPhoto: Data? = nil
    var photoGeometry: PhotoMeasurementSource? = nil
    var timing: InspectionTiming? = nil
}

@MainActor
final class InspectionStore: ObservableObject {
    @Published private(set) var entries: [InspectionEntry] = []
    @Published private(set) var error: String?
    private let file = URL.applicationSupportDirectory.appendingPathComponent("inspections.json")
    private var canWrite = true

    init() {
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        do { entries = try JSONDecoder().decode([InspectionEntry].self, from: Data(contentsOf: file)) }
        catch {
            canWrite = false
            self.error = "Не удалось прочитать локальный журнал."
        }
    }

    func save(_ entry: InspectionEntry) {
        guard canWrite else { return }
        let next = Array(([entry] + entries.filter { $0.id != entry.id }).prefix(10))
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(next).write(to: file, options: [.atomic, .completeFileProtection])
            entries = next
            error = nil
        } catch { self.error = "Не удалось сохранить запись. Проверьте свободное место на iPhone." }
    }
}
