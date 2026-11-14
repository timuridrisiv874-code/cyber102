import AVFoundation
import CoreTransferable
import UIKit
import UniformTypeIdentifiers

/// Photos keeps the received file only for the duration of the import closure.
final class InspectionClip: Transferable, @unchecked Sendable {
    let url: URL
    init(_ url: URL) { self.url = url }
    deinit { try? FileManager.default.removeItem(at: url) }

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { input in
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                .appendingPathExtension(input.file.pathExtension)
            try FileManager.default.copyItem(at: input.file, to: dest)
            return Self(dest)
        }
    }

    func duration() async throws -> Double {
        let seconds = try await AVURLAsset(url: url).load(.duration).seconds
        guard seconds.isFinite, seconds > 0 else { throw InspectionError("Не удалось определить длительность видео.") }
        return seconds
    }

    func frame(at seconds: Double) async throws -> (jpeg: Data, seconds: Double) {
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 1024, height: 1024)
        generator.requestedTimeToleranceAfter = CMTime(seconds: 0.10, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = CMTime(seconds: 0.10, preferredTimescale: 600)
        let result = try await generator.image(at: CMTime(seconds: seconds, preferredTimescale: 600))
        guard let data = UIImage(cgImage: result.image).jpegData(compressionQuality: 0.85)
        else { throw InspectionError("Не удалось извлечь кадр видео.") }
        return (data, result.actualTime.seconds)
    }
}
