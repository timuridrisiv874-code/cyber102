import Foundation

@main
struct LidarSamplingChecks {
    static func main() {
        let upright = MeasurementPoint(x: 0.23, y: 0.72)
        for (rotation, x, y) in [(LidarImageRotation.upright, 0.23, 0.72), (.clockwise, 0.72, 0.77),
                                 (.inverted, 0.77, 0.28), (.counterclockwise, 0.28, 0.23)] {
            let raw = rotation.sensorPoint(upright)
            precondition(abs(raw.x - x) < 1e-9 && abs(raw.y - y) < 1e-9)
        }
        func sample(_ values: [Double?], x: Int = 1, y: Int = 1) -> Double? {
            LidarSampling.median(at: x, y: y, width: 3, height: 3) { x, y in values[3 * y + x] }
        }
        let stable = sample([1, nil, 1.004, 1.002, 1.001, 1.003, nil, 1, .nan])
        precondition(abs((stable ?? 0) - 1.0015) < 1e-9)
        precondition(sample([1, 1, 1, 1, nil, 1, 1, 1, 1]) == nil)
        precondition(sample([1, 1, 1, 1, 0, 1, 1, 1, 1]) == nil)
        precondition(sample([1, 1, 1, 0.8, 1, 1, 1, 1, 1]) == nil)
        precondition(sample([1, nil, nil, 1, 1, nil, 1, nil, nil]) == nil)
        precondition(sample([1, 1, 1, 1, 1, 1, 1, 1, 1], x: 0, y: 0) == nil)
        let a = MeasurementPoint(x: 0.25, y: 0.5), b = MeasurementPoint(x: 0.75, y: 0.5)
        let near = DepthProjection.Point(x: 0, y: 0, z: 0.5)
        let far = DepthProjection.Point(x: 0.03, y: 0.04, z: 0.5)
        func twoTaps(_ first: DepthProjection.Point?, _ second: DepthProjection.Point?, _ separated: Bool) -> InspectionMeasurement? {
            InspectionMeasurement.onDepthFrame(from: a, to: b, firstDepth: first, secondDepth: second,
                                               separateDepthPixels: separated, rasterWidth: 640, rasterHeight: 480)
        }
        let metric = twoTaps(near, far, true)
        precondition(metric?.method == .lidar && abs((metric?.value ?? 0) - 50) < 1e-8)
        for fallback in [twoTaps(near, nil, true), twoTaps(nil, far, true), twoTaps(near, far, false)] {
            precondition(fallback?.method == .pixels && fallback?.value == 320)
        }
        print("LiDAR rotations, finite neighborhood, confidence holes and silhouette rejection passed.")
    }
}
