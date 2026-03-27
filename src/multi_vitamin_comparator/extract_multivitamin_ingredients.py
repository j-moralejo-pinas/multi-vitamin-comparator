"""
Extract supplement ingredients from text files or images using the OpenAI API.

Input:
    A folder containing .txt/.md files and/or images (.png, .jpg, .jpeg, .webp)

Output:
    One JSON file per input file, plus:
    - all_results.json
    - errors.json

Usage:
    export OPENAI_API_KEY=...
    pip install openai pydantic
    python extract_multivitamin_ingredients.py ./input ./output --model gpt-5.4

Design choices / normalization logic:
1. The model extracts a structured list of ingredients from the source text/image.
2. Python then applies deterministic normalization locally.
3. Ingredient names are normalized at the nutrient/substance level whenever safe,
    while preserving the original label wording and the extracted chemical form.
4. Canonical names are restricted to a controlled ontology defined in this file.
5. The API canonicalization pass is only used for unresolved names.
6. The API never receives the full ontology for a guessy open-ended choice; it only
    receives a per-ingredient candidate list inferred locally.
7. Source materials such as extracts, oils, powders, blends, roots, fruits, and herbs
    are not promoted to a contained active ingredient unless that active is explicitly
    named in the ingredient itself.
8. Units are normalized to: mg, mcg, g, IU, mL, cfu.
9. For mass units, the script also computes quantity_mg.
10. IU is preserved as IU because its conversion depends on the ingredient.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import mimetypes
import os
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from openai import OpenAI
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

SUPPORTED_TEXT_EXTS = {".txt", ".md"}
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTS = SUPPORTED_TEXT_EXTS | SUPPORTED_IMAGE_EXTS

CANONICALIZATION_KEEP_RAW = "__KEEP_RAW__"


class ExtractedIngredient(BaseModel):
    """
    Structured ingredient extracted from the source.

    Attributes
    ----------
    raw_name : str
        Ingredient name exactly or nearly exactly as it appears on the label.
    form : str | None
        Chemical or source form if explicit, e.g. "pyridoxine HCl", "methylcobalamin".
    quantity_value : float | None
        Numeric amount for this ingredient if present.
    quantity_unit : str | None
        Unit exactly as implied by the source, e.g. mg, mcg, ug, g, IU.
    percent_daily_value : float | None
        Percent daily value if present, without the percent sign.
    notes : str | None
        Short note only if needed for ambiguity.
    """

    raw_name: str = Field(
        description="Ingredient name exactly or nearly exactly as it appears on the label"  # noqa: E501
    )
    form: str | None = Field(
        default=None,
        description="Chemical or source form if explicit, e.g. pyridoxine HCl, methylcobalamin",  # noqa: E501
    )
    quantity_value: float | None = Field(
        default=None,
        description="Numeric amount for this ingredient if present",
    )
    quantity_unit: str | None = Field(
        default=None,
        description="Unit exactly as implied by the source, e.g. mg, mcg, ug, g, IU",
    )
    percent_daily_value: float | None = Field(
        default=None,
        description="Percent daily value if present, without the percent sign",
    )
    notes: str | None = Field(
        default=None,
        description="Short note only if needed for ambiguity",
    )


class SupplementExtraction(BaseModel):
    """
    Full structured extraction returned by the model for one source file.

    Attributes
    ----------
    source_kind : str | None
        "text" or "image" if known.
    product_name : str | None
        Product name if present.
    serving_size : str | None
        Serving size exactly or nearly exactly as shown on the label.
    servings_per_container : str | None
        Servings per container exactly or nearly exactly as shown on the label.
    ingredients : list[ExtractedIngredient]
        Extracted ingredients in label order.
    """

    source_kind: str | None = Field(default=None, description="text or image")
    product_name: str | None = None
    serving_size: str | None = None
    servings_per_container: str | None = None
    ingredients: list[ExtractedIngredient] = Field(default_factory=list)


class CanonicalizationRequestItem(BaseModel):
    """
    Ingredient candidate sent to the fallback canonicalization pass.

    Attributes
    ----------
    index : int
        Position of the ingredient in the extracted list.
    raw_name : str
        Ingredient name after local cleanup.
    form : str | None
        Extracted chemical/source form if available.
    fallback_name : str
        Value to keep if no safe ontology match is found.
    candidate_names : list[str]
        Closed per-ingredient candidate list the API may choose from.
    """

    index: int
    raw_name: str
    form: str | None = None
    fallback_name: str
    candidate_names: list[str] = Field(default_factory=list)


class CanonicalizationResponseItem(BaseModel):
    """
    Canonicalized ingredient returned by the fallback pass.

    Attributes
    ----------
    index : int
        Position of the ingredient in the extracted list.
    canonical_name : str
        One of the provided candidate names, or __KEEP_RAW__.
    """

    index: int
    canonical_name: str


class CanonicalizationResponse(BaseModel):
    """
    Structured response from the fallback canonicalization pass.

    Attributes
    ----------
    ingredients : list[CanonicalizationResponseItem]
        Canonicalization decisions keyed by the original ingredient index.
    """

    ingredients: list[CanonicalizationResponseItem] = Field(default_factory=list)


SYSTEM_PROMPT = """
You extract supplement ingredients from multivitamin labels or ingredient text.

