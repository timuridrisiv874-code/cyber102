// Reads paired real depth patches extracted by replay_iphone_measurement.py.
// Calls the same depth acceptance and back-projection functions as the app.
import Foundation

struct TapSample: Decodable {
    let u, v: Double
    let patch: [Double?]
}

struct CapturedPair: Decodable {
    let frame: Int
    let fx, fy, cx, cy: Double
    let points: [TapSample]

    func metricPoint(_ tap: TapSample) -> DepthProjection.Point? {
        guard tap.patch.count == 9,
              let z = LidarSampling.median(at: 1, y: 1, width: 3, height: 3, reading: {
                  tap.patch[3 * $1 + $0]
              }) else { return nil }
        return DepthProjection.point(u: tap.u, v: tap.v, depth: z, fx: fx, fy: fy, cx: cx, cy: cy)
    }
}

@main struct ReplayDepthChecks {
    static func main() throws {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        let frames = try JSONDecoder().decode([CapturedPair].self, from: input)
        for frame in frames {
            let p = frame.points.compactMap(frame.metricPoint)
            if p.count == 2 {
                print("\(frame.frame) \(DepthProjection.distanceMM(p[0], p[1]))")
            } else {
                print("\(frame.frame) rejected")
            }
        }
    }
}
