// swift-tools-version: 6.0
// Helper « idvision » : OCR Apple Vision pour IDReader (docs/02_ARCHITECTURE.md § Helper Swift).
import PackageDescription

let package = Package(
    name: "idvision",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "idvision", targets: ["idvision"])
    ],
    targets: [
        // Cœur sans dépendance à Vision : protocole JSON lines, géométrie, aiguillage.
        .target(name: "IdVisionCore"),
        // Exécutable : implémentation Apple Vision + ligne de commande.
        .executableTarget(name: "idvision", dependencies: ["IdVisionCore"]),
        .testTarget(name: "IdVisionCoreTests", dependencies: ["IdVisionCore"]),
    ]
)
