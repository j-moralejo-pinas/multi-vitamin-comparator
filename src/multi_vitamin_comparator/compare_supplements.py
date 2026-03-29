"""
Compare supplements against a target.

This module implements a comparison process that takes one target supplement and a list
of candidate supplements, and computes a detailed asymmetric logarithmic distance metric
for each candidate against the target. The distance metric penalizes missing
ingredients, underdosed ingredients, overdosed ingredients, and extra ingredients with
various configurable parameters. The output is a ranked list of candidates with detailed
comparison information for each ingredient.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from multi_vitamin_comparator.extract_multivitamin_ingredients import (
    CANONICAL_INGREDIENTS,
)

logger = logging.getLogger(__name__)

TOLERANCE_RATIO = 0.10
LOG_TOLERANCE = math.log(1.0 + TOLERANCE_RATIO)
UNDERDOSE_LOG_WEIGHT = 0.35
UNDERDOSE_MAX_PENALTY = 0.60
OVERDOSE_LOG_WEIGHT = 3.50
MISSING_PRESENCE_ONLY_PENALTY = 0.6
FORM_MISMATCH_PENALTY = 0.0
EXTRA_BASE_PENALTY = 0.45
EXTRA_AMOUNT_LOG_WEIGHT = 0.20
EXTRA_UNKNOWN_AMOUNT_PENALTY = 0.15
EXTRA_COMPONENT_CAP = 10.0
MIN_RATIO = 0.1

MEASURABLE_UNITS = {"IU", "mL", "cfu"}
EXTRA_REFERENCE_AMOUNTS = {
    "quantity_mg": 1.0,
    "quantity_IU": 100.0,
    "quantity_mL": 1.0,
    "quantity_cfu": 1e9,
    "percent_daily_value": 10.0,
}
EXTRA_HIGH_RISK_AMOUNT_LOG_WEIGHTS = {
    "Vitamin A": 1.0,
    "Vitamin E": 1.0,
}
EXTRA_HIGH_RISK_UNKNOWN_AMOUNT_PENALTIES = {
    "Vitamin A": 0.30,
    "Vitamin E": 0.20,
}


IngredientRecord = dict[str, Any]
SupplementRecord = dict[str, Any]


def load_json(path: Path) -> object:
    """
    Load a JSON file.

    Parameters
    ----------
    path : Path
        Input JSON path.

    Returns
    -------
    object
        Parsed JSON content.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def load_supplement(path: Path) -> SupplementRecord:
    """
    Load one normalized supplement JSON file.

    Parameters
    ----------
    path : Path
        Path to one supplement JSON produced by the extractor.

    Returns
    -------
    SupplementRecord
        Parsed supplement record.

    Raises
    ------
    ValueError
        If the JSON does not look like one supplement payload.
    """
    payload = load_json(path)
    if not isinstance(payload, dict) or "ingredients" not in payload:
        msg = f"Expected one supplement JSON object in {path}"
        raise ValueError(msg)
    return payload


