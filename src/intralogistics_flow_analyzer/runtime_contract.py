from __future__ import annotations

from typing import Any

from .models import Dataset, ENGINE_VERSION
from .reporting import analyse
from .validation import validate

PRODUCT_ID="flow"
PRODUCT_VERSION=ENGINE_VERSION
CONTRACT_VERSION="1.0"


class RuntimeContractError(ValueError):
    pass


def health_document() -> dict[str,Any]:
    return {
        "status":"ready",
        "product_id":PRODUCT_ID,
        "version":PRODUCT_VERSION,
        "engine_version":ENGINE_VERSION,
        "runtime_contract_version":CONTRACT_VERSION,
    }


def validate_request(request: dict[str,Any]) -> dict[str,Any]:
    validated=_validate_request(request,require_run=False)
    try:
        dataset=Dataset.from_dict(validated["dataset"])
        report=validate(dataset)
    except Exception as exc:
        raise RuntimeContractError(str(exc)) from exc

    return {
        "contract_version":CONTRACT_VERSION,
        "product_id":PRODUCT_ID,
        "product_version":PRODUCT_VERSION,
        "engine_version":ENGINE_VERSION,
        "status":"invalid" if report.status=="data_error" else ("partial" if report.status!="ok" else "valid"),
        "issues":[issue.to_dict() for issue in report.issues],
    }


def analyze_request(request: dict[str,Any]) -> dict[str,Any]:
    validated=_validate_request(request,require_run=True)
    platform_run_id=validated["platform_run_id"]
    input_fingerprint=validated["input_fingerprint"]
    raw_dataset=validated["dataset"]
    configuration=validated["configuration"]

    try:
        dataset=Dataset.from_dict(raw_dataset)
    except Exception as exc:
        raise RuntimeContractError(str(exc)) from exc

    unknown=set(configuration)-{"top_recommendations"}
    if unknown:
        raise RuntimeContractError(
            "unsupported runtime configuration keys: "+", ".join(sorted(unknown))
        )

    top=configuration.get("top_recommendations")
    if top is not None:
        try:
            top=int(top)
        except (TypeError,ValueError) as exc:
            raise RuntimeContractError("top_recommendations must be an integer") from exc
        if top<0 or top>100:
            raise RuntimeContractError("top_recommendations must be between 0 and 100")

    result=analyse(dataset,top_recommendations=top)
    return normalize_result(
        platform_run_id=platform_run_id,
        input_fingerprint=input_fingerprint,
        analysis=result.analysis,
    )


def _validate_request(request: dict[str,Any], *, require_run: bool) -> dict[str,Any]:
    if not isinstance(request,dict):
        raise RuntimeContractError("request must be a JSON object")
    if request.get("contract_version")!=CONTRACT_VERSION:
        raise RuntimeContractError("unsupported runtime contract version")
    if request.get("product_id")!=PRODUCT_ID:
        raise RuntimeContractError("product_id must be flow")
    if request.get("product_version")!=PRODUCT_VERSION:
        raise RuntimeContractError(f"product_version must be {PRODUCT_VERSION}")

    platform_run_id=str(request.get("platform_run_id") or "").strip()
    if require_run and not platform_run_id:
        raise RuntimeContractError("platform_run_id is required")

    input_fingerprint=str(request.get("input_fingerprint") or "").strip()
    configuration_fingerprint=str(request.get("configuration_fingerprint") or "").strip()
    if len(input_fingerprint)!=64:
        raise RuntimeContractError("input_fingerprint must be a SHA-256 hex digest")
    if len(configuration_fingerprint)!=64:
        raise RuntimeContractError("configuration_fingerprint must be a SHA-256 hex digest")

    raw_dataset=request.get("dataset")
    if not isinstance(raw_dataset,dict):
        raise RuntimeContractError("dataset must be a canonical Flow JSON object")

    configuration=request.get("configuration") or {}
    if not isinstance(configuration,dict):
        raise RuntimeContractError("configuration must be an object")

    return {
        "platform_run_id":platform_run_id,
        "input_fingerprint":input_fingerprint,
        "dataset":raw_dataset,
        "configuration":configuration,
    }


def normalize_result(
    *,
    platform_run_id: str,
    input_fingerprint: str,
    analysis: dict[str,Any],
) -> dict[str,Any]:
    engine_status=str(analysis.get("status") or "data_error")
    status={
        "ok":"completed",
        "partial":"partial",
        "insufficient_evidence":"partial",
        "data_error":"blocked",
    }.get(engine_status,"failed")

    evidence=[{
        "id":"INPUT-001",
        "kind":"raw-record",
        "source_refs":[f"sha256:{input_fingerprint}"],
        "description":"Canonical intralogistics dataset supplied to the deterministic Flow engine.",
    }]

    findings=[]
    claim_map={
        "observed":"observation",
        "calculated":"finding",
        "simulated":"finding",
        "hypothesis":"hypothesis",
    }
    for index,finding in enumerate(analysis.get("findings") or [],start=1):
        if not isinstance(finding,dict):
            continue
        claim_type=str(finding.get("claim_type") or "hypothesis")
        limitations=[str(x) for x in (finding.get("limitations") or [])]
        if claim_type=="simulated":
            limitations.append("A simulated distance reduction is not a realised saving.")
        findings.append({
            "id":str(finding.get("finding_id") or f"FLOW-F{index:03d}"),
            "classification":claim_type,
            "statement":str(finding.get("statement") or "Flow analysis finding"),
            "evidence_refs":["INPUT-001"],
            "claim_level":claim_map.get(claim_type,"hypothesis"),
            "limitations":limitations,
        })

    warnings=[
        str(issue.get("message"))
        for issue in (analysis.get("issues") or [])
        if isinstance(issue,dict) and issue.get("severity")=="warning"
    ]

    return {
        "contract_version":CONTRACT_VERSION,
        "platform_run_id":platform_run_id,
        "product_id":PRODUCT_ID,
        "product_version":PRODUCT_VERSION,
        "engine_version":str(analysis.get("engine_version") or ENGINE_VERSION),
        "status":status,
        "external_run_id":f"flow:{platform_run_id}",
        "summary":{
            "engine_status":engine_status,
            "data_quality":analysis.get("data_quality") or {},
            "metrics":analysis.get("metrics") or {},
            "recommendations":analysis.get("recommendations") or [],
            "top_orders_by_distance":analysis.get("top_orders_by_distance") or [],
            "top_locations_by_distance":analysis.get("top_locations_by_distance") or [],
        },
        "findings":findings,
        "evidence":evidence,
        "limitations":[str(x) for x in (analysis.get("limitations") or [])],
        "warnings":warnings,
        "raw":analysis,
    }
