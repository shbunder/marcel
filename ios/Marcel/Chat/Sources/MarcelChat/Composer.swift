import MarcelItems
import PixelKit
import SwiftUI

/// Where you talk to Marcel: the rounded field with a round send button. It is never disabled; only an empty
/// message cannot be sent. While a question waits for you it says "Or say something…" (wireframe 3B). The mention
/// chips and the attached item going with the next message sit above the field, on the composer's strip.
public struct Composer<Accessory: View>: View {
    @Binding var text: String
    var placeholder: String
    var focused: FocusState<Bool>.Binding?
    var send: () -> Void
    /// What VoiceOver calls the text field; "Message to <name>" when nil.
    var fieldLabel: String?
    /// Above the field, on the composer's strip.
    var accessory: Accessory

    public init(text: Binding<String>, placeholder: String, focused: FocusState<Bool>.Binding? = nil,
                fieldLabel: String? = nil, send: @escaping () -> Void, @ViewBuilder accessory: () -> Accessory) {
        self.fieldLabel = fieldLabel
        self.accessory = accessory()
        _text = text
        self.placeholder = placeholder
        self.focused = focused
        self.send = send
    }

    @Environment(\.chatStillAvatars) private var still
    @Environment(\.agentName) private var agentName

    /// The rounded composer of the chat's language (PixelKit's `ChatComposerField`, the S-18.6 follow-up): the field
    /// on the well colour, a round send button in Marcel's rose, on a white strip with a thin line above it.
    public var body: some View {
        ChatComposerField(text: $text, placeholder: placeholder, fieldLabel: fieldLabel ?? "Message to \(agentName)",
                          still: still, focused: focused, send: send) { accessory }
    }
}

extension Composer where Accessory == EmptyView {
    public init(text: Binding<String>, placeholder: String, focused: FocusState<Bool>.Binding? = nil,
                fieldLabel: String? = nil, send: @escaping () -> Void) {
        self.init(text: text, placeholder: placeholder, focused: focused, fieldLabel: fieldLabel, send: send,
                  accessory: { EmptyView() })
    }
}

extension Composer<EmptyView> {
    static func placeholder(questionWaiting: Bool, name: String) -> String {
        questionWaiting ? "Or say something…" : "Message \(name)…"
    }
}
