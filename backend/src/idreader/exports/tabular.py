"""Exports XLSX et CSV du tableau (docs/01_SPEC_FONCTIONNELLE.md § Exports).

- XLSX (openpyxl) : vraies cellules date (format JJ/MM/AAAA), ligne d'en-tête figée, filtre
  automatique.
- CSV : UTF-8 avec BOM, séparateur `;`, dates JJ/MM/AAAA (ouverture directe dans Excel en
  français).
- Protection contre l'injection de formules : une valeur qui commence par = + - @ (ou une
  tabulation / un retour chariot) est préfixée d'une apostrophe.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

DOC_STATUS = {
    "pending": "En attente",
    "processing": "En cours",
    "auto_validated": "Validé (auto)",
    "to_review": "À vérifier",
    "manual_validated": "Validé (manuel)",
    "failed": "Échec",
    "duplicate": "Doublon",
}
DOC_TYPES = {
    "passport": "Passeport",
    "id_card": "Carte d'identité",
    "residence_permit": "Titre de séjour",
    "visa": "Visa",
    "travel_document": "Document de voyage",
    "other": "Autre",
}
ALERTS = {
    "EXPIRED": "Expiré",
    "MINOR": "Mineur",
    "EXPIRES_SOON": "Expire bientôt",
    "LOW_QUALITY": "Qualité faible",
    "DUPLICATE_DOCUMENT": "Doublon",
    "UNUSUAL_NUMBER": "Numéro inhabituel",
    "ISSUE_VALIDITY_UNUSUAL": "Durée de validité inhabituelle",
    "ISSUE_DATE_INCONSISTENT": "Date de délivrance incohérente",
    "MRZ_NAME_TRUNCATED": "Nom tronqué dans la MRZ",
}

# Ordre et libellés des colonnes (identiques au tableau).
COLUMNS: dict[str, str] = {
    "status": "Statut",
    "doc_type": "Type",
    "issuing_state": "Pays émetteur",
    "surname": "Nom",
    "given_names": "Prénoms",
    "surname_native": "Nom (écriture d'origine)",
    "usage_name": "Nom d'usage",
    "sex": "Sexe",
    "date_of_birth": "Date de naissance",
    "place_of_birth": "Lieu de naissance",
    "nationality": "Nationalité",
    "document_number": "N° du document",
    "date_of_issue": "Date de délivrance",
    "date_of_expiry": "Date d'expiration",
    "authority": "Autorité de délivrance",
    "personal_number": "N° personnel",
    "mrz_valid": "MRZ",
    "confidence": "Confiance",
    "alerts_json": "Alertes",
    "source_file": "Fichier source",
    "created_at": "Analysé le",
}
STATUS_COLUMNS = frozenset({"status", "mrz_valid", "confidence", "alerts_json"})
DATE_COLUMNS = frozenset({"date_of_birth", "date_of_issue", "date_of_expiry"})
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def choose_columns(requested: Sequence[str] | None, include_status: bool) -> list[str]:
    wanted = [c for c in (requested or COLUMNS) if c in COLUMNS]
    if not wanted:
        wanted = list(COLUMNS)
    if not include_status:
        wanted = [c for c in wanted if c not in STATUS_COLUMNS]
    elif "status" not in wanted:
        wanted.insert(0, "status")
    return wanted


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def cell_value(column: str, row: dict[str, Any]) -> Any:
    """Valeur typée : `date` pour les dates, nombres pour la confiance, texte sinon."""
    v = row.get(column)
    if column in DATE_COLUMNS:
        return _date(v)
    if column == "created_at":
        if isinstance(v, datetime):
            return v.replace(tzinfo=None)
        try:
            return datetime.fromisoformat(str(v)).replace(tzinfo=None) if v else None
        except ValueError:
            return None
    if column == "status":
        return DOC_STATUS.get(str(v), str(v or ""))
    if column == "doc_type":
        return DOC_TYPES.get(str(v), str(v or ""))
    if column == "mrz_valid":
        return "Absente" if v is None else ("Valide" if v else "Invalide")
    if column == "confidence":
        return None if v is None else round(float(v) * 100)
    if column == "alerts_json":
        return ", ".join(ALERTS.get(str(a), str(a)) for a in (v or []))
    if column == "source_file" and v:
        page = row.get("page_index")
        return f"{v} (p. {int(page) + 1})" if page is not None else str(v)
    return None if v is None else str(v)


def safe_text(value: str) -> str:
    return "'" + value if value.startswith(DANGEROUS) else value


def to_xlsx(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    if ws is None:  # classeur neuf : toujours une feuille active
        ws = wb.create_sheet()
    ws.title = "Documents"
    ws.append([COLUMNS[c] for c in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        values = []
        for c in columns:
            v = cell_value(c, row)
            values.append(safe_text(v) if isinstance(v, str) else v)
        ws.append(values)
    for i, c in enumerate(columns, start=1):
        letter = get_column_letter(i)
        if c in DATE_COLUMNS:
            for cell in ws[letter][1:]:
                cell.number_format = "DD/MM/YYYY"
        elif c == "created_at":
            for cell in ws[letter][1:]:
                cell.number_format = "DD/MM/YYYY HH:MM"
        width = max([len(COLUMNS[c]), *(len(str(x.value or "")) for x in ws[letter][1:200])])
        ws.column_dimensions[letter].width = min(max(width + 2, 10), 45)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([COLUMNS[c] for c in columns])
    for row in rows:
        line = []
        for c in columns:
            v = cell_value(c, row)
            if isinstance(v, datetime):
                line.append(v.strftime("%d/%m/%Y %H:%M"))
            elif isinstance(v, date):
                line.append(v.strftime("%d/%m/%Y"))
            elif v is None:
                line.append("")
            else:
                line.append(safe_text(str(v)))
        writer.writerow(line)
    return ("﻿" + out.getvalue()).encode("utf-8")