def load_all_results(path: Path) -> list[SupplementRecord]:
    """
    Load all supplement results.

    Parameters
    ----------
    path : Path
        Path to all_results.json.

    Returns
    -------
    list[SupplementRecord]
        Parsed list of supplement payloads.

    Raises
    ------
    ValueError
        If the JSON shape is unsupported.
    """
    payload = load_json(path)

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("results", "all_results", "supplements", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

    msg = f"Unsupported all_results JSON shape in {path}"
    raise ValueError(msg)


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


def aggregate_group(key: str, items: list[IngredientRecord]) -> IngredientRecord:  # noqa: C901
    """
    Aggregate possibly repeated ingredient rows into one record.

    Parameters
    ----------
    key : str
        Canonical ingredient key.
    items : list[IngredientRecord]
        Grouped ingredient rows.

    Returns
    -------
    IngredientRecord
        Aggregated ingredient record.
    """
    forms: list[str] = []
    raw_names: list[str] = []
    notes: list[str] = []

    sum_quantity_mg = 0.0
    has_quantity_mg = False

    unit_buckets: dict[str, float] = defaultdict(float)
    percent_daily_value_sum = 0.0
    has_percent_daily_value = False

    for item in items:
        raw_name = item.get("raw_name")
        if raw_name and raw_name not in raw_names:
            raw_names.append(raw_name)

        form = item.get("form")
        if form and form not in forms:
            forms.append(form)

        note = item.get("notes")
        if note and note not in notes:
            notes.append(note)

        quantity_mg = item.get("quantity_mg")
        if quantity_mg is not None:
            sum_quantity_mg += quantity_mg
            has_quantity_mg = True
        else:
            quantity = item.get("quantity")
            unit = item.get("unit")
            if quantity is not None and unit in MEASURABLE_UNITS:
                unit_buckets[unit] += quantity

        percent_daily_value = item.get("percent_daily_value")
        if percent_daily_value is not None:
            percent_daily_value_sum += percent_daily_value
            has_percent_daily_value = True

    amount_kind: str | None = None
    amount_value: float | None = None
    amount_unit: str | None = None

    if has_quantity_mg:
        amount_kind = "quantity_mg"
        amount_value = round(sum_quantity_mg, 6)
        amount_unit = "mg"
    elif len(unit_buckets) == 1:
        amount_unit, amount_value = next(iter(unit_buckets.items()))
        amount_kind = f"quantity_{amount_unit}"
        amount_value = round(amount_value, 6)
    elif has_percent_daily_value:
        amount_kind = "percent_daily_value"
        amount_value = round(percent_daily_value_sum, 6)
        amount_unit = "%DV"

    return {
        "canonical_name": key,
        "raw_names": raw_names,
        "forms": forms,
        "notes": notes,
        "count": len(items),
        "amount_kind": amount_kind,
        "amount_value": amount_value,
        "amount_unit": amount_unit,
        "percent_daily_value": round(percent_daily_value_sum, 6)
        if has_percent_daily_value
        else None,
    }


def build_ingredient_index(supplement: SupplementRecord) -> dict[str, IngredientRecord]:
    """
    Build an aggregated ingredient index keyed by canonical_name.

    Parameters
    ----------
    supplement : SupplementRecord
        Supplement record.

    Returns
    -------
    dict[str, IngredientRecord]
        Aggregated ingredient index.
    """
    ingredients = supplement.get("ingredients")
    if not isinstance(ingredients, list):
        return {}

    groups: dict[str, list[IngredientRecord]] = defaultdict(list)
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        key = item.get("canonical_name")
        if key is None:
            continue
        if key not in CANONICAL_INGREDIENTS:
            continue
        groups[key].append(item)

    return {key: aggregate_group(key, items) for key, items in groups.items()}


def log_ratio_penalty(
    target_value: float, candidate_value: float
) -> dict[str, float | str]:
    """
    Compute the asymmetric logarithmic quantity penalty.

    Parameters
    ----------
    target_value : float
        Target amount.
    candidate_value : float
        Candidate amount.

    Returns
    -------
    dict[str, float | str]
        Quantity comparison details and penalty.
    """
    if target_value <= 0.0:
        return {
            "penalty": 0.0,
            "ratio": 1.0,
            "log_ratio": 0.0,
            "direction": "neutral",
        }

    ratio = max(candidate_value / target_value, MIN_RATIO)
    log_ratio = math.log(ratio)

    if abs(log_ratio) <= LOG_TOLERANCE:
        penalty = 0.0
        direction = "within_tolerance"
    elif log_ratio < 0.0:
        penalty = min(
            UNDERDOSE_MAX_PENALTY,
            UNDERDOSE_LOG_WEIGHT * (-log_ratio - LOG_TOLERANCE),
        )
        direction = "underdose"
    else:
        penalty = OVERDOSE_LOG_WEIGHT * (log_ratio - LOG_TOLERANCE) ** 2
        direction = "overdose"

    return {
        "penalty": round(penalty, 6),
        "ratio": round(ratio, 6),
        "log_ratio": round(log_ratio, 6),
        "direction": direction,
    }


def compare_forms(
    target_item: IngredientRecord,
    candidate_item: IngredientRecord | None,
) -> float:
    """
    Compute a small penalty for explicit form mismatches.

    Parameters
    ----------
    target_item : IngredientRecord
        Target aggregated ingredient.
    candidate_item : IngredientRecord | None
        Candidate aggregated ingredient.

    Returns
    -------
    float
        Form mismatch penalty.
    """
    if candidate_item is None:
        return 0.0

    target_forms = set(target_item.get("forms") or [])
    candidate_forms = set(candidate_item.get("forms") or [])

    if not target_forms or not candidate_forms:
        return 0.0
    if target_forms & candidate_forms:
        return 0.0
    return FORM_MISMATCH_PENALTY


def compare_target_ingredient(
    target_item: IngredientRecord,
    candidate_item: IngredientRecord | None,
) -> dict[str, Any]:
    """
    Compare one target ingredient against a candidate ingredient.

    Parameters
    ----------
    target_item : IngredientRecord
        Target aggregated ingredient.
    candidate_item : IngredientRecord | None
        Candidate aggregated ingredient or None when missing.

    Returns
    -------
    dict[str, Any]
        Detailed comparison entry.
    """
    canonical_name = target_item["canonical_name"]
    form_penalty = compare_forms(target_item, candidate_item)

    target_value = float(target_item.get("amount_value") or 0.0)
    candidate_value = 0.0
    if candidate_item is not None:
        candidate_value = float(candidate_item.get("amount_value") or 0.0)

    quantity_result = log_ratio_penalty(target_value, candidate_value)
    total_penalty = float(quantity_result["penalty"]) + form_penalty

    return {
        "canonical_name": canonical_name,
        "status": "missing" if candidate_item is None else "compared_by_amount",
        "comparison_kind": target_item.get("amount_kind"),
        "target_amount": target_item.get("amount_value"),
        "candidate_amount": candidate_value,
        "unit": target_item.get("amount_unit"),
        "ratio": quantity_result["ratio"],
        "log_ratio": quantity_result["log_ratio"],
        "direction": quantity_result["direction"],
        "quantity_penalty": quantity_result["penalty"],
        "form_penalty": round(form_penalty, 6),
        "penalty": round(total_penalty, 6),
        "target_forms": target_item.get("forms", []),
        "candidate_forms": []
        if candidate_item is None
        else candidate_item.get("forms", []),
    }


def score_extra_ingredient(extra_item: IngredientRecord) -> dict[str, Any]:
    """
    Compute a penalty for an ingredient absent from the target.

    Parameters
    ----------
    extra_item : IngredientRecord
        Candidate-only aggregated ingredient.

    Returns
    -------
    dict[str, Any]
        Extra ingredient scoring details.
    """
    canonical_name = str(extra_item["canonical_name"]).strip()
    amount_kind = extra_item.get("amount_kind")
    amount_value = extra_item.get("amount_value")
    if canonical_name not in CANONICAL_INGREDIENTS:
        return {
            "canonical_name": canonical_name,
            "amount_kind": amount_kind,
            "amount_value": amount_value,
            "amount_unit": extra_item.get("amount_unit"),
            "forms": extra_item.get("forms", []),
            "base_penalty": 0.0,
            "amount_penalty": 0.0,
            "penalty_reason": "extra_not_in_canonical_set",
            "penalty": 0.0,
        }

    base_penalty = EXTRA_BASE_PENALTY
    amount_penalty = 0.0
    penalty_reason = "extra_presence_only"

    reference_amount = EXTRA_REFERENCE_AMOUNTS.get(str(amount_kind))
    high_risk_amount_log_weight = EXTRA_HIGH_RISK_AMOUNT_LOG_WEIGHTS.get(
        canonical_name,
        0.0,
    )
    high_risk_unknown_amount_penalty = EXTRA_HIGH_RISK_UNKNOWN_AMOUNT_PENALTIES.get(
        canonical_name,
        0.0,
    )

    pdv = extra_item.get("percent_daily_value")

    if high_risk_amount_log_weight > 0.0:
        # High-risk substance: compute a single amount_penalty, no separate risk_penalty
        if pdv is not None and pdv > 0.0:
            # Linear scale vs 100 % DV so lower amounts are never rewarded.
            amount_penalty = high_risk_amount_log_weight * (pdv / 100.0)
            penalty_reason = "high_risk_extra_pdv_sensitive"
        elif amount_value is None:
            amount_penalty = max(
                high_risk_unknown_amount_penalty, EXTRA_UNKNOWN_AMOUNT_PENALTY
            )
            penalty_reason = "extra_unknown_amount"
        elif amount_value > 0.0 and reference_amount is not None:
            log_amount = math.log1p(amount_value / reference_amount)
            amount_penalty = high_risk_amount_log_weight * log_amount
            penalty_reason = "high_risk_extra_amount_sensitive"
    elif amount_value is None:
        amount_penalty = EXTRA_UNKNOWN_AMOUNT_PENALTY
        penalty_reason = "extra_unknown_amount"
    elif amount_value > 0.0 and reference_amount is not None:
        log_amount = math.log1p(amount_value / reference_amount)
        amount_penalty = EXTRA_AMOUNT_LOG_WEIGHT * log_amount
        penalty_reason = "extra_amount_sensitive"

    penalty = base_penalty + amount_penalty
    return {
        "canonical_name": canonical_name,
        "amount_kind": amount_kind,
        "amount_value": extra_item.get("amount_value"),
        "amount_unit": extra_item.get("amount_unit"),
        "forms": extra_item.get("forms", []),
        "base_penalty": round(base_penalty, 6),
        "amount_penalty": round(amount_penalty, 6),
        "penalty_reason": penalty_reason,
        "penalty": round(penalty, 6),
    }


def mean(values: list[float]) -> float:
    """
    Compute the arithmetic mean of a possibly empty list.

    Parameters
    ----------
    values : list[float]
        Input values.

    Returns
    -------
    float
        Mean value or 0.0 for an empty list.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def compare_supplements(
    target: SupplementRecord,
    candidate: SupplementRecord,
) -> dict[str, Any]:
    """
    Compare one candidate supplement against the target.

    Parameters
    ----------
    target : SupplementRecord
        Target supplement.
    candidate : SupplementRecord
        Candidate supplement.

    Returns
    -------
    dict[str, Any]
        Full comparison result.
    """
    target_index = build_ingredient_index(target)
    candidate_index = build_ingredient_index(candidate)

    ingredient_details: list[dict[str, Any]] = []
    target_penalties: list[float] = []

    for canonical_name, target_item in sorted(target_index.items()):
        candidate_item = candidate_index.get(canonical_name)
        detail = compare_target_ingredient(target_item, candidate_item)
        ingredient_details.append(detail)
        target_penalties.append(float(detail["penalty"]))

    extra_details: list[dict[str, Any]] = []
    extra_penalties: list[float] = []
    for canonical_name, candidate_item in sorted(candidate_index.items()):
        if canonical_name in target_index:
            continue
        detail = score_extra_ingredient(candidate_item)
        extra_details.append(detail)
        extra_penalties.append(float(detail["penalty"]))

    target_component = mean(target_penalties)
    extra_component_raw = sum(extra_penalties)
    extra_component = min(extra_component_raw, EXTRA_COMPONENT_CAP)
    total_distance = target_component + extra_component

    return {
        "candidate_source_file": candidate.get("source_file"),
        "candidate_source_path": candidate.get("source_path"),
        "candidate_product_name": candidate.get("product_name"),
        "target_component": round(target_component, 6),
        "extra_component": round(extra_component, 6),
        "total_distance": round(total_distance, 6),
        "num_target_ingredients": len(target_index),
        "num_candidate_ingredients": len(candidate_index),
        "num_extra_ingredients": len(extra_details),
        "ingredient_details": ingredient_details,
        "extra_ingredient_details": extra_details,
        "missing_target_ingredients": [
            detail["canonical_name"]
            for detail in ingredient_details
            if detail["status"] == "missing"
        ],
        "overdosed_ingredients": [
            detail["canonical_name"]
            for detail in ingredient_details
            if detail["direction"] == "overdose"
        ],
        "present_noncomparable_ingredients": [
            detail["canonical_name"]
            for detail in ingredient_details
            if detail["status"] == "present_but_not_comparable"
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the CLI argument parser.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Compare supplements against a target supplement using an "
            "asymmetric logarithmic distance metric."
        )
    )
    parser.add_argument(
        "target_json",
        type=Path,
        help="Path to one normalized target supplement JSON file",
    )
    parser.add_argument(
        "all_results_json",
        type=Path,
        help="Path to all_results.json produced by the extractor",
    )
    parser.add_argument(
        "output_json",
        type=Path,
        help="Path where the ranked comparison JSON will be written",
    )
    parser.add_argument(
        "--include-target",
        action="store_true",
        help="Keep the target supplement itself in the ranked candidate list",
    )
    return parser


def main() -> int:
    """
    Execute the supplement comparison process.

    Returns
    -------
    int
        Exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.target_json.exists():
        logger.error("Error: target_json does not exist: %s", args.target_json)
        return 1
    if not args.all_results_json.exists():
        logger.error(
            "Error: all_results_json does not exist: %s",
            args.all_results_json,
        )
        return 1

    target = load_supplement(args.target_json)
    all_results = load_all_results(args.all_results_json)

    comparisons: list[dict[str, Any]] = []
    for candidate in all_results:
        if not args.include_target and target.get("source_path") == candidate.get(
            "source_path"
        ):
            continue
        comparisons.append(compare_supplements(target, candidate))

    comparisons.sort(key=lambda item: item["total_distance"])

    payload = {
        "metric": {
            "description": (
                "Asymmetric logarithmic distance over target ingredients. "
                "Underdose penalties are capped, overdose penalties grow "
                "quadratically in log-space, and extra ingredients are "
                "penalized per ingredient with additional dose-sensitive "
                "risk terms for vitamins A and E."
            ),
            "tolerance_ratio": TOLERANCE_RATIO,
            "log_tolerance": round(LOG_TOLERANCE, 6),
            "underdose_log_weight": UNDERDOSE_LOG_WEIGHT,
            "underdose_max_penalty": UNDERDOSE_MAX_PENALTY,
            "overdose_log_weight": OVERDOSE_LOG_WEIGHT,
            "missing_presence_only_penalty": MISSING_PRESENCE_ONLY_PENALTY,
            "form_mismatch_penalty": FORM_MISMATCH_PENALTY,
            "extra_base_penalty": EXTRA_BASE_PENALTY,
            "extra_amount_log_weight": EXTRA_AMOUNT_LOG_WEIGHT,
            "extra_unknown_amount_penalty": EXTRA_UNKNOWN_AMOUNT_PENALTY,
            "extra_component_cap": EXTRA_COMPONENT_CAP,
            "extra_reference_amounts": EXTRA_REFERENCE_AMOUNTS,
            "extra_high_risk_amount_log_weights": EXTRA_HIGH_RISK_AMOUNT_LOG_WEIGHTS,
            "extra_high_risk_unknown_amount_penalties": EXTRA_HIGH_RISK_UNKNOWN_AMOUNT_PENALTIES,  # noqa: E501
        },
        "target": {
            "source_file": target.get("source_file"),
            "source_path": target.get("source_path"),
            "product_name": target.get("product_name"),
            "num_ingredients": len(build_ingredient_index(target)),
        },
        "num_candidates": len(comparisons),
        "ranked_candidates": comparisons,
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output_json, payload)

    logger.info("Wrote ranked comparison to %s", args.output_json)
    for index, item in enumerate(comparisons[:10], start=1):
        logger.info(
            "%d. %s | distance = %s",
            index,
            item["candidate_source_file"],
            item["total_distance"],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
