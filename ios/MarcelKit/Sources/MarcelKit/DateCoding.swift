import Foundation
import OpenAPIRuntime

/// The hub's timestamps are RFC 3339, with or without fractional seconds. Both must decode.
enum HubDates {
    static func parse(_ text: String) -> Date? {
        let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        let plain = Date.ISO8601FormatStyle()
        return (try? withFraction.parse(text)) ?? (try? plain.parse(text))
    }

    static func format(_ date: Date) -> String {
        date.formatted(Date.ISO8601FormatStyle())
    }

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = parse(text) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Not an RFC 3339 date: \(text)")
            }
            return date
        }
        return decoder
    }

    static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(format(date))
        }
        return encoder
    }
}

/// The same rule for the generated REST client.
struct HubDateTranscoder: DateTranscoder {
    func encode(_ date: Date) throws -> String { HubDates.format(date) }

    func decode(_ text: String) throws -> Date {
        guard let date = HubDates.parse(text) else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: "Not an RFC 3339 date: \(text)"))
        }
        return date
    }
}
