import MarcelKit
import SwiftUI
import UIKit

/// Scan the QR code `marcel-hub pair` shows, or paste its link (handy in the simulator).
struct PairingView: View {
    let connection: HubConnection
    @State private var pasted = ""
    @State private var working = false
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Text("Pair this phone with your hub")
                    .font(.title2.bold())
                Text("On the hub, run `marcel-hub pair` and point the camera at the code it shows.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                QRScannerView { code in submit(code) }
                    .frame(maxWidth: .infinity, minHeight: 240, maxHeight: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel("Camera, looking for the pairing code")
                TextField("Or paste the pairing link", text: $pasted)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submit(pasted) }
                Button(working ? "Pairing…" : "Pair") { submit(pasted) }
                    .buttonStyle(.borderedProminent)
                    .disabled(working || pasted.isEmpty)
                if let problem {
                    Text(problem)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }
                Spacer()
            }
            .padding()
            .navigationTitle("Marcel")
        }
    }

    private func submit(_ text: String) {
        guard !working else { return }
        working = true
        problem = nil
        Task {
            do {
                try await connection.pair(scanned: text, deviceName: UIDevice.current.name)
            } catch {
                problem = error.localizedDescription
            }
            working = false
        }
    }
}
