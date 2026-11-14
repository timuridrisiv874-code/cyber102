import Foundation
import ImageIO

/// Retains the original pixel geometry and optional planar scale, without retaining private metadata.
struct PhotoMeasurementSource: Codable, Equatable {
    let width: Int
    let height: Int
    private let scale: Scale?
    var hasMetricScale: Bool { scale != nil }

    private struct Scale: Codable, Equatable {
        let horizontal: Double // mm per oriented pixel
        let vertical: Double
    }

    init?(pixelWidth: Int, pixelHeight: Int) {
        guard pixelWidth > 0, pixelHeight > 0 else { return nil }
        width = pixelWidth
        height = pixelHeight
        scale = nil
    }

    init?(data: Data) {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
              let w = props[kCGImagePropertyPixelWidth as String] as? Int, w > 0,
              let h = props[kCGImagePropertyPixelHeight as String] as? Int, h > 0 else { return nil }
        let orientation = (props[kCGImagePropertyOrientation as String] as? Int) ?? 1
        let swapsAxes = (5...8).contains(orientation)
        width = swapsAxes ? h : w
        height = swapsAxes ? w : h
        guard (1...8).contains(orientation),
              let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any],
              !Self.hasCropOrEditing(props, source),
              let raw = Self.planarScale(exif: exif, width: w, height: h) else {
            scale = nil
            return
        }
        scale = swapsAxes ? Scale(horizontal: raw.vertical, vertical: raw.horizontal) : raw
    }

    private static func hasCropOrEditing(_ properties: [String: Any], _ source: CGImageSource) -> Bool {
        // A raw mosaic can carry active-area, default-crop and lens-warp instructions. Our preview
        // renderer does not expose a transform from those sensor coordinates to its visible image.
        if properties[kCGImagePropertyDNGDictionary as String] != nil { return true }
        if let tiff = properties[kCGImagePropertyTIFFDictionary as String] as? [String: Any],
           let software = tiff[kCGImagePropertyTIFFSoftware as String] as? String {
            let editors = ["photoshop", "lightroom", "snapseed", "gimp", "affinity photo", "darktable", "pixelmator"]
            if editors.contains(where: { software.localizedCaseInsensitiveContains($0) }) { return true }
        }
        if let metadata = CGImageSourceCopyMetadataAtIndex(source, 0, nil),
           let crop = CGImageMetadataCopyStringValueWithPath(metadata, nil, "crs:HasCrop" as CFString) as String? {
            return ["true", "1"].contains(crop.lowercased())
        }
        return false
    }

    private static func planarScale(exif: [String: Any], width: Int, height: Int) -> Scale? {
        func number(_ key: CFString) -> Double? {
            guard let value = (exif[key as String] as? NSNumber)?.doubleValue, value.isFinite else { return nil }
            return value
        }
        // SubjectDistanceRange (e.g. "close view"), DPI, and focus-motor position are not metric distances.
        guard let metres = number(kCGImagePropertyExifSubjectDistance), metres > 0, metres < Double(UInt32.max),
              number(kCGImagePropertyExifPixelXDimension) == Double(width),
              number(kCGImagePropertyExifPixelYDimension) == Double(height) else { return nil }
        let zoom = number(kCGImagePropertyExifDigitalZoomRatio)
        if let zoom, zoom != 0 && zoom != 1 { return nil }
        let distanceMM = metres * 1000
        if let focal = number(kCGImagePropertyExifFocalLength), focal > 0,
           let x = number(kCGImagePropertyExifFocalPlaneXResolution), x > 0,
           let y = number(kCGImagePropertyExifFocalPlaneYResolution), y > 0,
           let unit = number(kCGImagePropertyExifFocalPlaneResolutionUnit) {
            let mmPerUnit: Double
            switch unit {
            case 2: mmPerUnit = 25.4
            case 3: mmPerUnit = 10
            case 4: mmPerUnit = 1
            case 5: mmPerUnit = 0.001
            default: return nil
            }
            let fx = focal * x / mmPerUnit, fy = focal * y / mmPerUnit
            guard distanceMM > focal, fx.isFinite, fy.isFinite, fx > 0, fy > 0 else { return nil }
            // A large discrepancy with the independently recorded field of view suggests a crop or resize.
            if let equivalent = number(kCGImagePropertyExifFocalLenIn35mmFilm), equivalent > 0 {
                let diagonalFovRatio = hypot(Double(width) / fx, Double(height) / fy) * equivalent / hypot(36, 24)
                if !(0.85...1.15).contains(diagonalFovRatio) { return nil }
            }
            return Scale(horizontal: distanceMM / fx, vertical: distanceMM / fy)
        }
        // The 35-mm equivalent describes diagonal field of view for the captured image area.
        // Limit this fallback to normal 4:3 and 3:2 frames with explicitly disabled digital zoom.
        let aspect = Double(max(width, height)) / Double(min(width, height))
        guard zoom == 0 || zoom == 1,
              abs(aspect - 4.0 / 3) < 0.015 || abs(aspect - 1.5) < 0.015,
              let equivalent = number(kCGImagePropertyExifFocalLenIn35mmFilm), equivalent > 0 else { return nil }
        let focalPixels = equivalent * hypot(Double(width), Double(height)) / hypot(36, 24)
        return Scale(horizontal: distanceMM / focalPixels, vertical: distanceMM / focalPixels)
    }

    func measure(from start: MeasurementPoint, to end: MeasurementPoint) -> InspectionMeasurement? {
        guard start.isInImage, end.isInImage, width > 0, height > 0 else { return nil }
        let dx = (end.x - start.x) * Double(width), dy = (end.y - start.y) * Double(height)
        let value = hypot(dx * (scale?.horizontal ?? 1), dy * (scale?.vertical ?? 1))
        guard value.isFinite, value > 0 else { return nil }
        return InspectionMeasurement(method: hasMetricScale ? .exif : .pixels, value: value, start: start, end: end)
    }
}
