#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书开放平台 API 客户端（tenant_access_token 模式）。

凭据来源，按优先级：
1. 环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET
2. ~/.openclaw/openclaw.json 的 channels.feishu.accounts.<account>

绝不打印 appSecret。
"""

import json
import os
import threading
import time
from pathlib import Path

import requests

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_ACCOUNT = "kg_fetcher_robot"

BASE = "https://open.feishu.cn/open-apis"

# 常见错误码 -> 人话
ERROR_HINTS = {
    99991663: "tenant_access_token 无效或过期（自动重试一次）",
    99991661: "应用凭据错误，检查 appId / appSecret 是否匹配",
    99991672: "缺少权限 scope，去开放平台补开并【发布应用版本】",
    1770001: "转换接口参数被拒，常见原因：块数/体积超限、非法字符",
    1770002: "文档不存在或未授权给当前应用",
    1770003: "无权限访问该文档，需把文档共享给机器人",
    1770004: "参数校验失败，检查 row_size/column_size 等必填项",
    99992402: "字段校验失败。常见：children 单次超过 50 块（接口上限），需分批写入",
    1061002: "素材上传参数错误。必填 parent_type / parent_node / file_name / size",
    1061004: "素材上传被拒。检查 parent_node 是否指向真实块",
    1770013: "关系不匹配。素材上传的 parent_node 必须是图片块 block_id，不能传 document_id",
}


class FeishuError(RuntimeError):
    """带飞书错误码与排查提示的异常。"""

    def __init__(self, message, code=None, status=None, detail=None):
        self.code = code
        self.status = status
        self.detail = detail
        hint = ERROR_HINTS.get(code, "")
        text = f"[code={code}] [http={status}] {message}"
        if hint:
            text += f"\n  → 排查提示：{hint}"
        if detail:
            text += f"\n  → 原始返回：{json.dumps(detail, ensure_ascii=False)[:800]}"
        super().__init__(text)


def load_credentials(account=None):
    """返回 (app_id, app_secret)。"""
    env_id = os.environ.get("FEISHU_APP_ID")
    env_secret = os.environ.get("FEISHU_APP_SECRET")
    if env_id and env_secret:
        return env_id, env_secret

    account = account or os.environ.get("FEISHU_ACCOUNT") or DEFAULT_ACCOUNT
    if not OPENCLAW_CONFIG.exists():
        raise FeishuError(
            f"找不到配置文件 {OPENCLAW_CONFIG}，"
            "请设置环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET"
        )
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FeishuError(f"解析 {OPENCLAW_CONFIG} 失败: {exc}") from exc

    accounts = cfg.get("channels", {}).get("feishu", {}).get("accounts", {})
    if account not in accounts:
        raise FeishuError(
            f"配置里没有账号 {account!r}，可用：{list(accounts) or '（无）'}"
        )
    creds = accounts[account]
    app_id, app_secret = creds.get("appId"), creds.get("appSecret")
    if not app_id or not app_secret:
        raise FeishuError(f"账号 {account!r} 的 appId/appSecret 不完整")
    return app_id, app_secret


class FeishuClient:
    """最小可用的飞书 API 客户端，自动缓存并刷新 token。"""

    def __init__(self, account=None, app_id=None, app_secret=None, timeout=60):
        if app_id and app_secret:
            self.app_id, self.app_secret = app_id, app_secret
        else:
            self.app_id, self.app_secret = load_credentials(account)
        self.timeout = timeout
        self._token = None
        self._expire_at = 0.0
        self._lock = threading.Lock()

    # ---------- 鉴权 ----------

    def token(self):
        with self._lock:
            if self._token and time.time() < self._expire_at - 60:
                return self._token
            resp = requests.post(
                f"{BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(
                    "获取 tenant_access_token 失败",
                    code=data.get("code"),
                    status=resp.status_code,
                    detail=data,
                )
            self._token = data["tenant_access_token"]
            self._expire_at = time.time() + int(data.get("expire", 7200))
            return self._token

    # ---------- 通用请求 ----------

    def request(self, method, path, *, json_body=None, params=None, files=None,
                data=None, raw=False, _retry=True, max_retries=6):
        """发起请求。429 限流按 Retry-After 退避重试。"""
        url = path if path.startswith("http") else f"{BASE}{path}"
        headers = {"Authorization": f"Bearer {self.token()}"}

        attempt = 0
        while True:
            resp = requests.request(
                method, url, headers=headers, json=json_body, params=params,
                files=files, data=data, timeout=self.timeout,
            )

            # 限流：退避重试
            if resp.status_code == 429 and attempt < max_retries:
                wait = _retry_after(resp, attempt)
                time.sleep(wait)
                attempt += 1
                # files 是已打开的文件句柄，重试前需要回绕
                _rewind_files(files)
                continue

            break

        if raw:
            if resp.status_code >= 400:
                raise FeishuError("请求失败", status=resp.status_code,
                                  detail=_safe_body(resp))
            return resp.content

        try:
            body = resp.json()
        except Exception:
            raise FeishuError("返回不是 JSON", status=resp.status_code,
                              detail=resp.text[:500])

        code = body.get("code")
        if code == 99991663 and _retry:
            with self._lock:
                self._token = None
            return self.request(method, path, json_body=json_body, params=params,
                                files=files, data=data, raw=raw, _retry=False)

        if resp.status_code >= 400 or code not in (0, None):
            raise FeishuError(
                body.get("msg") or body.get("message") or "接口返回错误",
                code=code, status=resp.status_code, detail=body,
            )
        return body.get("data", body)

    # ---------- 云文档 ----------

    def create_document(self, title, folder_token=None):
        payload = {"title": title}
        if folder_token:
            payload["folder_token"] = folder_token
        data = self.request("POST", "/docx/v1/documents", json_body=payload)
        # 接口把结果包在 document 里
        doc = data.get("document", data)
        doc.setdefault("url", f"https://feishu.cn/docx/{doc.get('document_id')}")
        return doc

    def get_document(self, document_id):
        data = self.request("GET", f"/docx/v1/documents/{document_id}")
        return data.get("document", data)

    def list_blocks(self, document_id, page_size=500, page_token=None):
        blocks, token = [], page_token
        while True:
            params = {"page_size": page_size, "document_revision_id": -1}
            if token:
                params["page_token"] = token
            data = self.request(
                "GET", f"/docx/v1/documents/{document_id}/blocks", params=params
            )
            blocks.extend(data.get("items", []))
            if not data.get("has_more"):
                return blocks
            token = data.get("page_token")

    def convert_markdown(self, content, block_ids=None):
        """Markdown -> 飞书块。返回块列表。"""
        payload = {
            "content_type": "markdown",
            "content": content,
        }
        if block_ids:
            payload["options"] = {"block_ids": block_ids}
        data = self.request(
            "POST",
            "/docx/v1/documents/blocks/convert",
            json_body=payload,
        )
        blocks = data.get("blocks", [])
        # 转换接口返回的块里，根块需要剔除（它的 children 才是内容）
        return _flatten_descendant(blocks)

    def append_markdown(self, document_id, content, index=-1, chunk_size=50):
        """按转换结果追加块到文档。

        飞书 children 接口单次最多 50 块，超出会报
        `99992402 field validation failed (the max len is 50)`，
        因此按 chunk_size 自动分批。
        """
        blocks = _as_children_payload(self.convert_markdown(content))
        if not blocks:
            return {"children": [], "revision_id": None, "_batches": 0}

        written, batches = 0, 0
        last = None
        for start in range(0, len(blocks), chunk_size):
            chunk = blocks[start:start + chunk_size]
            last = self.request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                json_body={"children": chunk, "index": index},
            )
            written += len(chunk)
            batches += 1

        result = dict(last or {})
        result["children"] = blocks
        result["_batches"] = batches
        result["_written"] = written
        return result

    def replace_document(self, document_id, content):
        """整体替换：先列出并删除现有子块，再写入新内容。"""
        deleted = self.delete_all_children(document_id)
        result = self.append_markdown(document_id, content)
        result["_deleted"] = deleted
        return result

    def delete_all_children(self, document_id):
        """删除文档根下所有子块，返回删除数量。"""
        blocks = self.list_blocks(document_id)
        root_block = next((b for b in blocks
                           if b.get("block_type") == 1), None)
        if not root_block:
            return 0
        root = root_block["block_id"]
        children = root_block.get("children", []) or []
        if not children:
            return 0
        count = len(children)
        self.request(
            "DELETE",
            f"/docx/v1/documents/{document_id}/blocks/{root}/children/batch_delete",
            json_body={"start_index": 0, "end_index": count},
        )
        return count

    # ---------- 图片 ----------

    def upload_image(self, file_path, parent_node):
        """上传图片素材，返回 file_token。

        parent_node 必传，且必须指向**即将承载这张图的图片块 block_id**，
        不是 document_id。传 document_id 会报 1770013 relation mismatch。

        正确顺序：先建空图片块 → 拿 block_id → 上传（parent_node=block_id）
        → replace_image 填 token。
        """
        path = Path(file_path)
        if not path.exists():
            raise FeishuError(f"图片不存在: {path}")
        if not parent_node:
            raise FeishuError(
                "upload_image 需要 parent_node（承载图片的图片块 block_id）"
            )
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, _guess_mime(path))}
            data = {
                "parent_type": "docx_image",
                "parent_node": parent_node,
                "file_name": path.name,
                "size": str(path.stat().st_size),
            }
            return self.request(
                "POST", "/drive/v1/medias/upload_all",
                files=files, data=data,
            )

    def insert_image_block(self, document_id, file_path, index=-1):
        """把本地图片插入文档：建占位块 → 上传 → 填 token。

        三步顺序不能变，三者必须绑定同一个图片块：
          1. 用转换接口在文档里生成一个空图片块
          2. 上传素材，parent_node 指向该块的 block_id
          3. replace_image 把 token 填回该块
        """
        # 1) 生成空图片占位块（转接口只认 markdown，所以传个占位名）
        self.append_markdown(document_id, f"![]({Path(file_path).name})")

        blocks = self.list_blocks(document_id)
        empty = [b for b in blocks
                 if b.get("block_type") == 27
                 and not (b.get("image") or {}).get("token")]
        if not empty:
            raise FeishuError("未能生成图片占位块")
        target = empty[-1]["block_id"]

        # 2) 上传素材，绑定到该图片块
        up = self.upload_image(file_path, parent_node=target)
        file_token = up.get("file_token")
        if not file_token:
            raise FeishuError(f"上传未返回 file_token: {up}")

        # 3) 填入 token
        self.set_image_token(document_id, target, file_token)
        return {"block_id": target, "file_token": file_token}

    def set_image_token(self, document_id, block_id, file_token):
        """把已存在的空图片块替换为真实图片。"""
        return self.request(
            "PATCH",
            f"/docx/v1/documents/{document_id}/blocks/{block_id}",
            json_body={"replace_image": {"token": file_token}},
        )

    # ---------- 表格 ----------

    def create_table(self, document_id, rows, columns, index=-1):
        """建空表，返回表格块的 block_id。"""
        block = {
            "block_type": 31,
            "table": {
                "property": {
                    "row_size": rows,
                    "column_size": columns,
                    "column_width": [100] * columns,
                    "header_row": True,
                }
            },
        }
        data = self.request(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            json_body={"children": [block], "index": index},
        )
        table_id = _find_table_id(data)
        if not table_id:
            # 接口未回传 block_id 时，回读定位最后一个表格块
            blocks = self.list_blocks(document_id)
            tables = [b for b in blocks if b.get("block_type") == 31]
            if tables:
                table_id = tables[-1]["block_id"]
        return table_id

    def list_table_cells(self, document_id, table_block_id):
        """取表格的单元格块，按行优先顺序。

        单元格是表格块的直接 children，从 list_blocks 里按 parent_id 过滤。
        """
        cells = {}
        for b in self.list_blocks(document_id):
            if b.get("parent_id") == table_block_id:
                cells[b["block_id"]] = b

        # 按表格声明的 cells 顺序排列，保证行优先正确
        table = next((b for b in self.list_blocks(document_id)
                      if b["block_id"] == table_block_id), None)
        order = ((table or {}).get("table") or {}).get("cells") or []
        if order:
            return [cells[c] for c in order if c in cells]
        return list(cells.values())

    def write_cell_text(self, document_id, cell_block_id, text):
        """往单元格写入文本。单元格本身是容器，需写它的文本子块。"""
        cell = None
        for b in self.list_blocks(document_id):
            if b["block_id"] == cell_block_id:
                cell = b
                break
        if not cell:
            raise FeishuError(f"找不到单元格 {cell_block_id}")

        inner = (cell.get("children") or [None])[0]
        if not inner:
            raise FeishuError(
                f"单元格 {cell_block_id} 没有子文本块，"
                "表格可能未正确初始化"
            )
        return self.update_text(document_id, inner, text)

    def update_text(self, document_id, block_id, text):
        """替换文本块的文本内容。"""
        return self.request(
            "PATCH",
            f"/docx/v1/documents/{document_id}/blocks/{block_id}",
            json_body={
                "update_text_elements": {
                    "elements": [{"text_run": {"content": text}}]
                }
            },
        )

    def fill_table(self, document_id, table_block_id, grid):
        """按二维数组填表。grid[row][col]，缺省单元格跳过。"""
        cells = self.list_table_cells(document_id, table_block_id)
        if not cells:
            raise FeishuError(f"表格 {table_block_id} 没有单元格")
        table = next((b for b in self.list_blocks(document_id)
                      if b["block_id"] == table_block_id), {})
        prop = (table.get("table") or {}).get("property") or {}
        ncol = prop.get("column_size") or max(len(r) for r in grid)

        filled = 0
        for i, row in enumerate(grid):
            for j in range(ncol):
                idx = i * ncol + j
                if idx >= len(cells):
                    break
                text = row[j] if j < len(row) else ""
                if text == "":
                    continue
                self.write_cell_text(document_id, cells[idx]["block_id"], text)
                filled += 1
        return filled

    def convert_blocks(self, markdown_text):
        """Markdown → 飞书块列表（不含根容器）。

        转换接口返回的顶层是 Page 根块，真正内容在它的 children 里，
        需要展平成可直接写入的块数组。
        """
        data = self.request(
            "POST",
            "/docx/v1/documents/blocks/convert",
            json_body={"content_type": "markdown", "content": markdown_text},
        )
        blocks = data.get("blocks", [])
        return _as_children_payload(blocks)

    def insert_block(self, document_id, block, index):
        """在指定 index 插入单个块。

        逐块写入是保证文档顺序唯一可靠的方式：批量提交不保留数组顺序。
        单块写入仍受 50 块上限约束，但只传 1 块自然安全。
        """
        return self.request(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            json_body={"children": [block], "index": index},
        )

    def create_table_at(self, document_id, rows, columns, index):
        """在指定 index 建表，返回表格块 block_id。"""
        block = {
            "block_type": 31,
            "table": {
                "property": {
                    "row_size": rows,
                    "column_size": columns,
                    "column_width": [100] * columns,
                    "header_row": True,
                }
            },
        }
        data = self.request(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            json_body={"children": [block], "index": index},
        )
        table_id = _find_table_id(data)
        if not table_id:
            blocks = self.list_blocks(document_id)
            tables = [b for b in blocks if b.get("block_type") == 31]
            if tables:
                table_id = tables[-1]["block_id"]
        return table_id

    def insert_image_at(self, document_id, file_path, index):
        """在指定 index 插入本地图片。

        三步必须锁定同一个块（顺序不可变）：
          1. 在该 index 生成空图片块
          2. 上传素材，parent_node 指向这个块的 block_id
          3. replace_image 填回 token

        注意：单次只能插一张，index 为插入后的位置。
        """
        token = self.create_empty_image_at(document_id, index)
        up = self.upload_image(file_path, parent_node=token)
        file_token = up.get("file_token")
        if not file_token:
            raise FeishuError(f"上传未返回 file_token: {up}")
        self.set_image_token(document_id, token, file_token)
        return {"block_id": token, "file_token": file_token}

    def create_empty_image_at(self, document_id, index):
        """在 index 处建一个空图片块，返回其 block_id。"""
        blocks = self.convert_blocks("![](image.png)")
        if not blocks:
            raise FeishuError("无法生成图片占位块")
        data = self.request(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            json_body={"children": [blocks[0]], "index": index},
        )
        new_id = _find_image_block_id(data)
        if not new_id:
            # 回退：回读找最后一个空图片块
            allb = self.list_blocks(document_id)
            empty = [b for b in allb
                     if b.get("block_type") == 27
                     and not (b.get("image") or {}).get("token")]
            if not empty:
                raise FeishuError("未能定位新建的图片块")
            new_id = empty[-1]["block_id"]
        return new_id

    # ---------- 知识库（Wiki）----------

    def list_wiki_spaces(self, page_size=50, page_token=None):
        """列出知识空间。"""
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        return self.request("GET", "/wiki/v2/spaces", params=params)

    def get_wiki_node(self, token, obj_type="wiki"):
        """按 node_token 或 obj_token 取节点信息。"""
        return self.request(
            "GET", "/wiki/v2/spaces/get_node",
            params={"token": token, "obj_type": obj_type},
        )

    def list_wiki_nodes(self, space_id, parent_node_token=None,
                        page_size=50, page_token=None):
        """列出空间下节点，可按父节点过滤。"""
        params = {"page_size": page_size}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        if page_token:
            params["page_token"] = page_token
        return self.request(
            "GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params,
        )

    def create_wiki_node(self, space_id, title, obj_type="docx",
                         parent_node_token=None, node_type="origin"):
        """在知识空间里新建一个节点（默认建 docx 文档）。

        返回节点信息，含 node_token / obj_token / url。
        """
        payload = {
            "obj_type": obj_type,
            "node_type": node_type,
            "title": title,
        }
        if parent_node_token:
            payload["parent_node_token"] = parent_node_token
        data = self.request(
            "POST", f"/wiki/v2/spaces/{space_id}/nodes", json_body=payload,
        )
        return data.get("node", data)

    def move_doc_to_wiki(self, space_id, obj_token, parent_wiki_token=None,
                         obj_type="docx", apply=False):
        """把已有的云文档移入知识库。

        parent_wiki_token 为空则挂到空间根节点。
        若目标空间需要审批，先传 apply=True 发起申请。
        """
        payload = {
            "obj_type": obj_type,
            "obj_token": obj_token,
            "apply": apply,
        }
        if parent_wiki_token:
            payload["parent_wiki_token"] = parent_wiki_token
        return self.request(
            "POST",
            f"/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki",
            json_body=payload,
        )

    def update_wiki_title(self, space_id, node_token, title):
        """重命名知识库节点。"""
        return self.request(
            "POST",
            f"/wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title",
            json_body={"title": title},
        )

    def resolve_wiki_doc(self, token):
        """把 wiki 链接 token 解析成真实文档 token。

        知识库 URL 里的 token 是 node_token，写正文需要 obj_token。
        返回 (obj_token, obj_type, node_token, space_id)。
        """
        data = self.get_wiki_node(token)
        node = data.get("node", data)
        return (
            node.get("obj_token"),
            node.get("obj_type"),
            node.get("node_token"),
            node.get("space_id"),
        )

    # ---------- 权限 ----------

    def grant_permission(self, token, member_id, member_type="openid",
                         perm="edit", doc_type="docx"):
        return self.request(
            "POST",
            f"/drive/v1/permissions/{token}/members",
            params={"type": doc_type},
            json_body={
                "member_type": member_type,
                "member_id": member_id,
                "perm": perm,
            },
        )


# ---------- 内部工具 ----------


def _retry_after(resp, attempt):
    """计算限流重试等待秒数：优先用服务端 Retry-After，否则指数退避。"""
    header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if header:
        try:
            return max(1.0, float(header))
        except ValueError:
            pass
    return min(30.0, 1.5 * (2 ** attempt))


def _rewind_files(files):
    """重试前把 file 句柄回绕到开头。"""
    if not files:
        return
    for value in files.values():
        fh = value[1] if isinstance(value, tuple) and len(value) > 1 else None
        if hasattr(fh, "seek"):
            try:
                fh.seek(0)
            except Exception:
                pass


def _safe_body(resp):
    try:
        return resp.json()
    except Exception:
        return resp.text[:500]


def _guess_mime(path):
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _flatten_descendant(nodes):
    """转换接口返回根块 + 后代，展平成列表并保留层级。"""
    out = []

    def walk(node):
        out.append(node)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    for node in nodes:
        walk(node)
    return out


def _as_children_payload(blocks):
    """把转换结果整理成 children 写入格式（去掉根容器）。"""
    if not blocks:
        return []
    # convert 接口返回的第一个通常是 Page 根块，它的 children 才是内容
    root = blocks[0]
    if root.get("block_type") == 1 and root.get("children"):
        payload = []
        for child in root["children"]:
            payload.append(_strip_ids(child))
        return payload
    return [_strip_ids(b) for b in blocks]


def _placeholder_name(token):
    """为占位图片块生成一个稳定的伪文件名。"""
    return f"pending-{token[:12]}.png"


def _find_image_block_id(data):
    """从写入返回里挖出图片块 id。"""
    if not isinstance(data, dict):
        return None
    for key in ("children", "blocks", "items"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("block_type") == 27:
                return item.get("block_id")
    return None


def _find_table_id(data):
    """从写入返回里挖出表格块 id。"""
    if not isinstance(data, dict):
        return None
    for key in ("children", "blocks", "items"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("block_type") == 31:
                return item.get("block_id")
            nested = _find_table_id(item) if isinstance(item, dict) else None
            if nested:
                return nested
    return None


def _strip_ids(block):
    """写入时不应携带 block_id / parent_id。"""
    return {k: v for k, v in block.items()
            if k not in ("block_id", "parent_id", "children")}
