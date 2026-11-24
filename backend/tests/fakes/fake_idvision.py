"""Faux `idvision serve` : même protocole JSON lines, sans Apple Vision.

Comportement réglé par variables d'environnement :
- FAKE_IDVISION_MODE = normal | crash_once | crash_always | hang | garbage | stale_reply
- FAKE_IDVISION_STATE = fichier témoin (pour `crash_once` : survit au redémarrage)

Renvoie la première ligne MRZ du spécimen public ICAO 9303 « UTO ».
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SPECIMEN = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"


def reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    mode = os.environ.get("FAKE_IDVISION_MODE", "normal")
    state = Path(os.environ.get("FAKE_IDVISION_STATE", "fake-idvision.state"))
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        rid, op = req["id"], req["op"]
        if op == "version":
            reply({"id": rid, "ok": True, "ms": 0, "version": "fake", "macos": "Fake 15.0"})
        elif op == "languages":
            reply(
                {"id": rid, "ok": True, "ms": 0, "fast": ["en-US"], "accurate": ["en-US", "fr-FR"]}
            )
        elif op == "doctor":
            reply(
                {
                    "id": rid,
                    "ok": True,
                    "ms": 0,
                    "version": "fake",
                    "macos": "Fake 15.0",
                    "macos_version": [15, 0, 0],
                    "recognize_text_revision": "fake",
                    "languages": {"fast": ["en-US"], "accurate": ["en-US", "fr-FR"]},
                    "recognize_documents_available": False,
                }
            )
        elif op == "recognize_text":
            if mode == "crash_always" or (mode == "crash_once" and not state.exists()):
                state.write_text("crashed")
                os._exit(3)
            if mode == "hang":
                time.sleep(3600)
            if mode == "garbage":
                sys.stdout.write("ceci n'est pas du json\n")
                sys.stdout.flush()
                continue
            if mode == "stale_reply":
                reply({"id": "ancien", "ok": True, "lines": []})
            if not Path(req["image"]).is_file():
                reply({"id": rid, "ok": False, "error": "IMAGE_NOT_FOUND"})
                continue
            x, y = (req.get("roi") or [0, 0])[:2]
            quad = [[x + 10, y + 20], [x + 890, y + 20], [x + 890, y + 60], [x + 10, y + 60]]
            reply(
                {
                    "id": rid,
                    "ok": True,
                    "ms": 1,
                    "width": 1000,
                    "height": 700,
                    "lines": [
                        {
                            "text": SPECIMEN,
                            "confidence": 0.5,
                            "quad": quad,
                            "candidates": [{"text": SPECIMEN, "confidence": 0.5}],
                        }
                    ],
                }
            )
        else:
            reply({"id": rid, "ok": False, "error": "UNKNOWN_OP"})


if __name__ == "__main__":
    main()
