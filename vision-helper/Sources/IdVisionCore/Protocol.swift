// Protocole JSON lines du mode `serve` : une requête par ligne sur stdin, une réponse par
// ligne sur stdout. Les journaux vont sur stderr et ne contiennent jamais de texte reconnu.

import Foundation

public let helperVersion = "0.1.0"

/// Codes d'erreur stables (jamais de donnée dans un message d'erreur).
public enum HelperError: String, Error, Sendable {
    case badRequest = "BAD_REQUEST"
    case unknownOp = "UNKNOWN_OP"
    case imageNotFound = "IMAGE_NOT_FOUND"
    case imageUnreadable = "IMAGE_UNREADABLE"
    case invalidROI = "INVALID_ROI"
    case invalidLevel = "INVALID_LEVEL"
    case unsupportedOS = "UNSUPPORTED_OS"
    case notImplemented = "NOT_IMPLEMENTED"
    case visionFailed = "VISION_FAILED"
}

public enum RecognitionLevel: String, Sendable, Codable {
    case fast
    case accurate
}

public struct HelperRequest: Decodable, Sendable {
    public let id: String
    public let op: String
    public let image: String?
    public let roi: [Double]?
    public let level: String?
    public let languages: [String]?
    public let languageCorrection: Bool?
    public let topCandidates: Int?
    public let minTextHeight: Double?
}

/// Paramètres validés de `recognize_text`.
public struct RecognizeTextParams: Sendable, Equatable {
    public let image: String
    public let roi: [Double]?
    public let level: RecognitionLevel
    public let languages: [String]
    public let languageCorrection: Bool
    public let topCandidates: Int
    public let minTextHeight: Double

    public init(
        image: String, roi: [Double]? = nil, level: RecognitionLevel = .accurate,
        languages: [String] = [], languageCorrection: Bool = false, topCandidates: Int = 1,
        minTextHeight: Double = 0
    ) {
        self.image = image
        self.roi = roi
        self.level = level
        self.languages = languages
        self.languageCorrection = languageCorrection
        self.topCandidates = topCandidates
        self.minTextHeight = minTextHeight
    }

    public static func from(_ r: HelperRequest) throws(HelperError) -> RecognizeTextParams {
        guard let image = r.image, !image.isEmpty else { throw .badRequest }
        let level: RecognitionLevel
        if let raw = r.level {
            guard let parsed = RecognitionLevel(rawValue: raw) else { throw .invalidLevel }
            level = parsed
        } else {
            level = .accurate
        }
        let top = r.topCandidates ?? 1
        guard (1...10).contains(top) else { throw .badRequest }
        let minHeight = r.minTextHeight ?? 0
        guard minHeight >= 0, minHeight <= 1 else { throw .badRequest }
        return RecognizeTextParams(
            image: image, roi: r.roi, level: level, languages: r.languages ?? [],
            languageCorrection: r.languageCorrection ?? false, topCandidates: top,
            minTextHeight: minHeight)
    }
}

public struct TextCandidate: Codable, Sendable, Equatable {
    public let text: String
    public let confidence: Double

    public init(text: String, confidence: Double) {
        self.text = text
        self.confidence = confidence
    }
}

public struct TextLine: Codable, Sendable, Equatable {
    public let text: String
    public let confidence: Double
    /// Pixels, origine en haut à gauche : haut-gauche, haut-droite, bas-droite, bas-gauche.
    public let quad: [[Double]]
    public let candidates: [TextCandidate]

    public init(text: String, confidence: Double, quad: [[Double]], candidates: [TextCandidate]) {
        self.text = text
        self.confidence = confidence
        self.quad = quad
        self.candidates = candidates
    }
}

public struct RecognizeTextOutput: Codable, Sendable, Equatable {
    public let width: Int
    public let height: Int
    public let lines: [TextLine]

    public init(width: Int, height: Int, lines: [TextLine]) {
        self.width = width
        self.height = height
        self.lines = lines
    }
}

public struct VersionInfo: Codable, Sendable, Equatable {
    public let version: String
    public let macos: String

    public init(version: String, macos: String) {
        self.version = version
        self.macos = macos
    }
}

public struct LanguagesInfo: Codable, Sendable, Equatable {
    public let fast: [String]
    public let accurate: [String]

    public init(fast: [String], accurate: [String]) {
        self.fast = fast
        self.accurate = accurate
    }
}

public struct DoctorInfo: Codable, Sendable, Equatable {
    public let version: String
    public let macos: String
    public let macosVersion: [Int]
    public let recognizeTextRevision: String
    public let languages: LanguagesInfo
    /// `RecognizeDocumentsRequest` (macOS 26 et plus).
    public let recognizeDocumentsAvailable: Bool

    public init(
        version: String, macos: String, macosVersion: [Int], recognizeTextRevision: String,
        languages: LanguagesInfo, recognizeDocumentsAvailable: Bool
    ) {
        self.version = version
        self.macos = macos
        self.macosVersion = macosVersion
        self.recognizeTextRevision = recognizeTextRevision
        self.languages = languages
        self.recognizeDocumentsAvailable = recognizeDocumentsAvailable
    }
}

/// Ce que l'exécutable fournit (Apple Vision) ; remplaçable par un faux dans les tests.
public protocol VisionBackend: Sendable {
    func version() -> VersionInfo
    func languages() throws(HelperError) -> LanguagesInfo
    func doctor() throws(HelperError) -> DoctorInfo
    func recognizeText(_ params: RecognizeTextParams) async throws(HelperError) -> RecognizeTextOutput
}

public enum JSONLines {
    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    public static func encoder(pretty: Bool = false) -> JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            : [.sortedKeys, .withoutEscapingSlashes]
        return e
    }
}
