@preconcurrency import AVFoundation
import SwiftUI
import UIKit

/// The camera, reporting the first QR code it reads. Shows a plain note where there is no camera.
struct QRScannerView: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let controller = ScannerController()
        controller.onCode = onCode
        return controller
    }

    func updateUIViewController(_ controller: ScannerController, context: Context) {}

    final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        var onCode: ((String) -> Void)?
        private let session = AVCaptureSession()
        private var preview: AVCaptureVideoPreviewLayer?
        private var lastCode: String?

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .secondarySystemBackground
            guard let camera = AVCaptureDevice.default(for: .video),
                let input = try? AVCaptureDeviceInput(device: camera), session.canAddInput(input)
            else {
                showNote("No camera here. Paste the pairing link below.")
                return
            }
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else { return showNote("The camera cannot read codes.") }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]
            let layer = AVCaptureVideoPreviewLayer(session: session)
            layer.videoGravity = .resizeAspectFill
            view.layer.addSublayer(layer)
            preview = layer
            nonisolated(unsafe) let session = session
            DispatchQueue.global(qos: .userInitiated).async { session.startRunning() }
        }

        override func viewDidLayoutSubviews() {
            super.viewDidLayoutSubviews()
            preview?.frame = view.bounds
        }

        override func viewDidDisappear(_ animated: Bool) {
            super.viewDidDisappear(animated)
            nonisolated(unsafe) let session = session
            DispatchQueue.global(qos: .userInitiated).async { session.stopRunning() }
        }

        nonisolated func metadataOutput(
            _ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection
        ) {
            let code = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue
            MainActor.assumeIsolated {
                guard let code, code != lastCode else { return }
                lastCode = code
                onCode?(code)
            }
        }

        private func showNote(_ text: String) {
            let label = UILabel()
            label.text = text
            label.numberOfLines = 0
            label.textAlignment = .center
            label.textColor = .secondaryLabel
            label.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(label)
            NSLayoutConstraint.activate([
                label.centerXAnchor.constraint(equalTo: view.centerXAnchor),
                label.centerYAnchor.constraint(equalTo: view.centerYAnchor),
                label.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 16),
            ])
        }
    }
}