Return only structured data according to the schema.

Rules:
- The source may be in any language.
- Extract ingredients in label order.
- Prefer active ingredients / nutrients if present in a Supplement Facts, Nutrition \
Facts, Valores nutricionales, Valeurs nutritionnelles, Nährwerte, Informazioni \
nutrizionali, Informação nutricional, Ingredients, Ingredientes, Ingrédients, \
Zusammensetzung, Composición, Composizione, or similar panel.
- If the source also includes other ingredients without quantities, include them with \
quantity_value = null.
- Preserve the ingredient wording in raw_name as close to the source as possible.
- Do not translate raw_name.
- Put explicit chemical/source wording in form when possible.
- Extract numeric quantities as numbers only, including decimal commas when they are \
clearly amounts.
- Extract the quantity unit as it appears conceptually on the label.
- Recognize common multilingual unit variants such as IU, UI, IE, μg, µg, ug, UFC, CFU.
- If a % Daily Value or equivalent regional daily reference is shown, extract it as a \
number without the percent sign.
- Do not invent amounts.
- If text is partially unreadable, extract what is visible and use notes sparingly.
""".strip()


CANONICAL_INGREDIENTS: list[str] = [
    "Vitamin A",
    "Vitamin B1",
    "Vitamin B2",
    "Niacin (Vitamin B3)",
    "Pantothenic Acid (Vitamin B5)",
    "Vitamin B6",
    "Biotin (Vitamin B7)",
    "Folate (Vitamin B9)",
    "Vitamin B12",
    "Vitamin C",
    "Vitamin D",
    "Vitamin E",
    "Vitamin K",
    "Choline",
    "Inositol",
    "Calcium",
    "Magnesium",
    "Zinc",
    "Iron",
    "Iodine",
    "Selenium",
    "Copper",
    "Manganese",
    "Chromium",
    "Molybdenum",
    "Potassium",
    "Phosphorus",
    "Boron",
    "Silicon",
    "Vanadium",
    "Lutein",
    "Lycopene",
    "Zeaxanthin",
    "Coenzyme Q10",
    "Creatine",
    "Carnitine",
    "Taurine",
    "Omega-3",
    "DHA",
    "EPA",
    "Probiotics",
    "Melatonin",
]


FORM_STRIP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(?:as|from|de|des|del|da|do|dos|das|desde|como|von|aus|d'|di)\s+",
        re.IGNORECASE,
    ),
    re.compile(r"\s*[;,]\s*$"),
]


SOURCE_MATERIAL_TERMS: tuple[str, ...] = (
    "extract",
    "ext",
    "powder",
    "concentrate",
    "juice",
    "oil",
    "oleoresin",
    "resin",
    "herb",
    "botanical",
    "plant",
    "fruit",
    "vegetable",
    "berry",
    "root",
    "leaf",
    "leaves",
    "seed",
    "flower",
    "bark",
    "mushroom",
    "algae",
    "seaweed",
    "sprout",
    "bulb",
    "rhizome",
    "peel",
    "pulp",
    "whole food",
    "blend",
    "complex",
    "matrix",
    "proprietary blend",
    "proprietary complex",
    "extracto",
    "extracte",
    "polvo",
    "aceite",
    "jugo",
    "fruta",
    "fruto",
    "raiz",
    "hoja",
    "semilla",
    "flor",
    "corteza",
    "mezcla",
    "hierba",
    "planta",
    "huile",
    "poudre",
    "racine",
    "feuille",
    "graine",
    "fleur",
    "ecorce",
    "melange",
    "kraut",
    "wurzel",
    "blatt",
    "samen",
    "bluete",
    "rinde",
    "mischung",
    "pulver",
    "oel",
)


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in the given text.

    Parameters
    ----------
    text : str
        Input text.

    Returns
    -------
    str
        Text with repeated whitespace collapsed and outer whitespace stripped.
    """
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    """
    Remove accents and diacritics from a string.

    Parameters
    ----------
    text : str
        Input text.

    Returns
    -------
    str
        Accent-stripped text.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_for_matching(text: str) -> str:
    """
    Normalize text for deterministic multilingual matching.

    Parameters
    ----------
    text : str
        Input text.

    Returns
    -------
    str
        Lowercased, accent-stripped, punctuation-normalized text padded with spaces.
    """
    out = normalize_whitespace(text)
    out = strip_accents(out)
    out = out.lower()
    out = out.replace("μ", "u").replace("µ", "u")
    out = out.replace("&", " and ")

    # fmt: off
    out = re.sub(r"\bvit\.?\s*", "vitamin ", out)
    out = re.sub(r"\bvitamin\s+([abdekc])\s*[-.]?\s*(\d{1,2})\b", r"vitamin \1\2", out)
    out = re.sub(r"\bvitamina\s+([abdekc])\s*[-.]?\s*(\d{1,2})\b", r"vitamina \1\2", out) # noqa: E501
    out = re.sub(r"\bvitamine\s+([abdekc])\s*[-.]?\s*(\d{1,2})\b", r"vitamine \1\2", out) # noqa: E501
    out = re.sub(r"\bmk\s*[-.]?\s*(4|7)\b", r"mk\1", out)
    out = re.sub(r"\bp\s*[-.]?\s*5\s*[-.]?\s*p\b", "p5p", out)
    out = re.sub(r"[^a-z0-9+]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    # fmt: on

    return f" {out} "


CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "Vitamin A": (
        "vitamin a",
        "vitamina a",
        "vitamine a",
        "retinol",
        "retinyl",
    ),
    "Vitamin B1": (
        "vitamin b1",
        "vitamina b1",
        "vitamine b1",
        "thiamin",
        "thiamine",
        "tiamina",
        "thiamin hcl",
        "thiamine hcl",
        "thiamin mononitrate",
        "benfotiamine",
    ),
    "Vitamin B2": (
        "vitamin b2",
        "vitamina b2",
        "vitamine b2",
        "riboflavin",
        "riboflavina",
        "riboflavine",
        "riboflavin 5 phosphate",
    ),
    "Niacin (Vitamin B3)": (
        "vitamin b3",
        "vitamina b3",
        "vitamine b3",
        "niacin",
        "niacinamide",
        "nicotinamide",
        "nicotinic acid",
        "niacina",
        "inositol hexanicotinate",
    ),
    "Pantothenic Acid (Vitamin B5)": (
        "vitamin b5",
        "vitamina b5",
        "vitamine b5",
        "pantothenic acid",
        "pantothenate",
        "calcium d pantothenate",
        "acido pantotenico",
        "acide pantothenique",
    ),
    "Vitamin B6": (
        "vitamin b6",
        "vitamina b6",
        "vitamine b6",
        "pyridoxine",
        "piridoxina",
        "pyridoxal 5 phosphate",
        "pyridoxamine",
        "p5p",
    ),
    "Biotin (Vitamin B7)": (
        "vitamin b7",
        "vitamina b7",
        "vitamine b7",
        "biotin",
        "biotina",
        "biotine",
    ),
    "Folate (Vitamin B9)": (
        "vitamin b9",
        "vitamina b9",
        "vitamine b9",
        "folate",
        "folic acid",
        "acido folico",
        "acide folique",
        "folsaure",
        "methylfolate",
        "metilfolato",
        "5 methyltetrahydrofolate",
        "5 mthf",
        "l 5 mthf",
        "levomefolate",
    ),
    "Vitamin B12": (
        "vitamin b12",
        "vitamina b12",
        "vitamine b12",
        "cyanocobalamin",
        "cyanocobalamina",
        "methylcobalamin",
        "metilcobalamina",
        "hydroxocobalamin",
        "adenosylcobalamin",
        "cobalamin",
        "cobalamina",
    ),
    "Vitamin C": (
        "vitamin c",
        "vitamina c",
        "vitamine c",
        "ascorbic acid",
        "ascorbate",
        "acido ascorbico",
        "acide ascorbique",
    ),
    "Vitamin D": (
        "vitamin d",
        "vitamin d2",
        "vitamin d3",
        "vitamina d",
        "vitamina d2",
        "vitamina d3",
        "vitamine d",
        "vitamine d2",
        "vitamine d3",
        "cholecalciferol",
        "colecalciferol",
        "ergocalciferol",
        "calcifediol",
    ),
    "Vitamin E": (
        "vitamin e",
        "vitamina e",
        "vitamine e",
        "tocopherol",
        "tocopheryl",
        "tocoferol",
    ),
    "Vitamin K": (
        "vitamin k",
        "vitamin k1",
        "vitamin k2",
        "vitamina k",
        "vitamina k1",
        "vitamina k2",
        "vitamine k",
        "vitamine k1",
        "vitamine k2",
        "phylloquinone",
        "filoquinona",
        "menaquinone",
        "menaquinona",
        "mk4",
        "mk7",
    ),
    "Choline": ("choline", "colina"),
    "Inositol": ("inositol",),
    "Calcium": ("calcium", "calcio"),
    "Magnesium": (
        "magnesium",
        "magnesio",
        "magnesium citrate",
        "magnesium oxide",
        "magnesium glycinate",
    ),
    "Zinc": ("zinc", "zinco"),
    "Iron": ("iron", "hierro", "ferrous", "ferric", "ferro", "eisen"),
    "Iodine": ("iodine", "iodide", "yodo", "iode"),
    "Selenium": ("selenium", "selenio", "selenite", "selenate", "selenomethionine"),
    "Copper": ("copper", "cobre", "cuivre", "cupric", "cuprous"),
    "Manganese": ("manganese", "manganeso", "manganous"),
    "Chromium": ("chromium", "cromo", "chrome"),
    "Molybdenum": ("molybdenum", "molibdeno", "molybdate"),
    "Potassium": ("potassium", "potasio"),
    "Phosphorus": ("phosphorus", "phosphore", "fosforo", "phosphate", "fosfato"),
    "Boron": ("boron", "boro"),
    "Silicon": ("silicon", "silicio", "silica", "silicium"),
    "Vanadium": ("vanadium", "vanadio"),
    "Lutein": ("lutein", "luteina", "luteine"),
    "Lycopene": ("lycopene", "licopeno"),
    "Zeaxanthin": ("zeaxanthin", "zeaxantina"),
    "Coenzyme Q10": (
        "coenzyme q10",
        "coq10",
        "coenzima q10",
        "ubiquinone",
        "ubiquinol",
    ),
    "Creatine": ("creatine", "creatina"),
    "Carnitine": ("carnitine", "carnitina", "l carnitine"),
    "Taurine": ("taurine", "taurina"),
    "Omega-3": ("omega 3", "omega3"),
    "DHA": ("dha", "docosahexaenoic acid", "acido docosahexaenoico"),
    "EPA": ("epa", "eicosapentaenoic acid", "acido eicosapentaenoico"),
    "Probiotics": ("probiotic", "probiotics", "probiotico", "probioticos"),
    "Melatonin": ("melatonin", "melatonina", "melatonine"),
}


EXPLICIT_ACTIVE_ALIASES: dict[str, tuple[str, ...]] = {
    key: CANONICAL_ALIASES[key]
    for key in (
        "Lutein",
        "Lycopene",
        "Zeaxanthin",
        "Coenzyme Q10",
        "Creatine",
        "Carnitine",
        "Taurine",
        "Omega-3",
        "DHA",
        "EPA",
        "Probiotics",
        "Melatonin",
    )
}


API_CANDIDATE_HINTS: dict[str, tuple[str, ...]] = {
    key: value
    for key, value in CANONICAL_ALIASES.items()
    if key
    in {
        "Vitamin A",
        "Vitamin B1",
        "Vitamin B2",
        "Niacin (Vitamin B3)",
        "Pantothenic Acid (Vitamin B5)",
        "Vitamin B6",
        "Biotin (Vitamin B7)",
        "Folate (Vitamin B9)",
        "Vitamin B12",
        "Vitamin C",
        "Vitamin D",
        "Vitamin E",
        "Vitamin K",
        "Choline",
        "Inositol",
        "Calcium",
        "Magnesium",
        "Zinc",
        "Iron",
        "Iodine",
        "Selenium",
        "Copper",
        "Manganese",
        "Chromium",
        "Molybdenum",
        "Potassium",
        "Phosphorus",
        "Boron",
        "Silicon",
        "Vanadium",
        "Lutein",
        "Lycopene",
        "Zeaxanthin",
        "Coenzyme Q10",
        "Creatine",
        "Carnitine",
        "Taurine",
        "Omega-3",
        "DHA",
        "EPA",
        "Probiotics",
        "Melatonin",
    }
}


def build_canonicalization_system_prompt() -> str:
    """
    Build the system prompt for the unresolved-name canonicalization pass.

    Returns
    -------
    str
        Prompt that constrains the fallback pass to the per-item candidate lists.
    """
    return (
        "You canonicalize supplement ingredient names.\n\n"
        "Return only structured data according to the schema.\n\n"
        "Rules:\n"
        "- canonical_name must be exactly one of the candidate_names "
        f"provided for that ingredient, or {CANONICALIZATION_KEEP_RAW}.\n"
        "- If none of the candidate_names clearly matches, "
        f"return {CANONICALIZATION_KEEP_RAW}.\n"
        "- Never choose a value outside candidate_names for that ingredient.\n"
        "- Do not invent new canonical names.\n"
        "- Ignore language differences, spelling differences, "
        "OCR punctuation noise, and minor wording differences.\n"
        "- Do not infer a contained active ingredient from a botanical, extract, "
        "powder, juice, food, or blend unless that active "
        "is explicitly named in raw_name or form.\n"
        "- Do not include dosage, serving information, percentages, or explanations.\n"
    )


def normalize_unit(unit: str | None) -> str | None:
    """
    Normalize quantity units to a controlled set.

    Parameters
    ----------
    unit : str | None
        Extracted quantity unit.

    Returns
    -------
    str | None
        Normalized unit or None when the input is empty.
    """
    if unit is None:
        return None

    out = normalize_whitespace(unit)
    out = strip_accents(out)
    out = out.lower().replace("μ", "u").replace("µ", "u")
    out = out.replace("international units", "iu")
    out = out.replace("international unit", "iu")
    out = out.replace("unites internationales", "iu")
    out = out.replace("unidades internacionales", "iu")
    out = out.replace("internationale einheiten", "ie")
    out = out.replace("unita internazionali", "iu")
    out = re.sub(r"[.]", "", out)
    out = re.sub(r"\s+", "", out)

    mapping = {
        "mg": "mg",
        "mgs": "mg",
        "milligram": "mg",
        "milligrams": "mg",
        "milligramme": "mg",
        "milligrammes": "mg",
        "miligramo": "mg",
        "miligramos": "mg",
        "miligrammi": "mg",
        "mcg": "mcg",
        "ug": "mcg",
        "microgram": "mcg",
        "micrograms": "mcg",
        "microgramme": "mcg",
        "microgrammes": "mcg",
        "microgramo": "mcg",
        "microgramos": "mcg",
        "mikrogramm": "mcg",
        "g": "g",
        "gr": "g",
        "gram": "g",
        "grams": "g",
        "gramme": "g",
        "grammes": "g",
        "gramo": "g",
        "gramos": "g",
        "grammi": "g",
        "iu": "IU",
        "ui": "IU",
        "uie": "IU",
        "ie": "IU",
        "ml": "mL",
        "milliliter": "mL",
        "milliliters": "mL",
        "millilitre": "mL",
        "millilitres": "mL",
        "mililitro": "mL",
        "mililitros": "mL",
        "cfu": "cfu",
        "ufc": "cfu",
    }
    return mapping.get(out, unit.strip())


def quantity_to_mg(value: float | None, unit: str | None) -> float | None:
    """
    Convert a mass quantity to mg when possible.

    Parameters
    ----------
    value : float | None
        Numeric quantity.
    unit : str | None
        Normalized unit.

    Returns
    -------
    float | None
        Quantity converted to mg, or None if conversion is not valid.
    """
    if value is None or unit is None:
        return None
    if unit == "mg":
        return round(float(value), 6)
    if unit == "mcg":
        return round(float(value) / 1000.0, 6)
    if unit == "g":
        return round(float(value) * 1000.0, 6)
    return None


def split_form_from_raw_name(
    raw_name: str, model_form: str | None
) -> tuple[str, str | None]:
    """
    Split the chemical/source form from the raw ingredient name when possible.

    Parameters
    ----------
    raw_name : str
        Ingredient text as extracted from the label.
    model_form : str | None
        Separate form extracted by the model, if available.

    Returns
    -------
    tuple[str, str | None]
        Main ingredient name and extracted form.
    """
    raw = normalize_whitespace(raw_name)
    if model_form:
        return raw, normalize_whitespace(model_form)

    patterns = [
        re.compile(
            r"^(.*?)\s*\((?:as|from|como|de|des|del|da|do|dos|das|desde|von|aus)\s+(.+?)\)\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(.*?)\s*[,-]\s*(?:as|from|como|de|des|del|da|do|dos|das|von|aus)\s+(.+?)\s*$",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.match(raw)
        if match:
            return normalize_whitespace(match.group(1)), normalize_whitespace(
                match.group(2)
            )

    return raw, None


def clean_form(form: str | None) -> str | None:
    """
    Normalize and simplify the extracted form string.

    Parameters
    ----------
    form : str | None
        Form text.

    Returns
    -------
    str | None
        Cleaned form string or None.
    """
    if not form:
        return None

    out = normalize_whitespace(form)
    for pattern in FORM_STRIP_PATTERNS:
        out = pattern.sub("", out)
    return out or None


def contains_any_alias(text: str, aliases: Iterable[str]) -> bool:
    """
    Check whether any normalized alias appears in normalized text.

    Parameters
    ----------
    text : str
        Normalized matching text padded with spaces.
    aliases : Iterable[str]
        Alias strings in human-readable form.

    Returns
    -------
    bool
        True if any alias is present as a token sequence.
    """
    for alias in aliases:
        normalized_alias = normalize_for_matching(alias)
        if normalized_alias in text:
            return True
    return False


def is_source_material_name(name: str, form: str | None) -> bool:
    """
    Detect whether an ingredient is written primarily as a source material.

    Parameters
    ----------
    name : str
        Ingredient main name.
    form : str | None
        Ingredient form.

    Returns
    -------
    bool
        True if the ingredient looks like an extract, food source, botanical, or blend.
    """
    text = normalize_for_matching(f"{name} {form or ''}")
    return any(f" {term} " in text for term in SOURCE_MATERIAL_TERMS)


def has_explicit_active_name(name: str, form: str | None) -> bool:
    """
    Check whether an ingredient explicitly names a non-vitamin active substance.

    Parameters
    ----------
    name : str
        Ingredient main name.
    form : str | None
        Ingredient form.

    Returns
    -------
    bool
        True if the text explicitly names an active such as lycopene, lutein, DHA, etc.
    """
    text = normalize_for_matching(f"{name} {form or ''}")
    return any(
        contains_any_alias(text, aliases)
        for aliases in EXPLICIT_ACTIVE_ALIASES.values()
    )


def canonicalize_name_local(name: str, form: str | None) -> str | None:
    """
    Attempt deterministic local canonicalization.

    Parameters
    ----------
    name : str
        Ingredient main name.
    form : str | None
        Extracted chemical/source form.

    Returns
    -------
    str | None
        Canonical ontology value if recognized safely, otherwise None.
    """
    raw = normalize_whitespace(name)
    for canonical in CANONICAL_INGREDIENTS:
        if raw.casefold() == canonical.casefold():
            return canonical

    text = normalize_for_matching(f"{raw} {form or ''}")
    source_material = is_source_material_name(raw, form)

    for canonical, aliases in CANONICAL_ALIASES.items():
        if not contains_any_alias(text, aliases):
            continue

        if (
            source_material
            and canonical in EXPLICIT_ACTIVE_ALIASES
            and not has_explicit_active_name(raw, form)
        ):
            continue

        return canonical

    return None


def infer_candidate_names(name: str, form: str | None) -> list[str]:
    """
    Infer a small per-ingredient candidate list for fallback API canonicalization.

    Parameters
    ----------
    name : str
        Ingredient main name.
    form : str | None
        Extracted chemical/source form.

    Returns
    -------
    list[str]
        Candidate canonical names. Empty means no safe fallback API call should be made.
    """
    text = normalize_for_matching(f"{name} {form or ''}")
    source_material = is_source_material_name(name, form)
    candidates: list[str] = []

    for canonical, aliases in API_CANDIDATE_HINTS.items():
        if not contains_any_alias(text, aliases):
            continue

        if (
            source_material
            and canonical in EXPLICIT_ACTIVE_ALIASES
            and not has_explicit_active_name(name, form)
        ):
            continue

        candidates.append(canonical)

    deduped: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def canonicalize_name_api(
    client: OpenAI,
    model: str,
    unresolved: list[CanonicalizationRequestItem],
) -> dict[int, str]:
    """
    Canonicalize unresolved ingredient names against closed per-item candidate lists.

    Parameters
    ----------
    client : OpenAI
        OpenAI client instance.
    model : str
        Model name.
    unresolved : list[CanonicalizationRequestItem]
        Ingredients that local canonicalization could not resolve.

    Returns
    -------
    dict[int, str]
        Mapping from ingredient index to validated canonical choice.

    Raises
    ------
    RuntimeError
        If the model does not return a valid parsed response.
    """
    if not unresolved:
        return {}

    payload = {"ingredients": [item.model_dump() for item in unresolved]}

    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": build_canonicalization_system_prompt()},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Canonicalize the following unresolved ingredient names. "
                            "Return structured data only.\n\n"
                            "INPUT_JSON:\n"
                            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
                        ),
                    }
                ],
            },
        ],
        text_format=CanonicalizationResponse,
    )

    parsed = response.output_parsed
    if parsed is None:
        msg = "Model did not return a parsed canonicalization response"
        raise RuntimeError(msg)

    allowed_by_index = {
        item.index: set(item.candidate_names) | {CANONICALIZATION_KEEP_RAW}
        for item in unresolved
    }

    output: dict[int, str] = {}
    for item in parsed.ingredients:
        value = normalize_whitespace(item.canonical_name)
        allowed = allowed_by_index.get(item.index, {CANONICALIZATION_KEEP_RAW})
        if value in allowed:
            output[item.index] = value
    return output


def make_user_content_for_text(text: str) -> list[dict[str, str]]:
    """
    Create the user content payload for a text source.

    Parameters
    ----------
    text : str
        Source text contents.

    Returns
    -------
    list[dict[str, str]]
        Responses API content blocks.
    """
    return [
        {
            "type": "input_text",
            "text": (
                "Extract the supplement ingredients from the following text. "
                "Return structured data only.\n\n"
                f"SOURCE_TEXT:\n{text}"
            ),
        }
    ]


def encode_image_as_data_url(path: Path) -> str:
    """
    Encode an image file as a data URL suitable for the Responses API.

    Parameters
    ----------
    path : Path
        Input image path.

    Returns
    -------
    str
        Base64 data URL.
    """
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        mime_type = "image/jpeg"

    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


def make_user_content_for_image(path: Path) -> list[dict[str, str]]:
    """
    Create the user content payload for an image source.

    Parameters
    ----------
    path : Path
        Input image path.

    Returns
    -------
    list[dict[str, str]]
        Responses API content blocks.
    """
    data_url = encode_image_as_data_url(path)
    return [
        {
            "type": "input_text",
            "text": "Extract the supplement ingredients from this image. Return structured data only.",  # noqa: E501
        },
        {"type": "input_image", "image_url": data_url, "detail": "high"},
    ]


def read_text_file(path: Path) -> str:
    """
    Read a text file using a small encoding fallback sequence.

    Parameters
    ----------
    path : Path
        Input text path.

    Returns
    -------
    str
        Decoded file contents.
    """
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def normalize_extraction(
    client: OpenAI,
    model: str,
    source_file: Path,
    extraction: SupplementExtraction,
    *,
    use_api_canonicalization: bool = True,
) -> dict:
    """
    Normalize one parsed extraction into the final JSON output structure.

    Parameters
    ----------
    client : OpenAI
        OpenAI client instance.
    model : str
        Model name.
    source_file : Path
        Original source file path.
    extraction : SupplementExtraction
        Parsed structured extraction.
    use_api_canonicalization : bool
        Whether to use the fallback API pass for unresolved names. Defaults to True.

    Returns
    -------
    dict
        Final normalized JSON payload for one source file.
    """
    items: list[dict] = []
    unresolved: list[CanonicalizationRequestItem] = []

    for index, ingredient in enumerate(extraction.ingredients):
        raw_name_main, inferred_form = split_form_from_raw_name(
            ingredient.raw_name, ingredient.form
        )
        form = clean_form(inferred_form)
        normalized_raw_name = normalize_whitespace(ingredient.raw_name)
        fallback_name = normalize_whitespace(raw_name_main)
        unit = normalize_unit(ingredient.quantity_unit)
        quantity_mg = quantity_to_mg(ingredient.quantity_value, unit)

        local_canonical = canonicalize_name_local(raw_name_main, form)
        canonical_name = local_canonical or fallback_name

        items.append(
            {
                "raw_name": normalized_raw_name,
                "canonical_name": canonical_name,
                "form": form,
                "quantity": ingredient.quantity_value,
                "unit": unit,
                "quantity_mg": quantity_mg,
                "percent_daily_value": ingredient.percent_daily_value,
                "notes": normalize_whitespace(ingredient.notes)
                if ingredient.notes
                else None,
            }
        )

        if local_canonical is None and use_api_canonicalization:
            candidate_names = infer_candidate_names(raw_name_main, form)
            if candidate_names:
                unresolved.append(
                    CanonicalizationRequestItem(
                        index=index,
                        raw_name=fallback_name,
                        form=form,
                        fallback_name=fallback_name,
                        candidate_names=candidate_names,
                    )
                )

    if unresolved:
        api_results = canonicalize_name_api(
            client=client, model=model, unresolved=unresolved
        )
        fallback_by_index = {item.index: item.fallback_name for item in unresolved}

        for index, choice in api_results.items():
            if choice == CANONICALIZATION_KEEP_RAW:
                items[index]["canonical_name"] = fallback_by_index[index]
            else:
                items[index]["canonical_name"] = choice

    return {
        "source_file": source_file.name,
        "source_path": str(source_file),
        "source_kind": extraction.source_kind
        or ("image" if source_file.suffix.lower() in SUPPORTED_IMAGE_EXTS else "text"),
        "product_name": extraction.product_name,
        "serving_size": extraction.serving_size,
        "servings_per_container": extraction.servings_per_container,
        "ingredients": items,
    }


def safe_output_stem(path: Path) -> str:
    """
    Generate a safe output stem for a path.

    Parameters
    ----------
    path : Path
        Input file path.

    Returns
    -------
    str
        Safe stem for output filenames.
    """
    stem = path.stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return safe or "output"


def extract_one_file(
    client: OpenAI,
    model: str,
    source_file: Path,
    *,
    enable_api_canonicalization: bool = True,
) -> dict:
    """
    Extract supplement ingredients from one source file.

    Parameters
    ----------
    client : OpenAI
        OpenAI client instance.
    model : str
        Model name.
    source_file : Path
        Source text or image file.
    enable_api_canonicalization : bool
        Whether to use the fallback API pass for unresolved names. Defaults to True.

    Returns
    -------
    dict
        Normalized extraction payload.

    Raises
    ------
    ValueError
        If the file extension is unsupported.
    RuntimeError
        If the model does not return a valid parsed response.
    """
    suffix = source_file.suffix.lower()
    if suffix in SUPPORTED_TEXT_EXTS:
        text = read_text_file(source_file)
        user_content = make_user_content_for_text(text)
    elif suffix in SUPPORTED_IMAGE_EXTS:
        user_content = make_user_content_for_image(source_file)
    else:
        msg = f"Unsupported file extension: {suffix}"
        raise ValueError(msg)

    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},  # pyright: ignore[reportArgumentType]
        ],
        text_format=SupplementExtraction,
    )

    parsed = response.output_parsed
    if parsed is None:
        msg = "Model did not return a parsed structured response"
        raise RuntimeError(msg)

    return normalize_extraction(
        client=client,
        model=model,
        source_file=source_file,
        extraction=parsed,
        use_api_canonicalization=enable_api_canonicalization,
    )


def write_json(path: Path, payload: object) -> None:
    """
    Write the given payload as JSON.

    Parameters
    ----------
    path : Path
        Output path.
    payload : object
        JSON-serializable payload.
    """
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def iter_input_files(input_dir: Path) -> list[Path]:
    """
    Return supported input files from the input directory.

    Parameters
    ----------
    input_dir : Path
        Input directory.

    Returns
    -------
    list[Path]
        Supported input files.
    """
    return [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS
    ]


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the CLI argument parser.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser.
    """
    parser = argparse.ArgumentParser(
        description="Extract multivitamin ingredients from images and text files."
    )
    parser.add_argument(
        "input_dir", type=Path, help="Folder containing text files and/or images"
    )
    parser.add_argument(
        "output_dir", type=Path, help="Folder where JSON files will be written"
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4",
        help="OpenAI model to use for extraction and canonicalization (default: gpt-5.4)",  # noqa: E501
    )
    parser.add_argument(
        "--disable-api-canonicalization",
        action="store_true",
        help="Disable the second API pass that canonicalizes unresolved ingredient names.",  # noqa: E501
    )
    return parser


