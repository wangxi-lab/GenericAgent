import json
import hashlib
import hmac
import importlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

DEFAULT_KNOWLEDGE_HOST = "api-knowledgebase.mlp.cn-beijing.volces.com"
DEFAULT_KNOWLEDGE_PATH = "/api/knowledge/collection/search_knowledge"
DEFAULT_KNOWLEDGE_REGION = "cn-north-1"
DEFAULT_KNOWLEDGE_SERVICE = "air"

MYKEY_CONFIG_ALIASES = {
    "access_key_id": "VOLCENGINE_ACCESS_KEY_ID",
    "ak": "VOLCENGINE_ACCESS_KEY_ID",
    "volcengine_access_key_id": "VOLCENGINE_ACCESS_KEY_ID",
    "secret_access_key": "VOLCENGINE_SECRET_ACCESS_KEY",
    "sk": "VOLCENGINE_SECRET_ACCESS_KEY",
    "volcengine_secret_access_key": "VOLCENGINE_SECRET_ACCESS_KEY",
    "account_id": "VOLCENGINE_ACCOUNT_ID",
    "volcengine_account_id": "VOLCENGINE_ACCOUNT_ID",
    "session_token": "VOLCENGINE_SESSION_TOKEN",
    "collection_name": "ARK_COLLECTION_NAME",
    "knowledge_base_ids": "ARK_KNOWLEDGE_BASE_IDS",
    "project": "ARK_PROJECT",
    "top_k": "ARK_TOP_K",
    "knowledge_host": "ARK_KNOWLEDGE_HOST",
    "knowledge_path": "ARK_KNOWLEDGE_PATH",
    "knowledge_region": "ARK_KNOWLEDGE_REGION",
    "knowledge_service": "ARK_KNOWLEDGE_SERVICE",
    "rerank": "ARK_RERANK",
    "dense_weight": "ARK_DENSE_WEIGHT",
    "chunk_diffusion_count": "ARK_CHUNK_DIFFUSION_COUNT",
}


def _load_env_files() -> None:
    skill_dir = Path(__file__).resolve().parent
    agent_root = skill_dir.parents[1]
    config_dir = Path(os.environ["GENERIC_AGENT_CONFIG_DIR"]) if os.environ.get("GENERIC_AGENT_CONFIG_DIR") else None
    candidates = []
    if config_dir:
        candidates.extend(
            [
                config_dir / ".env",
                config_dir / "volc_ark_rag.env",
            ]
        )
    candidates.extend(
        [
            skill_dir / ".env",
            agent_root / ".env.local",
            agent_root / ".env",
        ]
    )
    for env_file in candidates:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _load_mykey_config() -> None:
    try:
        mykey = importlib.import_module("mykey")
        importlib.reload(mykey)
    except Exception:
        return

    config = {}
    for attr in ("volc_ark_rag_config", "ark_rag_config"):
        value = getattr(mykey, attr, None)
        if isinstance(value, dict):
            config.update(value)

    for attr in ("skill_configs", "skills_config"):
        value = getattr(mykey, attr, None)
        if isinstance(value, dict) and isinstance(value.get("volc_ark_rag"), dict):
            config.update(value["volc_ark_rag"])

    for key, value in config.items():
        env_key = MYKEY_CONFIG_ALIASES.get(str(key).lower(), str(key))
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(item) for item in value if str(item).strip())
        elif isinstance(value, bool):
            value = "true" if value else "false"
        elif value is None:
            continue
        os.environ[env_key] = str(value)


_load_env_files()
_load_mykey_config()


