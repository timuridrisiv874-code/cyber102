import AVFoundation
import SwiftUI

struct InspectionCamera: UIViewControllerRepresentable {
    let qrOnly: Bool
    let equipment: RegisteredEquipment?
    let updateEquipment: (RegisteredEquipment?) -> Void
    let picked: (Data) -> Void
    let failed: (String) -> Void
    func makeUIViewController(context: Context) -> CameraController {
        CameraController(qrOnly: qrOnly, equipment: equipment, updateEquipment: updateEquipment, picked: picked, failed: failed)
    }
    func updateUIViewController(_ controller: CameraController, context: Context) {}
}

final class CameraController: UIViewController, AVCaptureMetadataOutputObjectsDelegate, AVCapturePhotoCaptureDelegate {
    private let capture = AVCaptureSession()
    private let queue = DispatchQueue(label: "Cyber102.InspectionCamera")
    private let photo = AVCapturePhotoOutput()
    private let qrOnly: Bool
    private let updateEquipment: (RegisteredEquipment?) -> Void
    private let picked: (Data) -> Void
    private let failed: (String) -> Void
    private var currentEquipment: RegisteredEquipment?
    private var scanning: Bool
    private var preview: AVCaptureVideoPreviewLayer?
    private var configured = false // Camera queue.
    private let tagButton = UIButton(type: .system)
    private let scanButton = UIButton(type: .system)
    private let shutter = UIButton(type: .system)
    private var canScan = false
    private let scanMessage = UILabel()

