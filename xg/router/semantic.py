"""SmartRouter 语义编码器（第 6 期 C2，ONNX Runtime 加载 bge 产物）。

职责：把用户输入编码为 512 维语义向量，供 ml_router 精判时并入特征列
（``[TF-IDF + 数值 + 语义]``）；任何缺依赖 / 缺产物 / 产物损坏 情形一律
**静默回落**（encode 返回 None），行为退化为 ``ml_router`` 无语义时的原貌。

导入保护（主进程零硬性 ML 依赖）：
    onnxruntime / tokenizers / numpy 仅在 encode 运行期 import，任一把
    import 失败即在初始化时把本编码器标记为不可用（available=False），
    主进程绝不因缺依赖而报错。
"""

from __future__ import annotations

from pathlib import Path

_EMBED_DIM = 512  # bge-small-zh-v1.5 输出维度


def _default_onnx_path() -> Path:
    from ..adaptive.store import semantic_onnx_path
    return semantic_onnx_path()


class SemanticEncoder:
    """ONNX 语义编码器；不可用时 encode 返回 None（静默回落）。"""

    def __init__(self, onnx_path: Path | None = None) -> None:
        self._path = onnx_path or _default_onnx_path()
        self._session = None
        self._tokenizer = None
        self._load()

    def _load(self) -> None:
        """加载 ONNX session + tokenizer；任何异常 → 不可用。"""
        if not self._path.exists():
            return
        try:
            import onnxruntime  # noqa: PLC0415
            from tokenizers import Tokenizer  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415

            # bge 的 tokenizer 以 JSON 与产物同级目录存放（C1 导出时同目录）
            tok_path = self._path.with_suffix(".json")
            if not tok_path.exists():
                return
            tok = Tokenizer.from_file(str(tok_path))
            sess = onnxruntime.InferenceSession(
                str(self._path), providers=["CPUExecutionProvider"],
            )
            # 自检输出维度 = 512
            out_meta = sess.get_outputs()[0].shape
            dims = [d for d in out_meta if d not in (None, -1)]
            if dims and dims[-1] != _EMBED_DIM:
                return
            self._session = sess
            self._tokenizer = tok
        except Exception:
            self._session = None
            self._tokenizer = None

    @property
    def available(self) -> bool:
        return self._session is not None and self._tokenizer is not None

    @property
    def dim(self) -> int:
        return _EMBED_DIM

    def encode(self, text: str) -> list[float] | None:
        """把整段文本编码为 512 维向量；不可用返回 None（静默回落）。

        bge 取 first token（[CLS]）向量并做 L2 归一化。
        """
        if not self.available or not text:
            return None
        try:
            import numpy as np  # noqa: PLC0415
            enc = self._tokenizer.encode(text)
            ids = (enc.ids[:512] or [0]).__class__([  # 空输入兜底
                i for i in enc.ids[:512]
            ] or [0])[:512]
            mask = [1] * len(ids)
            input_ids = np.array([ids], dtype=np.int64)
            attention = np.array([mask], dtype=np.int64)
            feed = {"input_ids": input_ids, "attention_mask": attention}
            # bge/BERT 常要求 token_type_ids；单段输入给全零
            if "token_type_ids" in {i.name for i in self._session.get_inputs()}:
                feed["token_type_ids"] = np.zeros_like(input_ids)
            out = self._session.run(None, feed)[0]  # (1, seq, dim)
            vec = out[0][0].astype(np.float64)  # [CLS]
            norm = float(np.linalg.norm(vec))
            if norm < 1e-9:
                return [0.0] * _EMBED_DIM
            return (vec / norm).tolist()
        except Exception:
            return None


def load_semantic_encoder(onnx_path: Path | None = None) -> SemanticEncoder:
    """便捷工厂：缺依赖/缺产物时返回一个 available=False 的编码器。"""
    return SemanticEncoder(onnx_path)