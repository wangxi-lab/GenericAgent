import argparse
import html
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, Iterable, List

from bottle import Bottle, HTTPResponse, abort, request, response, run

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

CONFIG_DIR = os.environ.get("GENERIC_AGENT_CONFIG_DIR", "")
if CONFIG_DIR and CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

from agentmain import GeneraticAgent  # noqa: E402


class NextChatAgentServer:
    def __init__(self) -> None:
        self.agent = GeneraticAgent()
        self.agent.inc_out = True
        self.rag_sources: Dict[str, List[Dict[str, Any]]] = {}
        self.worker = threading.Thread(target=self.agent.run, daemon=True)
        self.worker.start()

    def health(self) -> Dict[str, Any]:
        try:
            llm_name = self.agent.get_llm_name(model=True)
        except Exception as exc:
            llm_name = f"unavailable: {exc}"
        return {
            "ok": True,
            "running": self.agent.is_running,
            "model": llm_name,
        }

    def abort(self) -> Dict[str, Any]:
        self.agent.abort()
        return {"ok": True}

    def chat_events(self, payload: Dict[str, Any]) -> Iterable[str]:
        request_id = payload.get("request_id") or str(uuid.uuid4())

        yield sse("status", {"id": request_id, "message": "GenericAgent received the task."})

        user_query = self._extract_user_query(payload)
        query = self._build_query(payload)
        selected_skill = self._extract_skill_name(payload)
        rag_config = payload.get("rag") or {}
        rag_references: List[Dict[str, Any]] = []
        if selected_skill == "volc_ark_rag" or rag_config.get("enabled"):
            try:
                yield sse("status", {"id": request_id, "message": "Searching Volc Ark RAG skill..."})
                query, references = self._build_rag_query(user_query)
                self._store_rag_sources(request_id, references)
                rag_references = references
            except NoKnowledgeFound:
                message = "知识库未检索到相关内容"
                yield sse("delta", {"id": request_id, "content": message})
                yield sse("done", {"id": request_id, "content": message})
                return
        elif selected_skill:
            try:
                yield sse("status", {"id": request_id, "message": f"Using {selected_skill} skill..."})
                query = self._build_forced_skill_query(user_query, selected_skill, query)
            except Exception as exc:
                message = f"Skill 加载失败：{exc}"
                yield sse("delta", {"id": request_id, "content": message})
                yield sse("done", {"id": request_id, "content": message})
                return
            except Exception as exc:
                message = f"知识库检索失败：{exc}"
                yield sse("delta", {"id": request_id, "content": message})
                yield sse("done", {"id": request_id, "content": message})
                return

        display_queue = self.agent.put_task(query, source="nextchat")
        full_text = ""
        sent_text_len = 0
        sent_any_delta = False

        while True:
            try:
                item = display_queue.get(timeout=1)
            except queue.Empty:
                yield sse("status", {"id": request_id, "message": "GenericAgent is running..."})
                continue

            if "next" in item:
                chunk = item.get("next") or ""
                full_text += chunk
                display_text = sanitize_agent_text(full_text)
                delta = display_text[sent_text_len:]
                if delta:
                    sent_text_len = len(display_text)
                    for piece in stream_text(delta):
                        sent_any_delta = True
                        yield sse("delta", {"id": request_id, "content": piece})

            if "done" in item:
                done_text = sanitize_agent_text(item.get("done") or full_text)
                if done_text:
                    current_text = sanitize_agent_text(full_text)
                    if not sent_any_delta:
                        tail = done_text
                    elif done_text.startswith(current_text):
                        tail = done_text[sent_text_len:]
                    else:
                        tail = ""
                    for piece in stream_text(tail):
                        sent_any_delta = True
                        yield sse("delta", {"id": request_id, "content": piece})
                yield sse("done", {"id": request_id, "content": done_text})
                if rag_references and "知识库未检索到相关内容" not in done_text:
                    yield sse("citation", {"id": request_id, "items": rag_references})
                break

    def _build_query(self, payload: Dict[str, Any]) -> str:
        query = self._extract_user_query(payload)
        permissions = payload.get("permissions") or {}
        if permissions:
            query += "\n\n[NextChat local permissions]\n"
            query += format_permissions(permissions)
            query += (
                "\nIf a permission above is enabled, do not claim it is disabled. "
                "Use GenericAgent tools inside the granted scopes when the task requires local operation."
            )
        return query

    def _extract_user_query(self, payload: Dict[str, Any]) -> str:
        if payload.get("input"):
            return str(payload["input"])
        messages = payload.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "user" and message.get("content"):
                return str(message.get("content"))
        return "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages
            if m.get("content")
        )

    def _extract_skill_name(self, payload: Dict[str, Any]) -> str:
        skill = payload.get("skill") or {}
        if isinstance(skill, dict):
            mode = str(skill.get("mode") or "").lower()
            if mode and mode != "force":
                return ""
            return safe_skill_name(str(skill.get("name") or ""))
        return ""

    def list_skills(self) -> Dict[str, Any]:
        skills = []
        for name, skill_path in find_skill_files().items():
            title, description = parse_skill_metadata(skill_path)
            skills.append(
                {
                    "name": name,
                    "title": title or name,
                    "description": description,
                }
            )
        skills.sort(key=lambda item: item["name"])
        return {"skills": skills}

    def _build_forced_skill_query(self, user_query: str, skill_name: str, base_query: str) -> str:
        skill_path = find_skill_files().get(skill_name)
        if not skill_path:
            raise RuntimeError(f"skill not found: {skill_name}")
        skill_text = read_text(skill_path, limit=12000)
        return (
            f"NextChat 已选择并强制使用 GenericAgent Skill：{skill_name}。\n"
            "你必须按照下面 SKILL.md 的能力边界、调用方式和失败处理来回答本轮问题。\n"
            "不要自行改用其他 Skill；如果该 Skill 无法完成任务，请明确说明原因。\n\n"
            f"[用户问题]\n{user_query}\n\n"
            f"[{skill_name}/SKILL.md]\n{skill_text}\n\n"
            "[原始任务上下文]\n"
            f"{base_query}"
        )

    def _build_rag_query(self, user_query: str) -> tuple[str, List[Dict[str, Any]]]:
        sys.path.insert(0, os.path.join(ROOT_DIR, "memory", "volc_ark_rag"))
        from volc_ark_rag import format_references, search  # type: ignore

        references = search(user_query)
        min_score = float(os.environ.get("NEXTCHAT_RAG_MIN_SCORE", "0.3"))
        filtered = [
            item
            for item in references
            if float(item.get("score") or 0) >= min_score
        ]
        if not filtered:
            raise NoKnowledgeFound()

        reference_text = format_references(filtered)
        rag_prompt = (
            "知识库问答已开启，这是强制知识库问答流程。\n"
            "你不能判断是否需要知识库，也不能改用通用问答。\n"
            "你必须只基于下方由 memory/volc_ark_rag skill 检索得到的知识库内容回答。\n"
            "如果下方内容不足以回答用户问题，只回答：知识库未检索到相关内容。\n\n"
            f"[用户问题]\n{user_query}\n\n"
            "[Volc Ark RAG skill 检索结果]\n"
            f"{reference_text}\n\n"
            "回答要求：\n"
            "1. 只根据检索结果回答，不要补充外部知识。\n"
            "2. 如果检索结果与用户问题不相关，只回答：知识库未检索到相关内容。\n"
            "3. 正文中凡是使用了某条检索资料的结论，必须在句末标注对应编号，格式必须是 [1](#kb-ref-1)、[2](#kb-ref-2) 这种 Markdown 链接。\n"
            "4. 编号必须对应上方检索结果中的 [1]、[2]、[3]，不要使用不存在的编号。\n"
            "5. 引用来源只能使用检索结果中提供的 doc_name/doc_id/chunk_id 或真实 source link。"
        )
        return rag_prompt, filtered

    def _store_rag_sources(self, request_id: str, references: List[Dict[str, Any]]) -> None:
        self.rag_sources[request_id] = references[:10]
        for index, item in enumerate(self.rag_sources[request_id], 1):
            if not has_real_url(item):
                item["source_url"] = f"http://127.0.0.1:8765/v1/rag/source/{request_id}/{index}"

    def rag_source_page(self, request_id: str, index: int) -> HTTPResponse:
        references = self.rag_sources.get(request_id) or []
        if index < 1 or index > len(references):
            return HTTPResponse(status=404, body="source not found")
        item = references[index - 1]
        doc_info = item.get("doc_info") if isinstance(item.get("doc_info"), dict) else {}
        title = (
            item.get("title")
            or item.get("chunk_title")
            or item.get("doc_name")
            or doc_info.get("doc_name")
            or item.get("id")
            or f"source-{index}"
        )
        meta = {
            "id": item.get("id"),
            "doc_id": item.get("doc_id") or doc_info.get("doc_id"),
            "doc_name": item.get("doc_name") or doc_info.get("doc_name"),
            "chunk_id": item.get("chunk_id"),
            "score": item.get("score"),
        }
        content = item.get("content") or item.get("text") or json.dumps(item, ensure_ascii=False, indent=2)
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{html.escape(str(title))}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 32px; max-width: 960px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f7f9; border: 1px solid #e4e7ec; border-radius: 8px; padding: 16px; }}
    .meta {{ color: #5f6673; font-size: 13px; margin-bottom: 20px; }}
  </style>
</head>
<body>
  <h1>{html.escape(str(title))}</h1>
  <div class="meta">{html.escape(json.dumps(meta, ensure_ascii=False))}</div>
  <pre>{html.escape(str(content))}</pre>
</body>
</html>"""
        return HTTPResponse(body=body, headers={"Content-Type": "text/html; charset=utf-8"})


def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_text(text: str) -> Iterable[str]:
    if not text:
        return
    chunk_size = max(1, int(os.environ.get("NEXTCHAT_STREAM_CHUNK_SIZE", "18")))
    delay = max(0.0, float(os.environ.get("NEXTCHAT_STREAM_DELAY", "0.01")))
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        if delay:
            time.sleep(delay)


class NoKnowledgeFound(Exception):
    pass


def sanitize_agent_text(text: str) -> str:
    text = re.sub(
        r"^\s*\**\s*LLM\s+Running\s*\(Turn\s+\d+\)\s*\.\.\.\s*\**\s*$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(
        r"<summary>.*?</summary>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"\n*`{3,5}\s*\n\s*\[Info\]\s*Final response to user\.\s*\n`{3,5}\s*\n*",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*\[Info\]\s*Final response to user\.\s*$",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return text.strip()


def has_real_url(item: Dict[str, Any]) -> bool:
    def is_real_url(value: Any) -> bool:
        return isinstance(value, str) and value.lower().startswith(("http://", "https://"))

    for key in ("url", "link", "source_url", "download_url", "doc_url", "href"):
        if is_real_url(item.get(key)):
            return True

    doc_info = item.get("doc_info")
    if isinstance(doc_info, dict):
        for key in ("url", "link", "source_url", "download_url", "doc_url", "href"):
            if is_real_url(doc_info.get(key)):
                return True

    attachments = item.get("chunk_attachment")
    if isinstance(attachments, list):
        return any(isinstance(attachment, dict) and is_real_url(attachment.get("link")) for attachment in attachments)
    return False


def format_permissions(permissions: Dict[str, Any]) -> str:
    labels = {
        "file_system": "文件系统",
        "shell": "Shell",
        "browser": "浏览器",
        "screen_control": "屏幕控制",
    }
    lines = ["权限\t状态\t说明"]
    for key in ("file_system", "shell", "browser", "screen_control"):
        value = permissions.get(key) or {}
        enabled = bool(value.get("enabled")) if isinstance(value, dict) else bool(value)
        note = ""
        if key == "file_system" and isinstance(value, dict):
            allowed_dirs = value.get("allowed_dirs") or []
            if enabled:
                note = "允许访问目录：" + (", ".join(allowed_dirs) if allowed_dirs else "未限制")
        elif key == "shell" and isinstance(value, dict):
            allowlist = value.get("allowlist") or []
            denylist = value.get("denylist") or []
            parts = []
            if allowlist:
                parts.append("允许命令：" + ", ".join(allowlist))
            if denylist:
                parts.append("禁止命令：" + ", ".join(denylist))
            note = "；".join(parts)
        lines.append(f"{key}\t{'✅ 启用' if enabled else '❌ 禁用'}\t{note}")
    return "\n".join(lines) + "\n\nRaw permissions:\n" + json.dumps(
        permissions,
        ensure_ascii=False,
        indent=2,
    )


def safe_skill_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return ""
    return name


def find_skill_files() -> Dict[str, str]:
    roots = [os.path.join(ROOT_DIR, "memory")]
    if CONFIG_DIR:
        roots.insert(0, os.path.join(CONFIG_DIR, "memory"))

    skills: Dict[str, str] = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            skill_name = safe_skill_name(entry)
            if not skill_name:
                continue
            skill_path = os.path.join(root, entry, "SKILL.md")
            if os.path.isfile(skill_path):
                skills.setdefault(skill_name, skill_path)
    return skills


def read_text(path: str, limit: int = 2000) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as file:
        text = file.read(limit + 1)
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]"
    return text


def parse_skill_metadata(path: str) -> tuple[str, str]:
    text = read_text(path, limit=3000)
    title = ""
    description = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not title and line.startswith("#"):
            title = line.lstrip("#").strip()
            continue
        if line.startswith("#") or line.startswith(">") or line.startswith("```"):
            continue
        description = line
        break
    return title, description[:220]


def create_app() -> Bottle:
    server = NextChatAgentServer()
    app = Bottle()
    auth_token = os.environ.get("NEXTCHAT_AGENT_TOKEN", "")

    @app.hook("after_request")
    def enable_cors() -> None:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    @app.route("/<path:path>", method="OPTIONS")
    def options(path: str) -> str:
        return ""

    @app.hook("before_request")
    def check_auth() -> None:
        if not auth_token or request.method == "OPTIONS":
            return
        header = request.headers.get("Authorization", "")
        if header != f"Bearer {auth_token}":
            abort(401, "unauthorized")

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return server.health()

    @app.get("/v1/skills")
    def skills() -> Dict[str, Any]:
        return server.list_skills()

    @app.post("/v1/abort")
    def abort() -> Dict[str, Any]:
        return server.abort()

    @app.get("/v1/rag/source/<request_id>/<index:int>")
    def rag_source(request_id: str, index: int) -> HTTPResponse:
        return server.rag_source_page(request_id, index)

    @app.post("/v1/chat")
    def chat() -> HTTPResponse:
        try:
            payload = request.json or {}
        except Exception:
            payload = {}

        def generate() -> Iterable[bytes]:
            try:
                for item in server.chat_events(payload):
                    yield item.encode("utf-8")
            except Exception as exc:
                traceback.print_exc()
                yield sse("error", {"message": str(exc), "trace": traceback.format_exc()}).encode("utf-8")

        response.content_type = "text/event-stream; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache"
        return generate()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="GenericAgent adapter for NextChat")
    parser.add_argument("--host", default=os.environ.get("NEXTCHAT_AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEXTCHAT_AGENT_PORT", "8765")))
    args = parser.parse_args()
    log_dir = os.environ.get("NEXTCHAT_LOG_DIR")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "generic-agent-sidecar.log")
        sys.stdout = sys.stderr = open(log_file, "a", encoding="utf-8", buffering=1)
        print(f"[NEW] GenericAgent sidecar starting on {args.host}:{args.port}")
    run(create_app(), host=args.host, port=args.port, server="wsgiref", debug=True)


if __name__ == "__main__":
    main()
