// Conversion des coordonnées Apple Vision vers le repère d'IDReader.
//
// Vision : coordonnées normalisées [0, 1], origine en BAS à gauche.
// IDReader : pixels, origine en HAUT à gauche (CLAUDE.md § Conventions). La conversion se fait
// uniquement ici.

public struct PixelSize: Sendable, Equatable {
    public let width: Double
    public let height: Double

    public init(width: Double, height: Double) {
        self.width = width
        self.height = height
    }
}

/// Rectangle en pixels, origine en haut à gauche.
public struct PixelRect: Sendable, Equatable {
    public let x: Int
    public let y: Int
    public let width: Int
    public let height: Int

    public init(x: Int, y: Int, width: Int, height: Int) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

/// Point normalisé de Vision (origine en bas à gauche).
public struct NormPoint: Sendable, Equatable {
    public let x: Double
    public let y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

/// Convertit un point normalisé Vision, exprimé dans une zone de taille `size` placée en
/// `offset` (pixels, origine en haut à gauche) dans l'image, en pixels de l'image.
public func visionToPixels(_ p: NormPoint, size: PixelSize, offsetX: Double = 0, offsetY: Double = 0)
    -> [Double]
{
    [offsetX + p.x * size.width, offsetY + (1.0 - p.y) * size.height]
}

/// Quadrilatère dans l'ordre haut-gauche, haut-droite, bas-droite, bas-gauche (sens horaire à
/// l'écran). Les coins sont ceux que Vision désigne visuellement (`topLeft`…).
public func visionQuadToPixels(
    topLeft: NormPoint, topRight: NormPoint, bottomRight: NormPoint, bottomLeft: NormPoint,
    size: PixelSize, offsetX: Double = 0, offsetY: Double = 0
) -> [[Double]] {
    [topLeft, topRight, bottomRight, bottomLeft].map {
        visionToPixels($0, size: size, offsetX: offsetX, offsetY: offsetY)
    }
}

/// Valide une zone d'intérêt `[x, y, w, h]` en pixels et la ramène aux limites de l'image.
public func validateROI(_ roi: [Double], imageWidth: Int, imageHeight: Int) throws(HelperError)
    -> PixelRect
{
    guard roi.count == 4, roi.allSatisfy({ $0.isFinite }) else { throw .invalidROI }
    let x0 = max(0, Int(roi[0].rounded(.down)))
    let y0 = max(0, Int(roi[1].rounded(.down)))
    let x1 = min(imageWidth, Int((roi[0] + roi[2]).rounded(.up)))
    let y1 = min(imageHeight, Int((roi[1] + roi[3]).rounded(.up)))
    guard x1 > x0, y1 > y0 else { throw .invalidROI }
    return PixelRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0)
}