def _env_list(name: str) -> List[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def is_configured() -> bool:
    return bool(_env_first("VOLCENGINE_ACCESS_KEY_ID", "ARK_ACCESS_KEY_ID", "VOLC_AK")) and bool(
        _env_first("VOLCENGINE_SECRET_ACCESS_KEY", "ARK_SECRET_ACCESS_KEY", "VOLC_SK")
    ) and bool(_env_first("VOLCENGINE_ACCOUNT_ID", "ARK_ACCOUNT_ID")) and bool(
        os.environ.get("ARK_COLLECTION_NAME") or _env_list("ARK_KNOWLEDGE_BASE_IDS")
    )


def search(query: str, *, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
    ak = _env_first("VOLCENGINE_ACCESS_KEY_ID", "ARK_ACCESS_KEY_ID", "VOLC_AK")
    sk = _env_first("VOLCENGINE_SECRET_ACCESS_KEY", "ARK_SECRET_ACCESS_KEY", "VOLC_SK")
    account_id = _env_first("VOLCENGINE_ACCOUNT_ID", "ARK_ACCOUNT_ID")
    session_token = _env_first("VOLCENGINE_SESSION_TOKEN", "ARK_SESSION_TOKEN")
    collection_name = os.environ.get("ARK_COLLECTION_NAME", "")
    knowledge_base_ids = _env_list("ARK_KNOWLEDGE_BASE_IDS")
    if not ak:
        raise RuntimeError("VOLCENGINE_ACCESS_KEY_ID or ARK_ACCESS_KEY_ID is not configured")
    if not sk:
        raise RuntimeError("VOLCENGINE_SECRET_ACCESS_KEY or ARK_SECRET_ACCESS_KEY is not configured")
    if not account_id:
        raise RuntimeError("VOLCENGINE_ACCOUNT_ID or ARK_ACCOUNT_ID is not configured")
    if not collection_name and not knowledge_base_ids:
        raise RuntimeError("ARK_COLLECTION_NAME or ARK_KNOWLEDGE_BASE_IDS is not configured")

    limit = int(top_k or os.environ.get("ARK_TOP_K", "5"))
    payload: Dict[str, Any] = {
        "project": os.environ.get("ARK_PROJECT", "default"),
        "query": query,
        "limit": limit,
        "pre_processing": {
            "need_instruction": True,
            "rewrite": False,
            "return_token_usage": True,
            "messages": [{"role": "user", "content": query}],
        },
        "post_processing": {
            "get_attachment_link": True,
            "chunk_group": True,
            "rerank_switch": os.environ.get("ARK_RERANK", "").lower() in ("1", "true", "yes"),
            "chunk_diffusion_count": int(os.environ.get("ARK_CHUNK_DIFFUSION_COUNT", "0")),
        },
    }
    if collection_name:
        payload["name"] = collection_name
    if knowledge_base_ids:
        payload["resource_id"] = knowledge_base_ids[0]
    dense_weight = os.environ.get("ARK_DENSE_WEIGHT")
    if dense_weight:
        payload["dense_weight"] = float(dense_weight)

    host = os.environ.get("ARK_KNOWLEDGE_HOST", DEFAULT_KNOWLEDGE_HOST)
    scheme = os.environ.get("ARK_KNOWLEDGE_SCHEME", "http")
    path = os.environ.get("ARK_KNOWLEDGE_PATH", DEFAULT_KNOWLEDGE_PATH)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    resp = requests.post(
        f"{scheme}://{host}{path}",
        headers=_signed_headers(
            "POST",
            path,
            body,
            ak=ak,
            sk=sk,
            host=host,
            account_id=account_id,
            session_token=session_token,
        ),
        data=body,
        timeout=60,
    )
    resp.raise_for_status()
    return _extract_references(resp.json())[:limit]


def format_references(references: List[Dict[str, Any]]) -> str:
    if not references:
        return "No relevant enterprise knowledge references found."

    blocks = []
    for index, item in enumerate(references, 1):
        title = _reference_title(item, index)
        content = item.get("content") or item.get("text") or json.dumps(item, ensure_ascii=False)
        score = item.get("score")
        score_text = f" score={score}" if score is not None else ""
        source = _reference_source(item)
        source_text = f"\nSource: {source}" if source else ""
        blocks.append(f"[{index}] {title}{score_text}{source_text}\n{content}")
    return "\n\n".join(blocks)


def _reference_title(item: Dict[str, Any], index: int) -> str:
    doc_info = item.get("doc_info") if isinstance(item.get("doc_info"), dict) else {}
    return (
        item.get("title")
        or item.get("knowledge_title")
        or item.get("doc_name")
        or item.get("knowledge_filename")
        or item.get("chunk_title")
        or doc_info.get("doc_name")
        or item.get("id")
        or f"reference-{index}"
    )


def _reference_source(item: Dict[str, Any]) -> str:
    url = _reference_url(item)
    doc_info = item.get("doc_info") if isinstance(item.get("doc_info"), dict) else {}
    doc_id = item.get("doc_id") or doc_info.get("doc_id")
    doc_name = item.get("doc_name") or doc_info.get("doc_name")
    chunk_id = item.get("chunk_id")
    if url:
        label = doc_name or item.get("chunk_title") or item.get("id") or "source"
        return f"[{label}]({url})"

    parts = []
    if doc_name:
        parts.append(f"doc_name={doc_name}")
    if doc_id:
        parts.append(f"doc_id={doc_id}")
    if chunk_id is not None:
        parts.append(f"chunk_id={chunk_id}")
    if not parts:
        return ""
    parts.append("link_unavailable=true; do not use javascript:void(0) or any placeholder link")
    return "; ".join(parts)


def _reference_url(item: Dict[str, Any]) -> str:
    for key in ("url", "link", "source_url", "download_url", "doc_url", "href"):
        value = item.get(key)
        if isinstance(value, str) and _is_real_url(value):
            return value

    doc_info = item.get("doc_info") if isinstance(item.get("doc_info"), dict) else {}
    for key in ("url", "link", "source_url", "download_url", "doc_url", "href"):
        value = doc_info.get(key)
        if isinstance(value, str) and _is_real_url(value):
            return value

    attachments = item.get("chunk_attachment")
    if isinstance(attachments, list):
        for attachment in attachments:
            if isinstance(attachment, dict):
                value = attachment.get("link")
                if isinstance(value, str) and _is_real_url(value):
                    return value
    return ""


def _is_real_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://")) and lowered != "javascript:void(0)"


def _extract_references(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if any(k in value for k in ("knowledge_id", "chunk_id", "score", "doc_id")):
                refs.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    return refs


def _signed_headers(
    method: str,
    path: str,
    body: bytes,
    *,
    ak: str,
    sk: str,
    host: str,
    account_id: str,
    session_token: str = "",
) -> Dict[str, str]:
    region = os.environ.get("ARK_KNOWLEDGE_REGION", DEFAULT_KNOWLEDGE_REGION)
    service = os.environ.get("ARK_KNOWLEDGE_SERVICE", DEFAULT_KNOWLEDGE_SERVICE)
    content_type = "application/json; charset=utf-8"
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    body_hash = hashlib.sha256(body).hexdigest()
    signed_header_names = ["content-type", "host", "v-account-id", "x-content-sha256", "x-date"]
    if session_token:
        signed_header_names.append("x-security-token")
    signed_headers = ";".join(sorted(signed_header_names))
    canonical_header_values = {
        "content-type": content_type,
        "host": host,
        "v-account-id": account_id,
        "x-content-sha256": body_hash,
        "x-date": x_date,
    }
    if session_token:
        canonical_header_values["x-security-token"] = session_token
    canonical_headers = "\n".join(
        [f"{name}:{canonical_header_values[name]}" for name in sorted(canonical_header_values)] + [""]
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            _normalize_path(path),
            "",
            canonical_headers,
            signed_headers,
            body_hash,
        ]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(_signing_key(sk, short_date, region, service), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Host": host,
        "V-Account-Id": account_id,
        "X-Content-Sha256": body_hash,
        "X-Date": x_date,
        "Authorization": (
            f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if session_token:
        headers["X-Security-Token"] = session_token
    return headers


def _signing_key(sk: str, short_date: str, region: str, service: str) -> bytes:
    date_key = hmac.new(sk.encode("utf-8"), short_date.encode("utf-8"), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(service_key, b"request", hashlib.sha256).digest()


def _normalize_path(path: str) -> str:
    return quote(path if path.startswith("/") else f"/{path}", safe="/-_.~")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Search Volcengine Ark private knowledge bases")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = search(args.query, top_k=args.top_k)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_references(results))
