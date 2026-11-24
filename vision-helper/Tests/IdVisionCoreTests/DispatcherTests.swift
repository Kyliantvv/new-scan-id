import Foundation
import Testing

@testable import IdVisionCore

/// Faux backend : aucune dépendance à Vision, réponses fixes (spécimen ICAO « UTO »).
struct FakeBackend: VisionBackend {
    func version() -> VersionInfo { VersionInfo(version: "test", macos: "Version 15.0") }

    func languages() throws(HelperError) -> LanguagesInfo {
        LanguagesInfo(fast: ["en-US"], accurate: ["en-US", "fr-FR"])
    }

    func doctor() throws(HelperError) -> DoctorInfo {
        DoctorInfo(
            version: "test", macos: "Version 15.0", macosVersion: [15, 0, 0],
            recognizeTextRevision: "revision3", languages: try languages(),
            recognizeDocumentsAvailable: false)
    }

    func recognizeText(_ params: RecognizeTextParams) async throws(HelperError) -> RecognizeTextOutput {
        if params.image == "/absent.png" { throw .imageNotFound }
        let line = TextLine(
            text: "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", confidence: 0.5,
            quad: [[0, 0], [10, 0], [10, 2], [0, 2]],
            candidates: [TextCandidate(text: "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", confidence: 0.5)])
        return RecognizeTextOutput(width: 10, height: 10, lines: [line])
    }
}

func decode(_ s: String) throws -> [String: Any] {
    let obj = try JSONSerialization.jsonObject(with: Data(s.utf8))
    return try #require(obj as? [String: Any])
}

@Suite("Protocole JSON lines")
struct DispatcherTests {
    let dispatcher = Dispatcher(backend: FakeBackend())

    @Test("version : réponse aplatie avec id, ok et ms")
    func version() async throws {
        let h = await dispatcher.handle(line: #"{"id":"1","op":"version"}"#)
        let json = try decode(h.json)
        #expect(json["id"] as? String == "1")
        #expect(json["ok"] as? Bool == true)
        #expect(json["version"] as? String == "test")
        #expect(json["ms"] is Int)
        #expect(!h.json.contains("\n"))
    }

    @Test("recognize_text : clés en snake_case, lignes et quadrilatères")
    func recognizeText() async throws {
        let req = #"{"id":"4","op":"recognize_text","image":"/abs/crop.png","level":"accurate","languages":["en-US"],"language_correction":false,"top_candidates":5}"#
        let h = await dispatcher.handle(line: req)
        let json = try decode(h.json)
        #expect(json["ok"] as? Bool == true)
        let lines = try #require(json["lines"] as? [[String: Any]])
        #expect(lines.count == 1)
        #expect((lines[0]["quad"] as? [[Double]])?.count == 4)
    }

    @Test("Erreurs : codes stables")
    func errors() async throws {
        let notFound = await dispatcher.handle(line: #"{"id":"5","op":"recognize_text","image":"/absent.png"}"#)
        #expect(try decode(notFound.json)["error"] as? String == "IMAGE_NOT_FOUND")
        let unknown = await dispatcher.handle(line: #"{"id":"6","op":"magie"}"#)
        #expect(try decode(unknown.json)["error"] as? String == "UNKNOWN_OP")
        let badLevel = await dispatcher.handle(line: #"{"id":"7","op":"recognize_text","image":"/a.png","level":"turbo"}"#)
        #expect(try decode(badLevel.json)["error"] as? String == "INVALID_LEVEL")
        let later = await dispatcher.handle(line: #"{"id":"8","op":"detect_documents","image":"/a.png"}"#)
        #expect(try decode(later.json)["error"] as? String == "NOT_IMPLEMENTED")
    }

    @Test("Requête illisible : BAD_REQUEST, identifiant récupéré si possible")
    func badRequest() async throws {
        let h = await dispatcher.handle(line: #"{"id":"9"}"#)
        let json = try decode(h.json)
        #expect(json["error"] as? String == "BAD_REQUEST")
        #expect(json["id"] as? String == "9")
        let garbage = await dispatcher.handle(line: "pas du json")
        #expect(try decode(garbage.json)["error"] as? String == "BAD_REQUEST")
    }
}
