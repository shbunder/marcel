import MarcelKit

/// The words the picker shows and VoiceOver reads for each choice.
public enum LookWords {
    public static func animal(_ animal: LookAnimal) -> String {
        switch animal {
        case .giraffe: "Giraffe"
        case .crocodile: "Crocodile"
        case .frog: "Frog"
        case .cat: "Cat"
        case .fox: "Fox"
        case .bear: "Bear"
        case .panda: "Panda"
        case .rabbit: "Rabbit"
        case .dog: "Dog"
        case .pig: "Pig"
        case .mouse: "Mouse"
        }
    }

    public static func color(_ color: LookColor) -> String {
        color == .natural ? "Its own colours" : color.rawValue.capitalized
    }

    public static func personality(_ personality: LookPersonality) -> String {
        personality.rawValue.capitalized
    }

    /// "A teal fox, cool": the whole look in a few words.
    public static func describe(_ look: KnownLook) -> String {
        let animal = Self.animal(look.animal).lowercased()
        let what = look.color == .natural ? "A \(animal) in its own colours" : "A \(look.color.rawValue) \(animal)"
        return "\(what), \(look.personality.rawValue)"
    }
}
