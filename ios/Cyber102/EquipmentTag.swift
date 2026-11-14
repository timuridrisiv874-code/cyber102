import Foundation

/// Accept a serial number or the serial on a checkpoint QR label. Never navigate to the URL.
enum EquipmentTag {
    static func parse(_ raw: String) -> String? {
        guard raw.utf16.count <= 2048 else { return nil }
        var id = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if id.range(of: "^https?://", options: [.regularExpression, .caseInsensitive]) != nil {
            guard var url = URLComponents(string: id), let host = url.host, !host.isEmpty,
                  url.user == nil, url.password == nil, url.percentEncodedPath == "/checkpoint.html" else { return nil }
            // URLSearchParams semantics shared with the Android and web camera.
            url.percentEncodedQuery = url.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%20")
            let serials = (url.queryItems ?? []).filter { $0.name == "serial" }
            guard serials.count == 1, let serial = serials[0].value else { return nil }
            id = serial.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard id.range(of: #"^[\p{L}\p{N}][\p{L}\p{N}._ /-]{0,127}\z"#, options: .regularExpression) != nil else { return nil }
        return id
    }
}
