// Aiguillage d'une ligne de requête vers le backend, et mise en forme de la réponse.

import Foundation

/// Résultat d'une requête : la ligne JSON à écrire, et de quoi journaliser sans donnée.
public struct HandledLine: Sendable {
    public let json: String
    public let id: String
    public let op: String
    public let ok: Bool
    public let errorCode: String?
    public let ms: Int
}

private struct Envelope<Payload: Encodable>: Encodable {
    let id: String
    let ok: Bool
    let ms: Int
    let payload: Payload

    func encode(to encoder: Encoder) throws {
        // Aplatit `payload` à côté de id/ok/ms : {"id":…,"ok":true,"ms":…,"lines":[…]}
        try payload.encode(to: encoder)
        var c = encoder.container(keyedBy: Keys.self)
        try c.encode(id, forKey: .id)
        try c.encode(ok, forKey: .ok)
        try c.encode(ms, forKey: .ms)
    }

    enum Keys: String, CodingKey { case id, ok, ms }
}

private struct ErrorBody: Encodable {
    let id: String
    let ok = false
    let error: String
}

public struct Dispatcher: Sendable {
    let backend: any VisionBackend

    public init(backend: any VisionBackend) {
        self.backend = backend
    }

    public func handle(line: String) async -> HandledLine {
        let start = DispatchTime.now().uptimeNanoseconds
        func elapsed() -> Int { Int((DispatchTime.now().uptimeNanoseconds - start) / 1_000_000) }

        guard let data = line.data(using: .utf8),
            let request = try? JSONLines.decoder().decode(HelperRequest.self, from: data)
        else {
            let id = Self.bestEffortId(line)
            return Self.error(id: id, op: "?", code: .badRequest, ms: elapsed())
        }
        do {
            let json: String
            switch request.op {
            case "version":
                json = try Self.ok(request.id, elapsed(), backend.version())
            case "languages":
                json = try Self.ok(request.id, elapsed(), backend.languages())
            case "doctor":
                json = try Self.ok(request.id, elapsed(), backend.doctor())
            case "recognize_text":
                let params = try RecognizeTextParams.from(request)
                let out = try await backend.recognizeText(params)
                json = try Self.ok(request.id, elapsed(), out)
            case "detect_documents", "detect_barcodes", "detect_faces", "recognize_document",
                "decode_image":
                throw HelperError.notImplemented  // phases suivantes
            default:
                throw HelperError.unknownOp
            }
            return HandledLine(
                json: json, id: request.id, op: request.op, ok: true, errorCode: nil, ms: elapsed())
        } catch let e as HelperError {
            return Self.error(id: request.id, op: request.op, code: e, ms: elapsed())
        } catch {
            return Self.error(id: request.id, op: request.op, code: .visionFailed, ms: elapsed())
        }
    }

    static func ok<P: Encodable>(_ id: String, _ ms: Int, _ payload: P) throws -> String {
        let data = try JSONLines.encoder().encode(Envelope(id: id, ok: true, ms: ms, payload: payload))
        return String(decoding: data, as: UTF8.self)
    }

    static func error(id: String, op: String, code: HelperError, ms: Int) -> HandledLine {
        let body = ErrorBody(id: id, error: code.rawValue)
        let data = (try? JSONLines.encoder().encode(body)) ?? Data("{\"ok\":false}".utf8)
        return HandledLine(
            json: String(decoding: data, as: UTF8.self), id: id, op: op, ok: false,
            errorCode: code.rawValue, ms: ms)
    }

    /// Récupère l'identifiant d'une requête mal formée, pour que le client puisse l'apparier.
    static func bestEffortId(_ line: String) -> String {
        struct OnlyId: Decodable { let id: String }
        if let data = line.data(using: .utf8), let parsed = try? JSONDecoder().decode(OnlyId.self, from: data)
        {
            return parsed.id
        }
        return ""
    }
}
