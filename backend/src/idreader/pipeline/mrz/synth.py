"""Construction de MRZ synthétiques valides (tests, `scripts/gen_mrz.py`).

Aucune donnée réelle : les valeurs viennent de l'appelant (générateur aléatoire ou
spécimens publics).
"""

from __future__ import annotations

from dataclasses import dataclass

from idreader.pipeline.mrz.checkdigit import FILLER, compute_check_digit
from idreader.pipeline.mrz.formats import MrzFormat


@dataclass(frozen=True)
class MrzData:
    document_code: str  # « P », « I », « ID », « V »…
    issuing_state: str
    surname: str
    given_names: str
    document_number: str
    nationality: str
    date_of_birth: str  # AAMMJJ
    sex: str  # M, F, X ou <
    date_of_expiry: str = ""  # AAMMJJ (absent de l'ancienne CNI française)
    optional_data: str = ""
    optional_data_2: str = ""  # TD1 ligne 2
    admin_code: str = ""  # ancienne CNI française


def _pad(text: str, length: int) -> str:
    if len(text) > length:
        raise ValueError("champ trop long")
    return text + FILLER * (length - len(text))


def _cd(text: str) -> str:
    return str(compute_check_digit(text))


def name_field(surname: str, given_names: str, length: int) -> str:
    """`NOM<<PRENOM<PRENOM2`, tronqué à `length` (ICAO 9303 partie 3, §4.6)."""
    s = "<".join(surname.upper().split())
    g = "<".join(given_names.upper().split())
    full = f"{s}<<{g}" if g else s
    return _pad(full[:length], length)


def _number(doc: str, opt: str, opt_len: int) -> tuple[str, str, str]:
    """(n° sur 9, chiffre ou `<`, données facultatives) ; n° long : ICAO 9303 partie 5, §4.2.4."""
    if len(doc) <= 9:
        return _pad(doc, 9), _cd(_pad(doc, 9)), _pad(opt, opt_len)
    return doc[:9], FILLER, _pad(doc[9:] + _cd(doc) + (FILLER + opt if opt else ""), opt_len)


def build(fmt: MrzFormat, d: MrzData) -> list[str]:
    code = _pad(d.document_code, 2)
    state = _pad(d.issuing_state, 3)
    nat = _pad(d.nationality, 3)
    if fmt in (MrzFormat.TD3, MrzFormat.MRVA, MrzFormat.TD2, MrzFormat.MRVB):
        length = 44 if fmt in (MrzFormat.TD3, MrzFormat.MRVA) else 36
        l1 = code + state + name_field(d.surname, d.given_names, length - 5)
        num9, ncd, opt = _number(d.document_number, d.optional_data, length - 29)
        if fmt is not MrzFormat.TD2 and ncd == FILLER:
            raise ValueError("n° long : seulement en TD1 et TD2")
        head = num9 + ncd + nat + d.date_of_birth + _cd(d.date_of_birth) + d.sex
        head += d.date_of_expiry + _cd(d.date_of_expiry)
        if fmt is MrzFormat.TD3:
            opt = _pad(d.optional_data, 14)
            opt_cd = FILLER if not d.optional_data else _cd(opt)
            body = head + opt + opt_cd
            composite = body[0:10] + body[13:20] + body[21:43]
            return [l1, body + _cd(composite)]
        if fmt is MrzFormat.TD2:
            body = head + opt
            composite = body[0:10] + body[13:20] + body[21:35]
            return [l1, body + _cd(composite)]
        return [l1, head + _pad(d.optional_data, length - 28)]
    if fmt is MrzFormat.TD1:
        num9, ncd, opt1 = _number(d.document_number, d.optional_data, 15)
        l1 = code + state + num9 + ncd + opt1
        l2 = d.date_of_birth + _cd(d.date_of_birth) + d.sex + d.date_of_expiry
        l2 += _cd(d.date_of_expiry) + nat + _pad(d.optional_data_2, 11)
        composite = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
        return [l1, l2 + _cd(composite), name_field(d.surname, d.given_names, 30)]
    if fmt is MrzFormat.FRA_ID:
        l1 = "IDFRA" + _pad("<".join(d.surname.upper().split())[:25], 25) + _pad(d.admin_code, 6)
        num = _pad(d.document_number, 12)
        given = _pad("<<".join(d.given_names.upper().split())[:14], 14)
        l2 = num + _cd(num) + given + d.date_of_birth + _cd(d.date_of_birth) + d.sex
        return [l1, l2 + _cd(l1 + l2)]
    raise ValueError(fmt)
