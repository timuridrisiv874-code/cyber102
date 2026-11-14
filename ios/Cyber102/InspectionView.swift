import AVFoundation
import PhotosUI
import SwiftUI
import Translation

private enum CaptureSheet: String, Identifiable { case photo, qr; var id: String { rawValue } }

@MainActor
struct InspectionView: View {
    private static let en = Locale.Language(identifier: "en")
    private static let ru = Locale.Language(identifier: "ru")
    @State private var engine = InspectionEngine()
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
    @State private var missingLanguage = false
    @State private var showHistory = false

    var body: some View {
        NavigationStack {
            List {
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
                Section("Фотография") {
                    if let jpeg, let photo = UIImage(data: jpeg) {
                        Image(uiImage: photo).resizable().scaledToFit()
                            .frame(maxWidth: .infinity, maxHeight: 280).accessibilityLabel("Фото осмотра")
                    }
                    HStack {
                        PhotosPicker(selection: $picked, matching: .any(of: [.images, .videos])) { Label("Фото или видео", systemImage: "photo.on.rectangle") }
                        Spacer()
                        Button { openCamera(.photo) } label: { Label("Камера", systemImage: "camera") }
                    }.buttonStyle(.borderless).disabled(analysis != nil || translating)
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
                    if analysis == nil {
                        Button("Проверить на iPhone", action: analyze)
                            .disabled(jpeg == nil || translating || loadingFrame)
                    } else {
                        Button("Остановить", role: .cancel, action: stop)
                    }
                    if !phase.isEmpty { HStack { ProgressView(); Text(phase).font(.subheadline) } }
                }
                if let entry {
                    Section("Предварительное наблюдение") {
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
                            var saved = entry
                            saved.qr = equipment?.serial; saved.equipment = equipment; saved.note = note
                            store.save(saved)
                        }
                    }
                }
                if let message = error ?? store.error { Section { Text(message).foregroundStyle(.secondary) } }
                if !store.entries.isEmpty {
                    Section { Button("Журнал осмотров (\(store.entries.count))") { showHistory = true } }
                }
            }
            .navigationTitle("Осмотр")
            .navigationDestination(isPresented: $showHistory) {
                List(store.entries) { row in
                    DisclosureGroup {
                        if let equipment = row.equipment {
                            LabeledContent("Оборудование", value: equipment.title)
                            LabeledContent("Серийный номер", value: equipment.serial)
                            equipmentDetails(equipment)
                        } else if let qr = row.qr { LabeledContent("QR", value: qr) }
                        if let time = row.videoSeconds { Text("Кадр видео · " + String(format: "%.2f с", time)) }
                        Text(row.russian ?? row.english).textSelection(.enabled)
                        if row.russian != nil {
                            DisclosureGroup("Английский оригинал") { Text(row.english).textSelection(.enabled) }
                        }
                        if !row.note.isEmpty { Text(row.note).foregroundStyle(.secondary) }
                    } label: { Text(row.date.formatted(date: .abbreviated, time: .shortened)) }
                }.navigationTitle("Журнал осмотров")
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
        .translationTask(translationConfig) { session in
            guard let id = translationEntryID, entry?.id == id, let english = entry?.english else { return }
            do {
                let response = try await session.translate(english)
                guard entry?.id == id else { return }
                entry?.russian = response.targetText
                missingLanguage = false
                error = nil
            } catch {
                guard entry?.id == id else { return }
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
    private func selectPhoto(_ bytes: Data) throws {
        let clean = try inspectionJPEG(bytes)
        stop()
        pickEpoch = UUID(); picked = nil
        frameEpoch = UUID(); clip = nil; frameSeconds = nil; loadingFrame = false
        jpeg = clean
        entry = nil; error = nil; missingLanguage = false; note = ""
    }
    private func invalidateFrame() {
        frameEpoch = UUID(); loadingFrame = false
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
    private func analyze() {
        guard let jpeg else { return }
        let id = UUID(); run = id
        let epoch = engine.begin()
        error = nil; phase = "LFM3 описывает фото…"; entry = nil; note = ""
        translationConfig = nil; translationEntryID = nil; translating = false; missingLanguage = false
        analysis = Task {
            defer { if run == id { analysis = nil } }
            do {
                let english = try await engine.describe(jpeg, epoch: epoch)
                guard run == id, !Task.isCancelled else { return }
                entry = InspectionEntry(id: id, date: Date(), english: english, russian: nil, qr: equipment?.serial, note: "", videoSeconds: frameSeconds, equipment: equipment)
                phase = ""
                let status = await LanguageAvailability().status(from: Self.en, to: Self.ru)
                guard run == id, !Task.isCancelled else { return }
                if status == .installed { startTranslation(id) }
                else if status == .supported { missingLanguage = true }
                else { error = "На этом iPhone системный перевод с английского на русский недоступен." }
            } catch {
                if run == id { self.error = error.localizedDescription; phase = "" }
            }
        }
    }
    private func startTranslation(_ id: UUID) {
        error = nil
        translating = true; translationEntryID = id; phase = "Перевод на русский…"
        if translationConfig == nil { translationConfig = .init(source: Self.en, target: Self.ru) }
        else { translationConfig?.invalidate() }
    }
    private func stop() {
        run = UUID()
        analysis?.cancel(); analysis = nil
        engine.cancel()
        translationEntryID = nil; translationConfig = nil; translating = false; phase = ""
    }
}
