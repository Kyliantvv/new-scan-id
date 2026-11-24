"""Client du helper Swift `idvision serve` (protocole JSON lines) et moteur Apple Vision.

- démarrage paresseux du processus, qui garde Vision chargé entre les requêtes ;
- redémarrage automatique s'il meurt (la requête est rejouée une fois : elles sont toutes
  sans effet de bord) ;
- délai maximal par requête : au-delà, le processus est tué et l'erreur remontée.

Le texte reconnu ne passe jamais par le journal.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from idreader.core.config import Settings
from idreader.engines.base import Candidate, OcrLevel, Quad, Roi, TextLine

log = logging.getLogger(__name__)

_EOF = object()


class HelperError(Exception):
    """Erreur du helper, avec un code stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HelperUnavailable(HelperError):
    def __init__(self) -> None:
        super().__init__("HELPER_NOT_FOUND")


class HelperTimeout(HelperError):
    def __init__(self) -> None:
        super().__init__("HELPER_TIMEOUT")


class HelperCrashed(HelperError):
    def __init__(self, code: str = "HELPER_CRASHED") -> None:
        super().__init__(code)


class _Process:
    """Un processus `idvision serve` et son fil de lecture de stdout."""

    def __init__(self, command: Sequence[str]) -> None:
        try:
            self.proc: subprocess.Popen[str] = subprocess.Popen(  # noqa: S603 — commande fixe
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # journal du helper : identifiants et codes seulement
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise HelperUnavailable() from exc
        self.lines: queue.Queue[object] = queue.Queue()
        if self.proc.stdout is None or self.proc.stdin is None:
            raise HelperCrashed("HELPER_PIPE_ERROR")
        self.stdin: IO[str] = self.proc.stdin
        self.reader = threading.Thread(
            target=self._read, args=(self.proc.stdout,), name="idvision-reader", daemon=True
        )
        self.reader.start()

    def _read(self, stdout: IO[str]) -> None:
        for line in stdout:
            self.lines.put(line)
        self.lines.put(_EOF)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, line: str) -> None:
        try:
            self.stdin.write(line + "\n")
            self.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise HelperCrashed() from exc

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("idvision process did not exit after kill")
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


class VisionHelperClient:
    """Client thread-safe d'un processus `idvision serve`."""

    def __init__(self, command: Sequence[str], timeout_seconds: float = 60.0) -> None:
        self.command = list(command)
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._process: _Process | None = None
        self._next_id = 0
        self.starts = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> VisionHelperClient:
        return cls([str(settings.idvision_path), "serve"], settings.idvision_timeout_seconds)

    def __enter__(self) -> VisionHelperClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._process is not None:
                self._process.kill()
                self._process = None

    def _ensure_process(self) -> _Process:
        if self._process is None or not self._process.alive():
            if self._process is not None:
                log.warning("idvision process died, restarting")
                self._process.kill()
            self._process = _Process(self.command)
            self.starts += 1
        return self._process

    def request(self, op: str, **params: Any) -> dict[str, Any]:
        """Envoie une requête ; rejoue une fois si le processus est mort entre-temps."""
        with self._lock:
            try:
                return self._request_once(op, params)
            except HelperCrashed:
                log.warning("idvision crashed during op=%s, retrying once", op)
                return self._request_once(op, params)

    def _request_once(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        proc = self._ensure_process()
        self._next_id += 1
        req_id = str(self._next_id)
        proc.send(json.dumps({"id": req_id, "op": op, **params}, ensure_ascii=False))
        while True:
            try:
                item = proc.lines.get(timeout=self.timeout_seconds)
            except queue.Empty:
                log.error("idvision timeout op=%s id=%s", op, req_id)
                proc.kill()
                self._process = None
                raise HelperTimeout() from None
            if item is _EOF:
                proc.kill()
                self._process = None
                raise HelperCrashed()
            try:
                response = json.loads(str(item))
            except json.JSONDecodeError:
                proc.kill()
                self._process = None
                raise HelperCrashed("HELPER_PROTOCOL_ERROR") from None
            if not isinstance(response, dict) or response.get("id") != req_id:
                continue  # réponse orpheline d'une requête précédente : ignorée
            if not response.get("ok", False):
                raise HelperError(str(response.get("error", "HELPER_ERROR")))
            return response

    # Opérations

    def version(self) -> dict[str, Any]:
        return self.request("version")

    def languages(self) -> dict[str, Any]:
        return self.request("languages")

    def doctor(self) -> dict[str, Any]:
        return self.request("doctor")

    def recognize_text(
        self,
        image: Path,
        *,
        roi: Roi | None = None,
        languages: Sequence[str] = (),
        level: OcrLevel = "accurate",
        language_correction: bool = False,
        top_candidates: int = 1,
        min_text_height: float = 0.0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "image": str(image.resolve()),
            "level": level,
            "languages": list(languages),
            "language_correction": language_correction,
            "top_candidates": top_candidates,
            "min_text_height": min_text_height,
        }
        if roi is not None:
            params["roi"] = roi.as_list()
        return self.request("recognize_text", **params)


def parse_lines(response: dict[str, Any], engine: str = "vision") -> list[TextLine]:
    """Réponse `recognize_text` → lignes typées (coordonnées déjà en pixels, origine en haut)."""
    out: list[TextLine] = []
    for raw in response.get("lines", []):
        quad = raw["quad"]
        out.append(
            TextLine(
                text=raw["text"],
                confidence=raw["confidence"],
                quad=Quad(points=tuple((float(p[0]), float(p[1])) for p in quad)),
                candidates=tuple(
                    Candidate(text=c["text"], confidence=c["confidence"])
                    for c in raw.get("candidates", [])
                ),
                engine=engine,
            )
        )
    return out


class AppleVisionEngine:
    """Moteur OCR n° 1 : Apple Vision via `idvision`."""

    name = "vision"

    def __init__(self, client: VisionHelperClient) -> None:
        self.client = client

    def recognize(
        self,
        image: Path,
        *,
        roi: Roi | None = None,
        languages: Sequence[str] = (),
        level: OcrLevel = "accurate",
        top_candidates: int = 1,
    ) -> list[TextLine]:
        # language_correction toujours désactivée : elle « corrige » les noms propres
        # (docs/03_PIPELINE_ANALYSE.md § G).
        response = self.client.recognize_text(
            image, roi=roi, languages=languages, level=level, top_candidates=top_candidates
        )
        return parse_lines(response, self.name)


def probe_helper(settings: Settings) -> dict[str, Any] | None:
    """Diagnostic au démarrage du worker : `doctor` si le helper est présent, sinon None."""
    if not settings.idvision_path.is_file():
        return None
    try:
        with VisionHelperClient.from_settings(settings) as client:
            return client.doctor()
    except HelperError as exc:
        log.warning("idvision doctor failed code=%s", exc.code)
        return None
