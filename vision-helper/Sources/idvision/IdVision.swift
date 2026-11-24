// Point d'entrée : sous-commandes version, languages, doctor, serve, recognize-text.

import Foundation
import IdVisionCore

@main
struct IdVision {
    static let usage = """
        Utilisation : idvision <commande>
          version                      version du helper et de macOS (JSON)
          languages                    langues Vision disponibles par niveau (JSON)
          doctor                       diagnostic : macOS, révision, langues (JSON)
          serve                        mode JSON lines (stdin → stdout)
          recognize-text <image> [--languages fr-FR,en-US] [--level fast|accurate]
                         [--roi x,y,w,h] [--top N] [--language-correction]
        """

    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let command = args.first else {
            fail(usage, code: 64)
        }
        let backend = AppleVisionBackend()
        do {
            switch command {
            case "version":
                try printJSON(backend.version())
            case "languages":
                try printJSON(backend.languages())
            case "doctor":
                try printJSON(backend.doctor(), pretty: true)
            case "serve":
                await serve(backend: backend)
            case "recognize-text":
                let params = try parseRecognizeText(Array(args.dropFirst()))
                let output = try await backend.recognizeText(params)
                try printJSON(output, pretty: true)
            case "-h", "--help", "help":
                print(usage)
            default:
                fail(usage, code: 64)
            }
        } catch let e as HelperError {
            fail("erreur : \(e.rawValue)", code: 1)
        } catch {
            fail("erreur : INTERNAL", code: 1)
        }
    }

    /// Boucle JSON lines. Une requête à la fois ; Vision reste chargé entre les requêtes.
    static func serve(backend: AppleVisionBackend) async {
        let dispatcher = Dispatcher(backend: backend)
        logErr("idvision serve start version=\(helperVersion)")
        while let line = readLine(strippingNewline: true) {
            if line.trimmingCharacters(in: .whitespaces).isEmpty { continue }
            let handled = await dispatcher.handle(line: line)
            // FileHandle : écriture non bufferisée, indispensable quand stdout est un tube.
            FileHandle.standardOutput.write(Data((handled.json + "\n").utf8))
            // Journal : identifiants et codes seulement, jamais le texte reconnu ni le chemin.
            logErr(
                "idvision op=\(handled.op) id=\(handled.id) ok=\(handled.ok) "
                    + "ms=\(handled.ms) error=\(handled.errorCode ?? "-")")
        }
        logErr("idvision serve stop")
    }

    static func parseRecognizeText(_ args: [String]) throws(HelperError) -> RecognizeTextParams {
        var image: String?
        var languages: [String] = []
        var level = RecognitionLevel.accurate
        var roi: [Double]?
        var top = 1
        var correction = false
        var i = 0
        while i < args.count {
            let a = args[i]
            func value() throws(HelperError) -> String {
                i += 1
                guard i < args.count else { throw .badRequest }
                return args[i]
            }
            switch a {
            case "--languages":
                languages = try value().split(separator: ",").map(String.init)
            case "--level":
                guard let l = RecognitionLevel(rawValue: try value()) else { throw .invalidLevel }
                level = l
            case "--roi":
                let parts = try value().split(separator: ",").compactMap { Double($0) }
                guard parts.count == 4 else { throw .invalidROI }
                roi = parts
            case "--top":
                guard let n = Int(try value()), (1...10).contains(n) else { throw .badRequest }
                top = n
            case "--language-correction":
                correction = true
            default:
                guard image == nil, !a.hasPrefix("--") else { throw .badRequest }
                image = a
            }
            i += 1
        }
        guard let image else { throw .badRequest }
        let absolute = URL(fileURLWithPath: image).standardizedFileURL.path
        return RecognizeTextParams(
            image: absolute, roi: roi, level: level, languages: languages,
            languageCorrection: correction, topCandidates: top)
    }

    static func printJSON<T: Encodable>(_ value: T, pretty: Bool = false) throws {
        let data = try JSONLines.encoder(pretty: pretty).encode(value)
        FileHandle.standardOutput.write(data + Data("\n".utf8))
    }

    static func logErr(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
    }

    static func fail(_ message: String, code: Int32) -> Never {
        logErr(message)
        exit(code)
    }
}
