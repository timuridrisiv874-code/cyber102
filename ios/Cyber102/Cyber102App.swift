import SwiftUI
import UIKit
import WebKit

@main
struct Cyber102App: App {
    var body: some Scene {
        WindowGroup {
            InspectionView()
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didReceiveMemoryWarningNotification)) { _ in
                    Task { await InspectionEngine.shared.releaseForMemoryPressure() }
                }
        }
    }
}

struct OnlineInspectionScreen: View {
    let serial: String?
    @Environment(\.dismiss) private var dismiss
    @State private var failed = false
    @State private var reload = UUID()

    var body: some View {
        ZStack(alignment: .topTrailing) {
            InspectionWebsite(failed: $failed, serial: serial)
                .id(reload)
                .ignoresSafeArea(edges: .bottom)
            if failed {
                ContentUnavailableView {
                    Label("Нет соединения", systemImage: "wifi.slash")
                } description: {
                    Text("Для онлайн-осмотра нужен интернет. Осмотр на телефоне доступен без сети.")
                } actions: {
                    Button("Повторить") { failed = false; reload = UUID() }
                        .buttonStyle(.borderedProminent)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(.background)
            }
            Button { dismiss() } label: {
                Label("На телефоне", systemImage: "iphone")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 12).padding(.vertical, 9)
                    .background(.regularMaterial, in: Capsule())
            }
            .padding(.top, 8).padding(.trailing, 12)
        }
    }
}

private struct InspectionWebsite: UIViewRepresentable {
    @Binding var failed: Bool
    let serial: String?

    private var url: URL {
        var components = URLComponents(string: "https://cyber102.94.41.87.102.sslip.io/index.html")!
        components.queryItems = [URLQueryItem(name: "serial", value: serial ?? "P-102")]
        return components.url!
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        configuration.websiteDataStore = .default()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.load(URLRequest(url: url))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private var parent: InspectionWebsite
        init(_ parent: InspectionWebsite) { self.parent = parent }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            parent.failed = true
        }

        func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            decisionHandler(.prompt)
        }
    }
}
