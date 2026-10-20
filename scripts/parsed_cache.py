"""解析结果缓存。

PDF 原件体积大（约 140MB），不进 Git 仓库；但解析一份 278-T 要几十秒，
每天在 CI 里重解析全部文件太慢。这里把「解析结果」按 docId 缓存成小 JSON
（合计约 3MB），随仓库提交，于是：

  * CI 只下载「没有缓存」的新文件，旧文件直接读缓存
  * 缓存文件本身就是可复核的中间产物（每笔交易都带页码和原始 URL）
"""
from __future__ import annotations

from common import CACHE, read_json, write_json

PARSED = CACHE / "parsed"


def _path(kind: str, doc_id: str):
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(doc_id))
    return PARSED / f"{kind}_{safe}.json"


def get(kind: str, doc_id: str):
    return read_json(_path(kind, doc_id))


def put(kind: str, doc_id: str, value) -> None:
    write_json(_path(kind, doc_id), value)
