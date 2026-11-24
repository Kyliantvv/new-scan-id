import Testing

@testable import IdVisionCore

@Suite("Conversion des coordonnées Vision → pixels (origine en haut à gauche)")
struct GeometryTests {
    let size = PixelSize(width: 2000, height: 1000)

    @Test("Le coin bas-gauche normalisé (0, 0) devient le bas de l'image")
    func originFlipsVertically() {
        #expect(visionToPixels(NormPoint(x: 0, y: 0), size: size) == [0, 1000])
        #expect(visionToPixels(NormPoint(x: 0, y: 1), size: size) == [0, 0])
        #expect(visionToPixels(NormPoint(x: 1, y: 1), size: size) == [2000, 0])
        #expect(visionToPixels(NormPoint(x: 0.25, y: 0.75), size: size) == [500, 250])
    }

    @Test("Le décalage d'une zone d'intérêt est ajouté en pixels")
    func offsetIsApplied() {
        let crop = PixelSize(width: 100, height: 50)
        let p = visionToPixels(NormPoint(x: 0.5, y: 1), size: crop, offsetX: 300, offsetY: 700)
        #expect(p == [350, 700])
    }

    @Test("Quadrilatère dans l'ordre HG, HD, BD, BG avec y croissant vers le bas")
    func quadOrder() {
        // Ligne de texte occupant x ∈ [0,1 ; 0,9], y Vision ∈ [0,1 ; 0,2] (bas de l'image).
        let quad = visionQuadToPixels(
            topLeft: NormPoint(x: 0.1, y: 0.2), topRight: NormPoint(x: 0.9, y: 0.2),
            bottomRight: NormPoint(x: 0.9, y: 0.1), bottomLeft: NormPoint(x: 0.1, y: 0.1),
            size: size)
        #expect(quad.count == 4)
        #expect(quad[0][1] < quad[3][1])  // le haut est au-dessus du bas
        #expect(quad[0][0] < quad[1][0])  // la gauche est à gauche de la droite
        #expect(abs(quad[0][1] - 800) < 1e-9)
        #expect(abs(quad[2][1] - 900) < 1e-9)
    }

    @Test("Zone d'intérêt ramenée aux limites de l'image")
    func roiClamped() throws {
        let r = try validateROI([-10, 20.4, 5000, 100.2], imageWidth: 2000, imageHeight: 1000)
        #expect(r == PixelRect(x: 0, y: 20, width: 2000, height: 101))
    }

    @Test("Zone d'intérêt invalide refusée")
    func roiInvalid() {
        #expect(throws: HelperError.invalidROI) {
            try validateROI([1, 2, 3], imageWidth: 10, imageHeight: 10)
        }
        #expect(throws: HelperError.invalidROI) {
            try validateROI([20, 20, 5, 5], imageWidth: 10, imageHeight: 10)
        }
        #expect(throws: HelperError.invalidROI) {
            try validateROI([0, 0, .nan, 5], imageWidth: 10, imageHeight: 10)
        }
    }
}
