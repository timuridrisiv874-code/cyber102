import SwiftUI
import UIKit

extension InspectionMeasurement {
    var methodLabel: String {
        switch method {
        case .lidar: "LiDAR"
        case .exif: "Оценка по EXIF"
        case .pixels: "Пиксели исходного фото"
        }
    }
}

/// The view uses UIKit zoom and converts touches in image coordinates after zoom and pan.
/// The source maps these upright, normalized points to the original raster or camera model.
private final class MeasurementCanvas: UIScrollView, UIScrollViewDelegate {
    private let picture = UIImageView()
    private let outline = CAShapeLayer()
    private let marks = CAShapeLayer()
    private var previousSize: CGSize = .zero
    var points: [MeasurementPoint] = [] { didSet { setNeedsLayout() } }
    var onPick: ((MeasurementPoint) -> Void)?

    init(image: UIImage) {
        super.init(frame: .zero)
        backgroundColor = .black
        delegate = self
        showsHorizontalScrollIndicator = false
        showsVerticalScrollIndicator = false
        contentInsetAdjustmentBehavior = .never
        picture.image = image
        picture.frame = CGRect(origin: .zero, size: image.size)
        addSubview(picture)
        for layer in [outline, marks] {
            layer.fillColor = nil
            layer.lineCap = .round
            layer.lineJoin = .round
            picture.layer.addSublayer(layer)
        }
        outline.strokeColor = UIColor.black.cgColor
        marks.strokeColor = UIColor.systemYellow.cgColor
        addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapped(_:))))
        accessibilityLabel = "Фото для замера"
    }

    required init?(coder: NSCoder) { nil }
    func viewForZooming(in scrollView: UIScrollView) -> UIView? { picture }
    func scrollViewDidZoom(_ scrollView: UIScrollView) { setNeedsLayout() }

    override func layoutSubviews() {
        super.layoutSubviews()
        let size = picture.bounds.size
        guard size.width > 0, size.height > 0, bounds.width > 0, bounds.height > 0 else { return }
        if bounds.size != previousSize {
            previousSize = bounds.size
            let fit = min(bounds.width / size.width, bounds.height / size.height)
            maximumZoomScale = max(fit * 8, 1)
            minimumZoomScale = fit
            setZoomScale(fit, animated: false)
            contentSize = picture.frame.size
        }
        // Center a letterboxed photograph without changing the scroll offset while pinching.
        let center = CGPoint(x: max(bounds.width, picture.frame.width) / 2,
                             y: max(bounds.height, picture.frame.height) / 2)
        if picture.center != center { picture.center = center }

        let path = UIBezierPath()
        let vertices = points.map { CGPoint(x: $0.x * size.width, y: $0.y * size.height) }
        if vertices.count == 2 {
            path.move(to: vertices[0]); path.addLine(to: vertices[1])
        }
        let radius = 6 / zoomScale
        for p in vertices {
            path.append(UIBezierPath(ovalIn: CGRect(x: p.x - radius, y: p.y - radius,
                                                    width: 2 * radius, height: 2 * radius)))
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        outline.lineWidth = 4 / zoomScale
        marks.lineWidth = 2 / zoomScale
        outline.path = path.cgPath
        marks.path = path.cgPath
        CATransaction.commit()
    }

    @objc private func tapped(_ gesture: UITapGestureRecognizer) {
        let p = gesture.location(in: picture)
        guard picture.bounds.contains(p), picture.bounds.width > 0, picture.bounds.height > 0 else { return }
        onPick?(MeasurementPoint(x: p.x / picture.bounds.width, y: p.y / picture.bounds.height))
    }
}

private struct ZoomableMeasurementPhoto: UIViewRepresentable {
    let image: UIImage
    let points: [MeasurementPoint]
    var picked: ((MeasurementPoint) -> Void)? = nil
    func makeUIView(context: Context) -> MeasurementCanvas { MeasurementCanvas(image: image) }
    func updateUIView(_ view: MeasurementCanvas, context: Context) {
        view.points = points
        view.onPick = picked
    }
}

struct PhotoMeasurementView: View {
    let imageData: Data
    let source: PhotoMeasurementSource
    let previous: InspectionMeasurement?
    let apply: (InspectionMeasurement) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var photo: UIImage?
    @State private var points: [MeasurementPoint] = []
    @State private var failure: String?

    private var result: InspectionMeasurement? {
        points.count == 2 ? source.measure(from: points[0], to: points[1]) : nil
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                if let photo {
                    ZoomableMeasurementPhoto(image: photo, points: points) { point in
                        if points.count < 2 { points.append(point) }
                    }
                } else if let failure {
                    ContentUnavailableView("Фото недоступно", systemImage: "photo", description: Text(failure))
                } else { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
                VStack(alignment: .leading, spacing: 6) {
                    if let result {
                        Text(result.displayText).font(.title2.bold()).monospacedDigit()
                        Text(result.methodLabel).foregroundStyle(.secondary)
                    } else if points.count == 2 {
                        Text("Точки совпадают. Переставьте вторую точку.")
                    } else {
                        Text(points.isEmpty ? "Коснитесь начала дефекта" : "Коснитесь конца дефекта").font(.headline)
                        Text(source.hasMetricScale ? "Масштаб по EXIF применим к поверхности в плоскости фокусировки, снятой прямо." : "У фото нет достоверного масштаба. Длина будет в пикселях.")
                            .foregroundStyle(.secondary).font(.footnote)
                    }
                    if !points.isEmpty { Button("Шаг назад") { points.removeLast() }.frame(minHeight: 44) }
                }.padding(.horizontal).padding(.bottom)
            }
            .navigationTitle("Замер по фото")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Отмена") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Готово") { if let result { apply(result); dismiss() } }.disabled(result == nil)
                }
            }
        }
        .task {
            if let previous { points = [previous.start, previous.end] }
            let bytes = imageData
            do {
                let preview = try await Task.detached(priority: .userInitiated) {
                    try inspectionJPEG(bytes, maximumPixels: 3072)
                }.value
                guard !Task.isCancelled, let picture = UIImage(data: preview) else { return }
                photo = picture
            } catch { failure = error.localizedDescription }
        }
    }
}

struct SavedMeasurementView: View {
    let jpeg: Data
    let measurement: InspectionMeasurement
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                if let picture = UIImage(data: jpeg) {
                    ZoomableMeasurementPhoto(image: picture, points: [measurement.start, measurement.end])
                }
                Text(measurement.displayText).font(.title2.bold()).monospacedDigit()
                Text(measurement.methodLabel).foregroundStyle(.secondary).padding(.bottom)
            }
            .navigationTitle("Замер осмотра")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Закрыть") { dismiss() } } }
        }
    }
}
