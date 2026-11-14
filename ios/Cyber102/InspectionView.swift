import AVFoundation
import PhotosUI
import SwiftUI
import Translation

private enum CaptureSheet: String, Identifiable { case photo, qr, measurePhoto, lidar; var id: String { rawValue } }

@MainActor
struct InspectionView: View {
    let openOnline: (String?) -> Void
    private static let en = Locale.Language(identifier: "en")
    private static let ru = Locale.Language(identifier: "ru")
    @Environment(\.scenePhase) private var scenePhase
    private let engine = InspectionEngine.shared
    private let profile: InspectionProfile = .quick
    @StateObject private var store = InspectionStore()
    @State private var picked: PhotosPickerItem?
    @State private var clip: InspectionClip?
    @State private var duration: Double = 0
    @State private var position: Double = 0
    @State private var frameSeconds: Double?
    @State private var loadingFrame = false
    @State private var movingSlider = false
    @State private var frameEpoch = UUID()
    @State private var jpeg: Data?
    @State private var originalPhoto: Data?
    @State private var photoSource: PhotoMeasurementSource?
    @State private var measurement: InspectionMeasurement?
    @State private var draftID = UUID()
    @State private var historyPhoto: InspectionEntry?
    @State private var pickEpoch = UUID()
    @State private var sheet: CaptureSheet?
    @State private var equipment: RegisteredEquipment?
    @State private var note = ""
    @State private var entry: InspectionEntry?
    @State private var phase = ""
    @State private var error: String?
    @State private var analysis: Task<Void, Never>?
    @State private var run = UUID()
    @State private var translationConfig: TranslationSession.Configuration?
    @State private var translating = false
    @State private var translationEntryID: UUID?
    @State private var translationRun = UUID()
    @State private var missingLanguage = false
    @State private var showHistory = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button { openOnline(equipment?.serial) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "viewfinder")
                                .font(.title3)
                                .frame(width: 36, height: 36)
                                .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                            VStack(alignment: .leading, spacing: 3) {
                                Text("Онлайн-осмотр").font(.headline)
                                Text("Фото, видео, 3D и общий журнал")
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                Section("Оборудование") {
                    HStack {
                        if let equipment {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(equipment.title).font(.headline)
                                Text(equipment.serial).font(.subheadline).monospacedDigit()
                            }
                        } else { Text("Объект не выбран").foregroundStyle(.secondary) }
                        Spacer()
                        if equipment != nil {
                            Button { equipment = nil } label: { Image(systemName: "xmark.circle") }
                                .accessibilityLabel("Очистить QR-код")
                        }
                        Button { openCamera(.qr) } label: { Label("QR", systemImage: "qrcode.viewfinder") }
                            .accessibilityLabel("Сканировать QR-код оборудования")
                    }
                    .buttonStyle(.borderless)
                    .disabled(analysis != nil)
                    if let equipment { equipmentDetails(equipment) }
                }
                Section("Кадр осмотра") {
                    if let jpeg, let photo = UIImage(data: jpeg) {
                        Image(uiImage: photo).resizable().scaledToFit()
                            .frame(maxWidth: .infinity, maxHeight: 280).accessibilityLabel("Фото осмотра")
                    }
                    HStack {
                        PhotosPicker(selection: $picked, matching: .any(of: [.images, .videos]), preferredItemEncoding: .current) { Label("Фото или видео", systemImage: "photo.on.rectangle") }
                        Spacer()
                        Button { openCamera(.photo) } label: { Label("Камера", systemImage: "camera") }
                    }.buttonStyle(.borderless).disabled(analysis != nil || translating)
                    if photoSource != nil, measurement?.method != .lidar {
                        Button { sheet = .measurePhoto } label: { Label("Замер по фото", systemImage: "ruler") }
                            .disabled(analysis != nil || translating)
                    }
                    if LidarMeasurementView.isSupported {
                        Button { openCamera(.lidar) } label: { Label("Снять с LiDAR", systemImage: "viewfinder") }
                            .disabled(analysis != nil || translating)
                    }
                    if let measurement {
                        LabeledContent(measurement.methodLabel, value: measurement.displayText)
                            .monospacedDigit()
                        if entry == nil {
                            let saved = store.entries.first { $0.id == draftID }
                            Button(saved == nil ? "Сохранить замер в журнал" : "Обновить замер в журнале") {
                                saveDraft()
                            }
                            .disabled(saved?.measurement == measurement && saved?.qr == equipment?.serial || analysis != nil)
                        }
                    }
                    if clip != nil, duration > 0 {
                        let upper = max(0.001, duration - min(0.1, duration / 2))
                        Text("Кадр видео · " + String(format: "%.2f с", position)).font(.caption).monospacedDigit()
                        Slider(value: $position, in: 0...upper, onEditingChanged: { editing in
                            movingSlider = editing
                            if editing { invalidateFrame() } else { loadFrame() }
                        })
                        .onChange(of: position) { _, _ in
                            if !movingSlider { invalidateFrame(); loadFrame() }
                        }
                        .accessibilityLabel("Время кадра")
                        .disabled(analysis != nil || translating)
                    }
                    if analysis == nil && !translating {
                        Button(entry == nil ? "Проверить на iPhone" : "Проверить ещё раз", action: analyze)
                            .disabled(jpeg == nil || loadingFrame)
                    } else {
                        Button("Остановить", role: .cancel, action: stop)
                    }
                    if !phase.isEmpty { HStack { ProgressView(); Text(phase).font(.subheadline) } }
                }
                if let entry {
                    Section("Наблюдение и действия") {
                        if let timing = entry.timing { timingDetails(timing) }
                        Text(entry.russian ?? entry.english).textSelection(.enabled)
                        if entry.russian != nil {
                            DisclosureGroup("Английский оригинал") { Text(entry.english).textSelection(.enabled) }
                        } else if !translating {
                            Button(missingLanguage ? "Подготовить перевод EN → RU" : "Перевести на русский") {
                                startTranslation(entry.id)
                            }
                        }
                        TextField("Заметка", text: $note, axis: .vertical)
                        Button(store.entries.first(where: { $0.id == entry.id }) == nil ? "Сохранить осмотр" : "Обновить запись") {
                            saveDraft()
                        }
                        Button { openCamera(.photo) } label: {
                            Label("Снять следующее фото", systemImage: "camera")
                        }
                        .disabled(analysis != nil || translating)
                    }
                }
                if let message = error ?? store.error { Section { Text(message).foregroundStyle(.secondary) } }
                if !store.entries.isEmpty {
                    Section { Button("Журнал осмотров (\(store.entries.count))") { showHistory = true } }
                }
            }
            .navigationTitle("Осмотр на iPhone")
            .navigationDestination(isPresented: $showHistory) {
                List(store.entries) { row in
                    DisclosureGroup {
                        if let equipment = row.equipment {
                            LabeledContent("Оборудование", value: equipment.title)
                            LabeledContent("Серийный номер", value: equipment.serial)
                            equipmentDetails(equipment)
                        } else if let qr = row.qr { LabeledContent("QR", value: qr) }
                        if let time = row.videoSeconds { Text("Кадр видео · " + String(format: "%.2f с", time)) }
                        if let measurement = row.measurement {
                            LabeledContent(measurement.methodLabel, value: measurement.displayText).monospacedDigit()
                            if row.measuredPhoto != nil {
                                Button("Открыть кадр с точками") { historyPhoto = row }
                            }
                        }
                        if let timing = row.timing { timingDetails(timing) }
                        if !row.english.isEmpty { Text(row.russian ?? row.english).textSelection(.enabled) }
                        if row.russian != nil {
                            DisclosureGroup("Английский оригинал") { Text(row.english).textSelection(.enabled) }
                        }
                        if !row.note.isEmpty { Text(row.note).foregroundStyle(.secondary) }
                    } label: { Text(row.date.formatted(date: .abbreviated, time: .shortened)) }
                }.navigationTitle("Журнал осмотров")
                .sheet(item: $historyPhoto) { item in
                    if let data = item.measuredPhoto, let value = item.measurement {
                        SavedMeasurementView(jpeg: data, measurement: value)
                    }
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { saveDraft() }
            if phase == .background && (analysis != nil || translating) {
                stop()
                error = "Проверка остановлена при переходе в фон. Можно продолжить с этим кадром или выбрать другой."
            }
        }
        .onChange(of: picked) { _, value in
            guard let value else { return }
            let token = UUID()
            pickEpoch = token
            Task {
                do {
                    if value.supportedContentTypes.contains(where: { $0.conforms(to: .movie) }) {
                        guard let video = try await value.loadTransferable(type: InspectionClip.self)
                        else { throw InspectionError("Видео недоступно.") }
                        let length = try await video.duration()
                        guard pickEpoch == token else { return }
                        stop()
                        clip = video; duration = length; position = 0
                        invalidateFrame(); loadFrame()
                    } else {
                        guard let data = try await value.loadTransferable(type: Data.self)
                        else { throw InspectionError("Файл недоступен.") }
                        guard pickEpoch == token else { return }
                        try selectPhoto(data)
                    }
                } catch { if pickEpoch == token { self.error = error.localizedDescription } }
                if pickEpoch == token { picked = nil }
            }
        }
        .sheet(item: $sheet) { source in
            switch source {
            case .measurePhoto:
                if let originalPhoto, let photoSource {
                    PhotoMeasurementView(imageData: originalPhoto, source: photoSource,
                                        previous: measurement?.method == .lidar ? nil : measurement) { value in
                        measurement = value
                    }
                }
            case .lidar:
                LidarMeasurementView { bytes, value in
                    do {
                        try selectPhoto(bytes)
                        measurement = value
                    } catch { self.error = error.localizedDescription }
                }
            case .photo, .qr:
                NavigationStack {
                    InspectionCamera(qrOnly: source == .qr, equipment: equipment) { value in
                        equipment = value
                        if source == .qr, value != nil { sheet = nil }
                    } picked: { bytes in
                        sheet = nil
                        do { try selectPhoto(bytes) } catch { self.error = error.localizedDescription }
                    } failed: { message in
                        error = message; sheet = nil
                    }
                    .ignoresSafeArea(edges: .bottom)
                    .navigationTitle(source == .qr ? "QR-код оборудования" : "Камера")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { sheet = nil } } }
                }
            }
        }
        .translationTask(translationConfig) { session in
            guard let id = translationEntryID, entry?.id == id, let english = entry?.english else { return }
            let token = translationRun
            do {
                let response = try await session.translate(english)
                guard translationRun == token, translationEntryID == id, entry?.id == id else { return }
                entry?.russian = response.targetText
                missingLanguage = false
                error = nil
                saveDraft()
            } catch {
                guard translationRun == token, translationEntryID == id, entry?.id == id else { return }
                self.error = "Русский перевод недоступен. \(error.localizedDescription)"
            }
            if translationEntryID == id { translating = false; phase = "" }
        }
    }

    @ViewBuilder private func equipmentDetails(_ object: RegisteredEquipment) -> some View {
        ForEach(object.details, id: \.label) { field in
            LabeledContent(field.label, value: field.value).font(.subheadline)
        }
    }
    @ViewBuilder private func timingDetails(_ timing: InspectionTiming) -> some View {
        let name = timing.profile == .quick ? "Быстрый" : "Подробный"
        let time: (Double) -> String = { milliseconds in
            (milliseconds / 1_000).formatted(.number.precision(.fractionLength(0...2))) + " с"
        }
        DisclosureGroup("\(name) · анализ \(time(timing.totalMs))") {
            if timing.preparationMs > 0 { LabeledContent("Загрузка модели", value: time(timing.preparationMs)) }
            LabeledContent("Обработка кадра и запроса", value: time(timing.promptMs))
            LabeledContent("Входных токенов", value: String(timing.inputTokens))
            LabeledContent("Генерация ответа", value: time(timing.generationMs))
            LabeledContent("Токенов ответа", value: String(timing.generatedTokens))
        }
        .font(.subheadline)
        if timing.truncated {
            Text("Достигнут лимит ответа. Описание может быть неполным.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }
    private func selectPhoto(_ bytes: Data) throws {
        let clean = try inspectionJPEG(bytes)
        saveDraft()
        stop()
        pickEpoch = UUID(); picked = nil
        frameEpoch = UUID(); clip = nil; frameSeconds = nil; loadingFrame = false
        originalPhoto = bytes
        photoSource = PhotoMeasurementSource(data: bytes)
        measurement = nil
        draftID = UUID()
        jpeg = clean
        entry = nil; error = nil; missingLanguage = false; note = ""
    }
    private func invalidateFrame() {
        saveDraft()
        frameEpoch = UUID(); loadingFrame = false
        originalPhoto = nil; photoSource = nil; measurement = nil; draftID = UUID()
        jpeg = nil; frameSeconds = nil; entry = nil; note = ""; error = nil
    }
    private func loadFrame() {
        guard let clip else { return }
        let token = UUID(); frameEpoch = token; loadingFrame = true
        let seconds = position
        Task {
            defer { if frameEpoch == token { loadingFrame = false } }
            do {
                let frame = try await clip.frame(at: seconds)
                guard frameEpoch == token else { return }
                jpeg = frame.jpeg; frameSeconds = frame.seconds
            } catch { if frameEpoch == token { self.error = error.localizedDescription } }
        }
    }
    private func openCamera(_ mode: CaptureSheet) {
        guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
            error = "Камера недоступна. Можно выбрать готовое фото."; return
        }
        Task {
            guard await AVCaptureDevice.requestAccess(for: .video) else {
                error = "Разрешите доступ к камере в настройках iPhone."; return
            }
            sheet = mode
        }
    }
    private func saveDraft() {
        guard entry != nil || measurement != nil else { return }
        var saved = entry ?? store.entries.first { $0.id == draftID } ??
            InspectionEntry(id: draftID, date: Date(), english: "", russian: nil,
                            qr: nil, note: "", videoSeconds: frameSeconds)
        saved.qr = equipment?.serial
        saved.equipment = equipment
        saved.note = note
        saved.measurement = measurement
        saved.measuredPhoto = measurement == nil ? nil : jpeg
        saved.photoGeometry = measurement == nil ? nil : photoSource
        store.save(saved)
    }

    private func analyze() {
        guard let jpeg else { return }
        let id = UUID(); run = id
        let epoch = engine.begin()
        let selectedProfile = profile
        error = nil; phase = "LFM3 описывает фото…"; entry = nil
        translationConfig = nil; translationEntryID = nil; translating = false; missingLanguage = false
        analysis = Task {
            defer { if run == id { analysis = nil } }
            do {
                let output = try await engine.describe(jpeg, profile: selectedProfile, epoch: epoch)
                guard run == id, !Task.isCancelled else { return }
                let inspectionDate = store.entries.first { $0.id == draftID }?.date ?? Date()
                entry = InspectionEntry(id: draftID, date: inspectionDate, english: output.text, russian: nil, qr: equipment?.serial, note: note, videoSeconds: frameSeconds, equipment: equipment, timing: InspectionTiming(output))
                phase = ""
                saveDraft()
                let status = await LanguageAvailability().status(from: Self.en, to: Self.ru)
                guard run == id, !Task.isCancelled else { return }
                if status == .installed { startTranslation(draftID) }
                else if status == .supported { missingLanguage = true }
                else { error = "На этом iPhone системный перевод с английского на русский недоступен." }
            } catch {
                if run == id { self.error = error.localizedDescription; phase = "" }
            }
        }
    }
    private func startTranslation(_ id: UUID) {
        translationRun = UUID()
        error = nil
        translating = true; translationEntryID = id; phase = "Перевод на русский…"
        if translationConfig == nil { translationConfig = .init(source: Self.en, target: Self.ru) }
        else { translationConfig?.invalidate() }
    }
    private func stop() {
        run = UUID()
        translationRun = UUID()
        if let analysis {
            analysis.cancel()
            engine.cancel()
            self.analysis = nil
        }
        translationEntryID = nil; translationConfig = nil; translating = false; phase = ""
    }
}
