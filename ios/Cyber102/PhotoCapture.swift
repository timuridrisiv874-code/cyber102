import Foundation
import ImageIO
import UniformTypeIdentifiers

func inspectionJPEG(_ data: Data) throws -> Data {
    guard let source = CGImageSourceCreateWithData(data as CFData, nil),
          let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: 1024,
            kCGImageSourceCreateThumbnailWithTransform: true
          ] as CFDictionary)
    else { throw InspectionError("Не удалось открыть фотографию.") }
    let bytes = NSMutableData()
    guard let encoder = CGImageDestinationCreateWithData(bytes, UTType.jpeg.identifier as CFString, 1, nil) else {
        throw InspectionError("Не удалось подготовить снимок.")
    }
    CGImageDestinationAddImage(encoder, thumbnail, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
    guard CGImageDestinationFinalize(encoder) else { throw InspectionError("Не удалось подготовить JPEG.") }
    return bytes as Data
}
