import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// swiftc ios/Cyber102/{MeasurementGeometry,PhotoMeasurement}.swift ios/Tests/PhotoMeasurementChecks.swift -o /tmp/photo-check && /tmp/photo-check
@main
enum PhotoMeasurementChecks {
    static let width = 1200, height = 800
    static let projection: [String: Any] = [
        kCGImagePropertyExifSubjectDistance as String: 1.0,
        kCGImagePropertyExifFocalLength as String: 10.0,
        kCGImagePropertyExifFocalPlaneXResolution as String: 2000,
        kCGImagePropertyExifFocalPlaneYResolution as String: 1600,
        kCGImagePropertyExifFocalPlaneResolutionUnit as String: 3,
        kCGImagePropertyExifPixelXDimension as String: width,
        kCGImagePropertyExifPixelYDimension as String: height
    ]

    /// A JPEG with an independently specified rectangular target. The projected edges at x=200/371
    /// represent a nominal 85.60-mm-wide gauge at 1 m, rounded to whole pixels on a 2 px/mm camera.
    static func photograph(exif: [String: Any], orientation: Int = 1, editor: String? = nil) -> Data {
        let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        ctx.setFillColor(gray: 1, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        ctx.setFillColor(gray: 0, alpha: 1)
        ctx.fill(CGRect(x: 200, y: 300, width: 171, height: 86))
        let bytes = NSMutableData()
        let destination = CGImageDestinationCreateWithData(bytes, UTType.jpeg.identifier as CFString, 1, nil)!
        var props: [String: Any] = [kCGImagePropertyOrientation as String: orientation,
                                     kCGImagePropertyExifDictionary as String: exif]
        if let editor { props[kCGImagePropertyTIFFDictionary as String] = [kCGImagePropertyTIFFSoftware as String: editor] }
        CGImageDestinationAddImage(destination, ctx.makeImage()!, props as CFDictionary)
        precondition(CGImageDestinationFinalize(destination))
        return bytes as Data
    }

    /// Observe the width of the encoded JPEG rather than reusing its nominal design coordinates.
    static func observedEnds(_ data: Data) -> (MeasurementPoint, MeasurementPoint) {
        let source = CGImageSourceCreateWithData(data as CFData, nil)!
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)!
        let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                            bytesPerRow: image.width, space: CGColorSpaceCreateDeviceGray(),
                            bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        let pixels = ctx.data!.assumingMemoryBound(to: UInt8.self)
        for y in 0..<image.height {
            let row = y * ctx.bytesPerRow
            let dark = (0..<image.width).filter { pixels[row + $0] < 128 }
            if let first = dark.first, let last = dark.last {
                let yFromTop = 1 - (Double(y) + 0.5) / Double(image.height)
                return (MeasurementPoint(x: Double(first) / Double(image.width), y: yFromTop),
                        MeasurementPoint(x: Double(last + 1) / Double(image.width), y: yFromTop))
            }
        }
        preconditionFailure("The projected target must be visible in the decoded JPEG")
    }

    static func main() throws {
        let jpeg = photograph(exif: projection)
        let (start, end) = observedEnds(jpeg)
        let source = PhotoMeasurementSource(data: jpeg)!
        let measured = source.measure(from: start, to: end)!
        precondition(measured.method == .exif && abs(measured.value - 85.5) < 0.00001)
        print(String(format: "Synthetic 85.60 mm target: %.2f mm measured, %.2f mm absolute error (%.3f%%).",
                     measured.value, abs(measured.value - 85.60), abs(measured.value - 85.60) * 100 / 85.60))

        // EXIF 5–8 swap the two pixel scales even when they are unequal.
        for orientation in 1...8 {
            let source = PhotoMeasurementSource(data: photograph(exif: projection, orientation: orientation))!
            let swaps = orientation >= 5
            precondition(source.width == (swaps ? height : width) && source.height == (swaps ? width : height))
            let a = MeasurementPoint(x: 0.1, y: 0.1)
            let b = swaps ? MeasurementPoint(x: 0.1, y: 0.2) : MeasurementPoint(x: 0.2, y: 0.1)
            precondition(abs(source.measure(from: a, to: b)!.value - 60) < 0.0001)
        }

        func checkPixels(_ exif: [String: Any], editor: String? = nil) {
            let pixels = PhotoMeasurementSource(data: photograph(exif: exif, editor: editor))!.measure(from: start, to: end)!
            precondition(pixels.method == .pixels && abs(pixels.value - 171) < 1e-6)
        }
        checkPixels([:])
        var modified = projection
        modified.removeValue(forKey: kCGImagePropertyExifSubjectDistance as String)
        checkPixels(modified)
        for invalid in [0.0, Double(UInt32.max)] {
            modified[kCGImagePropertyExifSubjectDistance as String] = invalid
            checkPixels(modified)
        }
        checkPixels(projection, editor: "Adobe Photoshop")
        modified = projection
        modified[kCGImagePropertyExifDigitalZoomRatio as String] = 2
        checkPixels(modified)
        modified = projection
        modified[kCGImagePropertyExifFocalPlaneResolutionUnit as String] = 1
        checkPixels(modified)
        modified = projection
        modified[kCGImagePropertyExifFocalLenIn35mmFilm as String] = 120
        checkPixels(modified)
        var equivalent: [String: Any] = [kCGImagePropertyExifFocalLenIn35mmFilm as String: 30,
            kCGImagePropertyExifSubjectDistance as String: 0.8, kCGImagePropertyExifDigitalZoomRatio as String: 1]
        let known = PhotoMeasurementSource(data: photograph(exif: equivalent))!.measure(from: start, to: end)!
        // 1200 × 800 is 3:2. Its full-frame-equivalent width is 36 mm.
        precondition(known.method == .exif && abs(known.value - 136.8) < 0.01)
        equivalent.removeValue(forKey: kCGImagePropertyExifDigitalZoomRatio as String)
        checkPixels(equivalent)
        precondition(source.measure(from: start, to: start) == nil)
        precondition(source.measure(from: start, to: .init(x: 1.01, y: 0.5)) == nil)
        let encoded = try JSONEncoder().encode(measured)
        let restored = try JSONDecoder().decode(InspectionMeasurement.self, from: encoded)
        precondition(restored == measured)
        print("PASS: original dimensions, 8 orientations, equivalent focal length, missing/ambiguous EXIF, pixel fallback and persistence")
    }
}
