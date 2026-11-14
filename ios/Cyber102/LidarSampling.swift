import Foundation

/// Rotations applied to the rear camera image before showing the frozen, aspect-fit photograph.
enum LidarImageRotation {
    case upright, clockwise, inverted, counterclockwise

    func sensorPoint(_ displayed: MeasurementPoint) -> MeasurementPoint {
        switch self {
        case .upright: return displayed
        case .clockwise: return .init(x: displayed.y, y: 1 - displayed.x)
        case .inverted: return .init(x: 1 - displayed.x, y: 1 - displayed.y)
        case .counterclockwise: return .init(x: 1 - displayed.y, y: displayed.x)
        }
    }
}

/// ARKit depth is optical-axis Z, in metres. Never fill a missing center pixel from a neighboring surface.
enum LidarSampling {
    static func median(at x: Int, y: Int, width: Int, height: Int,
                       reading: (Int, Int) -> Double?) -> Double? {
        guard x >= 0, x < width, y >= 0, y < height,
              let center = reading(x, y), center.isFinite, center > 0 else { return nil }
        var values: [Double] = []
        for row in max(0, y - 1)...min(height - 1, y + 1) {
            for column in max(0, x - 1)...min(width - 1, x + 1) {
                if let z = reading(column, row), z.isFinite, z > 0 { values.append(z) }
            }
        }
        // Decline a mixed foreground/background neighborhood, especially on thin protruding parts.
        guard values.count >= 5 else { return nil }
        values.sort()
        guard values.last! - values.first! <= max(0.012, center * 0.02) else { return nil }
        return (values[(values.count - 1) / 2] + values[values.count / 2]) * 0.5
    }
}
