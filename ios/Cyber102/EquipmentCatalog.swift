import Foundation

struct RegisteredEquipment: Codable, Equatable {
    let serial: String
    let name: String
    let model: String?
    let manufacturer: String?
    let demo: Bool

    var title: String { demo ? name + " (демо)" : name }
    var details: [(label: String, value: String)] {
        let fields = [("Модель", model), ("Изготовитель", manufacturer)]
        return fields.compactMap { label, value in
            guard let value, !value.isEmpty else { return nil }
            return (label, value)
        }
    }
}

struct EquipmentCatalog {
    private let equipment: [String: RegisteredEquipment]
    private enum CatalogError: Error { case unavailable, invalid }

    static let bundled = Result {
        guard let file = Bundle.main.url(forResource: "equipment-catalog", withExtension: "json")
        else { throw CatalogError.unavailable }
        return try EquipmentCatalog(data: Data(contentsOf: file))
    }

    init(data: Data) throws {
        struct FileContents: Decodable { let schemaVersion: Int; let equipment: [RegisteredEquipment] }
        let contents = try JSONDecoder().decode(FileContents.self, from: data)
        guard contents.schemaVersion == 1 else { throw CatalogError.invalid }
        var items: [String: RegisteredEquipment] = [:]
        for object in contents.equipment {
            guard !object.name.isEmpty, EquipmentTag.parse(object.serial) == object.serial,
                  items.updateValue(object, forKey: object.serial) == nil else { throw CatalogError.invalid }
        }
        equipment = items
    }

    func find(_ serial: String) -> RegisteredEquipment? { equipment[serial] }
}
