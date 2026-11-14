import Foundation
import SwiftUI

struct InspectionEntry: Identifiable, Codable {
    var id: UUID
    var date: Date
    var english: String
    var russian: String?
    var qr: String?
    var note: String
    var videoSeconds: Double? = nil
    var equipment: RegisteredEquipment? = nil
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
