import ARKit
import AVFoundation
import CoreImage
import SwiftUI

struct LidarMeasurementView: View {
    static var isSupported: Bool { ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) }
    let onSave: (Data, InspectionMeasurement) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        LidarScreen(onSave: { jpeg, length in
            onSave(jpeg, length)
            dismiss()
        }, onCancel: { dismiss() })
        .ignoresSafeArea(.container, edges: .bottom)
    }
}

private struct LidarScreen: UIViewControllerRepresentable {
    let onSave: (Data, InspectionMeasurement) -> Void
    let onCancel: () -> Void
    func makeUIViewController(context: Context) -> LidarController {
        LidarController(onSave: onSave, onCancel: onCancel)
    }
    func updateUIViewController(_ controller: LidarController, context: Context) {}
    static func dismantleUIViewController(_ controller: LidarController, coordinator: ()) { controller.stop() }
}

private extension LidarImageRotation {
    init(_ interface: UIInterfaceOrientation) {
        switch interface {
        case .portrait: self = .clockwise
        case .portraitUpsideDown: self = .counterclockwise
        case .landscapeLeft: self = .inverted
        default: self = .upright
        }
    }

    var imageOrientation: CGImagePropertyOrientation {
        switch self {
        case .upright: return .up
        case .clockwise: return .right
        case .inverted: return .down
        case .counterclockwise: return .left
        }
    }
}

private struct FrozenLidarFrame {
    let image: UIImage
    let depth: CVPixelBuffer
    let confidence: CVPixelBuffer
    let rotation: LidarImageRotation
    let resolution: CGSize
    let intrinsics: simd_float3x3

    init?(_ frame: ARFrame, interface: UIInterfaceOrientation, context: CIContext) {
        guard let scene = frame.sceneDepth, let confidence = scene.confidenceMap else { return nil }
        let depth = scene.depthMap
        guard CVPixelBufferGetPixelFormatType(depth) == kCVPixelFormatType_DepthFloat32,
              CVPixelBufferGetPixelFormatType(confidence) == kCVPixelFormatType_OneComponent8,
              CVPixelBufferGetWidth(depth) > 0, CVPixelBufferGetHeight(depth) > 0,
              CVPixelBufferGetWidth(confidence) == CVPixelBufferGetWidth(depth),
              CVPixelBufferGetHeight(confidence) == CVPixelBufferGetHeight(depth) else { return nil }
        rotation = LidarImageRotation(interface)
        let pixels = CIImage(cvPixelBuffer: frame.capturedImage).oriented(rotation.imageOrientation)
        guard let cgImage = context.createCGImage(pixels, from: pixels.extent) else { return nil }
        image = UIImage(cgImage: cgImage)
        self.depth = depth
        self.confidence = confidence
        resolution = frame.camera.imageResolution
        intrinsics = frame.camera.intrinsics
    }

    func position(_ displayed: MeasurementPoint) -> DepthProjection.Point? {
        let sensor = rotation.sensorPoint(displayed)
        let w = CVPixelBufferGetWidth(depth), h = CVPixelBufferGetHeight(depth)
        guard CVPixelBufferLockBaseAddress(depth, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(depth, .readOnly) }
        guard CVPixelBufferLockBaseAddress(confidence, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
        guard let zs = CVPixelBufferGetBaseAddress(depth)?.assumingMemoryBound(to: Float32.self),
              let levels = CVPixelBufferGetBaseAddress(confidence)?.assumingMemoryBound(to: UInt8.self) else { return nil }
        let depthStride = CVPixelBufferGetBytesPerRow(depth) / MemoryLayout<Float32>.stride
        let levelStride = CVPixelBufferGetBytesPerRow(confidence)
        let x = min(w - 1, Int(sensor.x * Double(w))), y = min(h - 1, Int(sensor.y * Double(h)))
        let meters = LidarSampling.median(at: x, y: y, width: w, height: h) { column, row in
            guard levels[row * levelStride + column] >= UInt8(ARConfidenceLevel.medium.rawValue) else { return nil }
            return Double(zs[row * depthStride + column])
        }
        guard let meters else { return nil }
        return DepthProjection.point(u: sensor.x * resolution.width - 0.5, v: sensor.y * resolution.height - 0.5,
                                     depth: meters, fx: Double(intrinsics[0][0]), fy: Double(intrinsics[1][1]),
                                     cx: Double(intrinsics[2][0]), cy: Double(intrinsics[2][1]))
    }

    func separated(_ a: MeasurementPoint, _ b: MeasurementPoint) -> Bool {
        let p = rotation.sensorPoint(a), q = rotation.sensorPoint(b)
        return hypot((p.x - q.x) * Double(CVPixelBufferGetWidth(depth)),
                     (p.y - q.y) * Double(CVPixelBufferGetHeight(depth))) >= 1
    }
}

private final class LidarController: UIViewController, ARSCNViewDelegate {
    private let onSave: (Data, InspectionMeasurement) -> Void
    private let onCancel: () -> Void
    private let camera = ARSCNView(frame: .zero)
    private let context = CIContext()
    private let stage = UIView()
    private let photo = UIImageView()
    private let ink = CAShapeLayer()
    private let message = UILabel()
    private let primary = UIButton(type: .system)
    private let restart = UIButton(type: .system)
    private var frozen: FrozenLidarFrame?
    private var first: (MeasurementPoint, DepthProjection.Point?)?
    private var result: InspectionMeasurement?
    private var active = false
    private var live = false
    private var previousFrameTimestamp: TimeInterval?