def main() -> int:
    """
    Execute the ingredient extraction process.

    Returns
    -------
    int
        Exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = build_arg_parser()
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("Error: OPENAI_API_KEY is not set.")
        return 1

    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir

    if not input_dir.exists() or not input_dir.is_dir():
        logger.error(
            "Error: input_dir does not exist or is not a directory: %s", input_dir
        )
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    files = iter_input_files(input_dir)

    if not files:
        logger.warning("No supported files found in %s", input_dir)
        return 1

    client = OpenAI(api_key=api_key)

    all_results: list[dict] = []
    errors: list[dict] = []

    for source_file in files:
        try:
            result = extract_one_file(
                client=client,
                model=args.model,
                source_file=source_file,
                enable_api_canonicalization=not args.disable_api_canonicalization,
            )
            all_results.append(result)
            out_path = output_dir / f"{safe_output_stem(source_file)}.json"
            write_json(out_path, result)
            logger.info("OK  %s -> %s", source_file.name, out_path.name)
        except (
            Exception
        ) as exc:  # pragma: no cover - runtime safety for batch processing
            error = {
                "source_file": source_file.name,
                "source_path": str(source_file),
                "error": f"{type(exc).__name__}: {exc}",
            }
            errors.append(error)
            logger.exception("ERR %s -> %s", source_file.name, error["error"])

    write_json(output_dir / "all_results.json", all_results)
    write_json(output_dir / "errors.json", errors)

    logger.info("Processed: %s file(s)", len(files))
    logger.info("Succeeded: %s", len(all_results))
    logger.info("Failed:    %s", len(errors))
    return 0 if all_results else 2


if __name__ == "__main__":
    raise SystemExit(main())
