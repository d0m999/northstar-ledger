"""命理 AI 语料处理管道包。

提供语料数字化、清洗、标注与向量化的完整工作流。

Submodules:
    digitization: 多模态数字化（PDF OCR、音频转写）
    cleaning: 文本清洗与标准化
    annotation: 语料标注（实体识别、知识图谱）
    vector_store: 向量化与 Qdrant 索引

Pipeline:
    01_raw_inbox → digitization → cleaning → annotation → vector_store

Supported Formats:
    - PDF (via PaddleOCR)
    - DOCX, TXT (direct parsing)
    - Audio (via faster-whisper)
    - Images (multimodal extraction)

Example:
    >>> from astrology_corpus import __version__
    >>> print(__version__)
    0.1.0
"""

__version__ = "0.1.0"
__author__ = "Astrology AI Team"

# 待实现后取消注释并添加导出
# from .digitization import Digitizer
# from .cleaning import TextCleaner
# from .annotation import Annotator
# from .vector_store import VectorIndexer

__all__: list[str] = [
    "__version__",
    "__author__",
    # 待实现后添加:
    # "Digitizer",
    # "TextCleaner",
    # "Annotator",
    # "VectorIndexer",
]
