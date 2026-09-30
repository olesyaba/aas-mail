import SwiftUI

@main
struct AASMailApp: App {
    var body: some Scene {
        WindowGroup { RootView() }
    }
}

/// «Запуск…» → the mail UI, or an error screen that can share the server log.
struct RootView: View {
    enum Phase { case starting, ready, failed(String) }
    @State private var phase: Phase = .starting
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            switch phase {
            case .starting:
                ProgressView("AAS mail запускается…").font(.title3)
            case .ready:
                WebView().ignoresSafeArea(.container, edges: .bottom)
            case .failed(let text):
                VStack(spacing: 16) {
                    Text(text).font(.title3).multilineTextAlignment(.center)
                    Text("Перезапустите приложение. Если не поможет — отправьте лог.")
                        .foregroundStyle(.secondary)
                    ShareLink(item: PyServer.logFile) { Label("Поделиться логом", systemImage: "square.and.arrow.up") }
                }.padding(40)
            }
        }
        .task { await start() }
        .onChange(of: scenePhase) { _, now in
            // iPadOS may have stopped the server thread while we were in the background.
            guard now == .active, case .ready = phase else { return }
            Task { if !(await PyServer.alive()) { phase = .failed("Почтовый сервер остановился") } }
        }
    }

    private func start() async {
        do { try await PyServer.start(); phase = .ready }
        catch { phase = .failed(error.localizedDescription) }
    }
}
