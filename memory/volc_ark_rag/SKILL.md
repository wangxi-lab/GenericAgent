# Volc Ark RAG

Use this skill when the user asks questions that should be answered from an enterprise knowledge base.

## When To Use

- The user mentions enterprise knowledge, internal documents, company policies, product docs, SOPs, or knowledge base Q&A.
- The request requires grounded answers with citations from a Volcengine Ark private knowledge base.
- The user explicitly asks to search or answer from the knowledge base.

## Configuration

Prefer putting this skill's credentials in GenericAgent `mykey.py`, using the same main config file as LLM credentials. `.env` is still supported as a fallback.

Example `mykey.py` block:

```python
skill_configs = {
    "volc_ark_rag": {
        "access_key_id": "your-ak",
        "secret_access_key": "your-sk",
        "account_id": "your-account-id",
        "knowledge_base_ids": ["your-knowledge-base-id"],
        # "collection_name": "your-collection-name",
        "project": "default",
        "top_k": 5,
    }
}
```

The following environment variable names are also accepted, either from process env, GenericAgent `.env`, or `memory/volc_ark_rag/.env`:

- `VOLCENGINE_ACCESS_KEY_ID`: Volcengine access key. `ARK_ACCESS_KEY_ID` and `VOLC_AK` are also accepted.
- `VOLCENGINE_SECRET_ACCESS_KEY`: Volcengine secret key. `ARK_SECRET_ACCESS_KEY` and `VOLC_SK` are also accepted.
- `VOLCENGINE_ACCOUNT_ID`: Volcengine account ID, sent as `V-Account-Id`. `ARK_ACCOUNT_ID` is also accepted.
- `VOLCENGINE_SESSION_TOKEN`: optional security token for temporary credentials. `ARK_SESSION_TOKEN` is also accepted.
- `ARK_COLLECTION_NAME`: knowledge collection name.
- `ARK_KNOWLEDGE_BASE_IDS`: optional resource IDs. When set, the first ID is sent as `resource_id`.
- `ARK_PROJECT`: optional, defaults to `default`.
- `ARK_TOP_K`: optional, defaults to `5`.
- `ARK_KNOWLEDGE_HOST`: optional, defaults to `api-knowledgebase.mlp.cn-beijing.volces.com`.
- `ARK_KNOWLEDGE_PATH`: optional, defaults to `/api/knowledge/collection/search_knowledge`.
- `ARK_KNOWLEDGE_REGION`: optional, defaults to `cn-north-1`.
- `ARK_KNOWLEDGE_SERVICE`: optional, defaults to `air`.
- `ARK_RERANK`: optional, set `true` to enable rerank.

Example `memory/volc_ark_rag/.env`:

```dotenv
VOLCENGINE_ACCESS_KEY_ID=your-ak
VOLCENGINE_SECRET_ACCESS_KEY=your-sk
VOLCENGINE_ACCOUNT_ID=your-account-id
# VOLCENGINE_SESSION_TOKEN=your-token-if-using-temporary-credentials
ARK_COLLECTION_NAME=your-collection
ARK_PROJECT=default
ARK_TOP_K=5
```

## Usage

Call the helper script from GenericAgent with `code_run`:

```python
import sys
sys.path.append("memory/volc_ark_rag")
from volc_ark_rag import search, format_references

refs = search("用户的问题")
print(format_references(refs))
```

Then answer the user using only the returned references when they are relevant. Include concise citations by title or id.

## Failure Handling

- If AK/SK or collection configuration is missing, tell the user the enterprise knowledge base is not configured.
- If the search API fails, tell the user the knowledge base search failed and continue with non-RAG reasoning only if the user allows it.
- Do not invent citations.
