from __future__ import annotations

from typing import Any

from .models import Dataset, ENGINE_VERSION
from .reporting import analyse

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


def analyze_request(request: dict[str,Any]) -> dict[str,Any]:
    if not isinstance(request,dict):
        raise RuntimeContractError("request must be a JSON object")
    if request.get("contract_version")!=CONTRACT_VERSION:
        raise RuntimeContractError("unsupported runtime contract version")
    if request.get("product_id")!=PRODUCT_ID:
        raise RuntimeContractError("product_id must be flow")
    if request.get("product_version")!=PRODUCT_VERSION:
        raise RuntimeContractError(f"product_version must be {PRODUCT_VERSION}")

    platform_run_id=str(request.get("platform_run_id") or "").strip()
    if not platform_run_id:
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
    unknown=set(configuration)-{"top_recommendations"}
    if unknown:
        raise RuntimeContractError(
            "unsupported runtime configuration keys: "+", ".join(sorted(unknown))
        )

    try:
        dataset=Dataset.from_dict(raw_dataset)
    except Exception as exc:
        raise RuntimeContractError(str(exc)) from exc

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
