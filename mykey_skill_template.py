# Copy this block into mykey.py to keep Skill credentials in the main
# GenericAgent config file instead of memory/volc_ark_rag/.env.

skill_configs = {
    "volc_ark_rag": {
        # Volcengine AK/SK authentication.
        "access_key_id": "your-ak",
        "secret_access_key": "your-sk",
        "account_id": "your-account-id",

        # Optional temporary credential token.
        # "session_token": "your-session-token",

        # Knowledge base target. Use either collection_name or knowledge_base_ids.
        # "collection_name": "your-collection-name",
        "knowledge_base_ids": ["your-knowledge-base-id"],

        # Optional tuning.
        "project": "default",
        "top_k": 5,
        # "rerank": True,
        # "dense_weight": 0.5,
        # "chunk_diffusion_count": 0,
    }
}


# VOLCENGINE_ACCESS_KEY_ID=AKLTNDBkNjJiYmM5MzhkNGVlY2I1OTAzMjBlYTI5NTc1ZGY
# VOLCENGINE_SECRET_ACCESS_KEY=
# ARK_COLLECTION_NAME=know
# ARK_PROJECT=default
# ARK_TOP_K=5
# VOLCENGINE_ACCOUNT_ID=
