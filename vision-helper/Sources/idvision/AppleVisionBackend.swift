// Implémentation Apple Vision (API Swift de macOS 15 : RecognizeTextRequest).

import CoreGraphics
import Foundation
import IdVisionCore
import ImageIO
import Vision

struct AppleVisionBackend: VisionBackend {
    func version() -> VersionInfo {
        VersionInfo(version: helperVersion, macos: Self.macOSString())
    }

    func languages() throws(HelperError) -> LanguagesInfo {
        LanguagesInfo(
            fast: Self.supportedLanguages(level: .fast),
            accurate: Self.supportedLanguages(level: .accurate))
    }

    func doctor() throws(HelperError) -> DoctorInfo {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        let request = RecognizeTextRequest()
        var documents = false
        if #available(macOS 26, *) {
            documents = true
        }
        return DoctorInfo(
            version: helperVersion,
            macos: Self.macOSString(),
            macosVersion: [v.majorVersion, v.minorVersion, v.patchVersion],
            recognizeTextRevision: String(describing: request.revision),
            languages: try languages(),
            recognizeDocumentsAvailable: documents)
    }

    func recognizeText(_ params: RecognizeTextParams) async throws(HelperError) -> RecognizeTextOutput {
        let image = try Self.loadImage(path: params.image)
        let fullWidth = image.width
        let fullHeight = image.height

        // Zone d'intérêt : on recadre l'image plutôt que d'utiliser `regionOfInterest`, pour que
        // la conversion de coordonnées ne dépende que de nous (décalage + taille du recadrage).
        var target = image
        var offsetX = 0.0
        var offsetY = 0.0
        if let roi = params.roi {
            let rect = try validateROI(roi, imageWidth: fullWidth, imageHeight: fullHeight)
            // CGImage.cropping(to:) : repère en pixels, origine en haut à gauche de l'image.
            let cg = CGRect(x: rect.x, y: rect.y, width: rect.width, height: rect.height)
            guard let cropped = image.cropping(to: cg) else { throw .invalidROI }
            target = cropped
            offsetX = Double(rect.x)
            offsetY = Double(rect.y)
        }

        var request = RecognizeTextRequest()
        request.recognitionLevel = params.level == .fast ? .fast : .accurate
        request.usesLanguageCorrection = params.languageCorrection
        if !params.languages.isEmpty {
            request.recognitionLanguages = params.languages.map { Locale.Language(identifier: $0) }
        }
        request.minimumTextHeightFraction = Float(params.minTextHeight)

        let observations: [RecognizedTextObservation]
        do {
            observations = try await request.perform(on: target)
        } catch {
            throw .visionFailed
        }

        let size = PixelSize(width: Double(target.width), height: Double(target.height))
        let lines: [TextLine] = observations.compactMap { obs in
            let candidates = obs.topCandidates(params.topCandidates)
            guard let best = candidates.first else { return nil }
            let quad = visionQuadToPixels(
                topLeft: Self.norm(obs.topLeft), topRight: Self.norm(obs.topRight),
                bottomRight: Self.norm(obs.bottomRight), bottomLeft: Self.norm(obs.bottomLeft),
                size: size, offsetX: offsetX, offsetY: offsetY)
            return TextLine(
                text: best.string,
                confidence: Double(best.confidence),
                quad: quad,
                candidates: candidates.map {
                    TextCandidate(text: $0.string, confidence: Double($0.confidence))
                })
        }
        return RecognizeTextOutput(width: fullWidth, height: fullHeight, lines: lines)
    }

    // MARK: - Outils

    static func norm(_ p: NormalizedPoint) -> NormPoint {
        NormPoint(x: Double(p.x), y: Double(p.y))
    }

    static func macOSString() -> String {
        ProcessInfo.processInfo.operatingSystemVersionString
    }

    static func supportedLanguages(level: RecognizeTextRequest.RecognitionLevel) -> [String] {
        var request = RecognizeTextRequest()
        request.recognitionLevel = level
        return request.supportedRecognitionLanguages.map(languageIdentifier)
    }

    /// Identifiant de type « fr-FR », « zh-Hans » (langue, écriture, région si présentes).
    static func languageIdentifier(_ language: Locale.Language) -> String {
        [
            language.languageCode?.identifier,
            language.script?.identifier,
            language.region?.identifier,
        ].compactMap { $0 }.joined(separator: "-")
    }

    /// Charge les pixels tels que stockés (l'orientation EXIF est déjà appliquée par le
    /// pipeline, qui ne transmet que des PNG normalisés).
    static func loadImage(path: String) throws(HelperError) -> CGImage {
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else { throw .imageNotFound }
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw .imageUnreadable }
        return image
    }
}