    init(onSave: @escaping (Data, InspectionMeasurement) -> Void, onCancel: @escaping () -> Void) {
        self.onSave = onSave
        self.onCancel = onCancel
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { nil }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let close = UIButton(type: .system, primaryAction: UIAction(title: "Закрыть") { [weak self] _ in self?.onCancel() })
        let title = UILabel()
        title.text = "Замер LiDAR"
        title.font = .preferredFont(forTextStyle: .headline)
        let heading = UIStackView(arrangedSubviews: [title, close])
        heading.alignment = .center
        stage.backgroundColor = .black
        camera.delegate = self
        photo.backgroundColor = .black
        photo.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        photo.contentMode = .scaleAspectFit
        photo.isUserInteractionEnabled = true
        photo.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapPhoto(_:))))
        photo.isHidden = true
        for element in [camera, photo] {
            stage.addSubview(element)
            element.translatesAutoresizingMaskIntoConstraints = false
            NSLayoutConstraint.activate([element.topAnchor.constraint(equalTo: stage.topAnchor),
                                         element.leadingAnchor.constraint(equalTo: stage.leadingAnchor),
                                         element.bottomAnchor.constraint(equalTo: stage.bottomAnchor),
                                         element.trailingAnchor.constraint(equalTo: stage.trailingAnchor)])
        }
        ink.strokeColor = UIColor.systemYellow.cgColor
        ink.fillColor = UIColor.black.withAlphaComponent(0.5).cgColor
        ink.lineWidth = 3
        photo.layer.addSublayer(ink)
        message.font = .preferredFont(forTextStyle: .body)
        message.numberOfLines = 0
        primary.configuration = .filled()
        primary.configuration?.title = "Зафиксировать кадр"
        primary.addAction(UIAction { [weak self] _ in self?.apply() }, for: .touchUpInside)
        restart.configuration = .tinted()
        restart.configuration?.title = "Переснять"
        restart.addAction(UIAction { [weak self] _ in self?.start() }, for: .touchUpInside)
        restart.isHidden = true
        let actions = UIStackView(arrangedSubviews: [restart, primary])
        actions.spacing = 8
        let layout = UIStackView(arrangedSubviews: [heading, stage, message, actions])
        layout.axis = .vertical
        layout.spacing = 10
        view.addSubview(layout)
        layout.translatesAutoresizingMaskIntoConstraints = false
        let safe = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([layout.leadingAnchor.constraint(equalTo: safe.leadingAnchor, constant: 12),
                                     layout.trailingAnchor.constraint(equalTo: safe.trailingAnchor, constant: -12),
                                     layout.topAnchor.constraint(equalTo: safe.topAnchor, constant: 6),
                                     layout.bottomAnchor.constraint(equalTo: safe.bottomAnchor, constant: -8),
                                     actions.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
                                     close.heightAnchor.constraint(greaterThanOrEqualToConstant: 44)])
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        active = true
        if frozen == nil { start() }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        stop()
    }

    func stop() {
        active = false
        live = false
        camera.session.pause()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        drawPoints()
    }

    private func start() {
        guard active else { return }
        frozen = nil
        first = nil
        result = nil
        photo.image = nil
        photo.isHidden = true
        ink.path = nil
        restart.isHidden = true
        primary.configuration?.title = "Зафиксировать кадр"
        primary.isEnabled = false
        guard LidarMeasurementView.isSupported else { message.text = "На этом iPhone LiDAR недоступен."; return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: runCamera()
        case .notDetermined:
            message.text = "Разрешите доступ к камере для замера."
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self, self.active else { return }
                    if granted { self.runCamera() } else { self.permissionDenied() }
                }
            }
        default: permissionDenied()
        }
    }

    private func permissionDenied() {
        message.text = "Откройте настройки iPhone и разрешите Cyber102 доступ к камере."
        restart.configuration?.title = "Повторить"
        restart.isHidden = false
    }

    private func runCamera() {
        guard active, frozen == nil else { return }
        let config = ARWorldTrackingConfiguration()
        config.frameSemantics = .sceneDepth
        previousFrameTimestamp = camera.session.currentFrame?.timestamp
        live = true
        camera.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        primary.isEnabled = true
        restart.isHidden = true
        message.text = "Наведите камеру на деталь и зафиксируйте кадр."
    }

    private var captureOrientation: UIInterfaceOrientation? {
        guard let scene = view.window?.windowScene else { return nil }
        #if compiler(>=6.2)
        if #available(iOS 26.0, macCatalyst 26.0, *) {
            return scene.effectiveGeometry.interfaceOrientation
        } else {
            return scene.interfaceOrientation
        }
        #else
        return scene.interfaceOrientation
        #endif
    }

    private func apply() {
        if let frozen, let result {
            guard let jpeg = frozen.image.jpegData(compressionQuality: 0.90) else {
                message.text = "Не удалось сохранить снимок."; return
            }
            primary.isEnabled = false
            onSave(jpeg, result)
        } else if frozen == nil && live {
            guard let frame = camera.session.currentFrame, frame.timestamp != previousFrameTimestamp,
                  let orientation = captureOrientation, orientation != .unknown,
                  let snapshot = FrozenLidarFrame(frame, interface: orientation, context: context) else {
                message.text = "Карта глубины ещё не готова. Подержите телефон неподвижно и повторите."
                return
            }
            frozen = snapshot
            live = false
            camera.session.pause()
            photo.image = snapshot.image
            photo.isHidden = false
            restart.configuration?.title = "Переснять"
            restart.isHidden = false
            primary.configuration?.title = "Применить замер"
            primary.isEnabled = false
            message.text = "Коснитесь первого конца детали."
        }
    }

    private var pictureRect: CGRect {
        guard let image = frozen?.image else { return .zero }
        return AVMakeRect(aspectRatio: image.size, insideRect: photo.bounds)
    }

    @objc private func tapPhoto(_ gesture: UITapGestureRecognizer) {
        let rect = pictureRect, tap = gesture.location(in: photo)
        guard let frozen, rect.width > 0, rect.height > 0, rect.contains(tap) else { return }
        let p = MeasurementPoint(x: (tap.x - rect.minX) / rect.width, y: (tap.y - rect.minY) / rect.height)
        if result != nil {
            result = nil
            first = nil
            primary.isEnabled = false
            drawPoints()
        }
        let position = frozen.position(p)
        if let first {
            guard p != first.0 else { message.text = "Коснитесь другого конца детали."; return }
            guard let image = frozen.image.cgImage,
                  let measurement = InspectionMeasurement.onDepthFrame(
                    from: first.0, to: p, firstDepth: first.1, secondDepth: position,
                    separateDepthPixels: frozen.separated(first.0, p),
                    rasterWidth: image.width, rasterHeight: image.height) else { return }
            result = measurement
            message.text = measurement.method == .pixels
                ? "\(measurement.displayText) · Для замера в миллиметрах переснимите деталь крупнее."
                : measurement.displayText
            primary.isEnabled = true
        } else {
            first = (p, position)
            result = nil
            primary.isEnabled = false
            message.text = position == nil
                ? "Глубина первой точки ненадёжна. Укажите вторую для замера в пикселях или переснимите."
                : "Коснитесь второго конца детали."
        }
        drawPoints()
    }

    private func drawPoints() {
        guard let start = first?.0 else { ink.path = nil; return }
        let rect = pictureRect
        func screen(_ p: MeasurementPoint) -> CGPoint { CGPoint(x: rect.minX + p.x * rect.width, y: rect.minY + p.y * rect.height) }
        let path = UIBezierPath()
        let endpoints = [start] + (result.map { [$0.end] } ?? [])
        if endpoints.count == 2 { path.move(to: screen(start)); path.addLine(to: screen(endpoints[1])) }
        for p in endpoints {
            let point = screen(p)
            path.append(UIBezierPath(ovalIn: CGRect(x: point.x - 6, y: point.y - 6, width: 12, height: 12)))
        }
        ink.frame = photo.bounds
        ink.path = path.cgPath
    }

    nonisolated func session(_ session: ARSession, didFailWithError error: Error) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.active, self.frozen == nil else { return }
            self.live = false
            self.primary.isEnabled = false
            self.restart.configuration?.title = "Повторить"
            self.restart.isHidden = false
            self.message.text = "Съёмка LiDAR прервана. Повторите запуск камеры."
        }
    }

    nonisolated func sessionWasInterrupted(_ session: ARSession) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.active, self.frozen == nil else { return }
            self.live = false
            self.primary.isEnabled = false
            self.message.text = "Камера приостановлена."
        }
    }

    nonisolated func sessionInterruptionEnded(_ session: ARSession) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.active, self.frozen == nil else { return }
            self.runCamera()
        }
    }
}
