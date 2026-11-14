import SwiftUI
import UIKit

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
