// The Python driver supplies the original registered depth samples around two RGB corners.
// Call the same filtering, unprojection and distance functions as the native iPhone screen.
import Foundation

@main
struct TUMDepthReplay {
    struct Corner: Decodable {
        let u, v: Double
        let rawDepth: [UInt16]

        func cameraPoint() -> DepthProjection.Point? {
            guard rawDepth.count == 9 else { return nil }
            let metres = LidarSampling.median(at: 1, y: 1, width: 3, height: 3) { column, row in
                let raw = rawDepth[3 * row + column]
                return raw > 0 ? Double(raw) / 5000 : nil
            }
            guard let metres else { return nil }
            return DepthProjection.point(u: u, v: v, depth: metres, fx: 535.4, fy: 539.2,
                                         cx: 320.1, cy: 247.6)
        }
    }

    static func main() throws {
        let rows = try JSONDecoder().decode([[Corner]].self, from: FileHandle.standardInput.readDataToEndOfFile())
        let lengths: [Double?] = rows.map { pair in
            guard pair.count == 2, let a = pair[0].cameraPoint(), let b = pair[1].cameraPoint() else { return nil }
            return DepthProjection.distanceMM(a, b)
        }
        FileHandle.standardOutput.write(try JSONEncoder().encode(lengths))
    }
}
