import Foundation

/// Coordinates run from 0 to 1 across the complete, oriented image.
struct MeasurementPoint: Codable, Hashable {
    let x: Double
    let y: Double

    var isInImage: Bool { (0...1).contains(x) && (0...1).contains(y) }
}

struct InspectionMeasurement: Codable, Hashable {
    enum Method: String, Codable { case lidar, exif, pixels }

    let method: Method
    /// Millimetres for lidar/exif, original-image pixels for pixels.
    let value: Double
    let start: MeasurementPoint
    let end: MeasurementPoint

    /// Use the upright RGB raster when either depth sample is missing or both taps share a depth pixel.
    static func onDepthFrame(from start: MeasurementPoint, to end: MeasurementPoint,
                             firstDepth: DepthProjection.Point?, secondDepth: DepthProjection.Point?,
                             separateDepthPixels: Bool, rasterWidth: Int, rasterHeight: Int) -> Self? {
        guard start.isInImage, end.isInImage, start != end,
              rasterWidth > 0, rasterHeight > 0 else { return nil }
        if separateDepthPixels, let firstDepth, let secondDepth {
            let length = DepthProjection.distanceMM(firstDepth, secondDepth)
            if length.isFinite, length > 0 {
                return Self(method: .lidar, value: length, start: start, end: end)
            }
        }
        let length = hypot((end.x - start.x) * Double(rasterWidth), (end.y - start.y) * Double(rasterHeight))
        return Self(method: .pixels, value: length, start: start, end: end)
    }

    var displayText: String {
        let number = value.formatted(.number.locale(Locale(identifier: "ru_RU"))
            .precision(.fractionLength(0...1)))
        return method == .pixels ? "\(number) пикс." : "≈ \(number) мм"
    }
}

/// The depth map supplies axial Z in metres, not distance along a sloping viewing ray.
enum DepthProjection {
    struct Point {
        let x: Double
        let y: Double
        let z: Double
    }

    static func point(u: Double, v: Double, depth: Double, fx: Double, fy: Double,
                      cx: Double, cy: Double) -> Point? {
        guard [u, v, depth, fx, fy, cx, cy].allSatisfy(\.isFinite),
              depth > 0, fx > 0, fy > 0 else { return nil }
        return Point(x: (u - cx) * depth / fx, y: (v - cy) * depth / fy, z: depth)
    }

    static func distanceMM(_ a: Point, _ b: Point) -> Double {
        hypot(hypot(a.x - b.x, a.y - b.y), a.z - b.z) * 1000
    }
}