    init(qrOnly: Bool, equipment: RegisteredEquipment?, updateEquipment: @escaping (RegisteredEquipment?) -> Void, picked: @escaping (Data) -> Void, failed: @escaping (String) -> Void) {
        self.qrOnly = qrOnly; self.currentEquipment = equipment; self.scanning = qrOnly
        self.updateEquipment = updateEquipment; self.picked = picked; self.failed = failed
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { nil }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        let layer = AVCaptureVideoPreviewLayer(session: capture)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
        for button in [scanButton, tagButton, shutter] {
            var style = UIButton.Configuration.tinted()
            style.baseForegroundColor = .white
            style.background.backgroundColor = UIColor.black.withAlphaComponent(0.72)
            style.cornerStyle = .capsule
            style.imagePadding = 6
            button.configuration = style
            button.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(button)
        }
        scanButton.configuration?.image = UIImage(systemName: "qrcode.viewfinder")
        scanButton.isSelected = scanning
        scanButton.accessibilityLabel = "Сканировать QR-код оборудования"
        scanButton.addTarget(self, action: #selector(toggleScan), for: .touchUpInside)
        tagButton.configuration?.image = UIImage(systemName: "xmark.circle")
        tagButton.addTarget(self, action: #selector(clearTag), for: .touchUpInside)
        shutter.configuration?.image = UIImage(systemName: "camera.fill")
        shutter.configuration?.title = "Снять фото"
        shutter.accessibilityLabel = "Снять фото для осмотра"
        shutter.addTarget(self, action: #selector(shoot), for: .touchUpInside)
        shutter.isHidden = qrOnly
        shutter.isEnabled = false
        scanMessage.translatesAutoresizingMaskIntoConstraints = false
        scanMessage.textColor = .white
        scanMessage.backgroundColor = UIColor.black.withAlphaComponent(0.75)
        scanMessage.font = .preferredFont(forTextStyle: .subheadline)
        scanMessage.layer.cornerRadius = 6
        scanMessage.layer.masksToBounds = true
        scanMessage.textAlignment = .center
        scanMessage.numberOfLines = 0
        scanMessage.isHidden = true
        view.addSubview(scanMessage)
        refreshTag()
        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            scanButton.topAnchor.constraint(equalTo: guide.topAnchor, constant: 12),
            scanButton.trailingAnchor.constraint(equalTo: guide.trailingAnchor, constant: -14),
            tagButton.leadingAnchor.constraint(equalTo: guide.leadingAnchor, constant: 14),
            tagButton.trailingAnchor.constraint(lessThanOrEqualTo: scanButton.leadingAnchor, constant: -8),
            tagButton.centerYAnchor.constraint(equalTo: scanButton.centerYAnchor),
            scanMessage.leadingAnchor.constraint(equalTo: guide.leadingAnchor, constant: 14),
            scanMessage.trailingAnchor.constraint(equalTo: guide.trailingAnchor, constant: -14),
            scanMessage.topAnchor.constraint(equalTo: scanButton.bottomAnchor, constant: 8),
            shutter.centerXAnchor.constraint(equalTo: guide.centerXAnchor),
            shutter.bottomAnchor.constraint(equalTo: guide.bottomAnchor, constant: -24),
            shutter.heightAnchor.constraint(greaterThanOrEqualToConstant: 48)
        ])
        scanButton.isEnabled = false
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
    }
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        queue.async { [weak self] in self?.start() }
    }
    private func start() {
        if !configured {
            guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                  let input = try? AVCaptureDeviceInput(device: device) else {
                DispatchQueue.main.async { self.failed("Камера недоступна.") }; return
            }
            capture.beginConfiguration()
            capture.sessionPreset = .photo
            guard capture.canAddInput(input) else {
                capture.commitConfiguration()
                DispatchQueue.main.async { self.failed("Не удалось подключить камеру.") }; return
            }
            capture.addInput(input)
            guard capture.canAddOutput(photo) else {
                capture.commitConfiguration()
                DispatchQueue.main.async { self.failed("Камера не смогла включить съёмку.") }; return
            }
            capture.addOutput(photo)
            let metadata = AVCaptureMetadataOutput()
            var supportsQR = false
            if capture.canAddOutput(metadata) {
                capture.addOutput(metadata)
                supportsQR = metadata.availableMetadataObjectTypes.contains(.qr)
                if supportsQR {
                    metadata.setMetadataObjectsDelegate(self, queue: .main)
                    metadata.metadataObjectTypes = [.qr]
                }
            }
            capture.commitConfiguration()
            configured = true
            let qr = supportsQR
            DispatchQueue.main.async {
                self.canScan = qr; self.scanButton.isEnabled = qr; self.shutter.isEnabled = true
                if self.qrOnly && !qr { self.failed("Эта камера не поддерживает чтение QR.") }
            }
        }
        capture.startRunning()
    }
    override func viewDidDisappear(_ animated: Bool) {
        super.viewDidDisappear(animated)
        queue.async { [capture] in if capture.isRunning { capture.stopRunning() } }
    }
    private func refreshTag() {
        scanButton.isSelected = scanning
        tagButton.isHidden = currentEquipment == nil
        tagButton.configuration?.title = currentEquipment.map { $0.serial + " · " + $0.title }
        tagButton.titleLabel?.lineBreakMode = .byTruncatingTail
        tagButton.accessibilityLabel = currentEquipment.map { "Очистить объект \($0.title), \($0.serial)" }
        scanButton.configuration?.title = scanning ? "Наведите на QR" : "QR"
    }
    @objc private func toggleScan() {
        guard canScan else { return }
        scanning = qrOnly || !scanning; scanMessage.isHidden = true; refreshTag()
    }
    @objc private func clearTag() {
        currentEquipment = nil; updateEquipment(nil); scanMessage.isHidden = true; refreshTag()
    }
    private func message(_ value: String) {
        guard scanMessage.isHidden || scanMessage.text != value else { return }
        scanMessage.text = value
        scanMessage.isHidden = false
        UIAccessibility.post(notification: .announcement, argument: value)
    }
    @objc private func shoot() {
        shutter.isEnabled = false
        queue.async { self.photo.capturePhoto(with: AVCapturePhotoSettings(), delegate: self) }
    }
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        let data = photo.fileDataRepresentation()
        DispatchQueue.main.async {
            if let data, error == nil { self.picked(data) }
            else { self.failed(error?.localizedDescription ?? "Не удалось снять фото.") }
        }
    }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard scanning else { return }
        let strings = metadataObjects.compactMap { ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }
        guard !strings.isEmpty else { return }
        let serials = strings.compactMap(EquipmentTag.parse)
        guard let serial = serials.first else {
            message("QR не содержит номера оборудования.")
            return
        }
        do {
            let catalog = try EquipmentCatalog.bundled.get()
            guard let object = serials.compactMap({ catalog.find($0) }).first else {
                message("Объект с номером «" + serial + "» не найден.")
                return
            }
            currentEquipment = object; scanning = false
            scanMessage.isHidden = true; refreshTag(); updateEquipment(object)
        } catch {
            message("Каталог оборудования недоступен.")
        }
    }
}
