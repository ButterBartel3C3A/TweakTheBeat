"""Demo hook module (D2 保底逃生舱)：演示 adapter.py 的固定接口。

钩子签名固定为 decode_*(payload: bytes) -> dict；用 AdapterBase 提供的
读取助手保持简短。
"""

from blecli.profiles.adapter_base import AdapterBase


def decode_float(payload: bytes) -> dict:
    """示例：F1 81 xx xx [4B float] xx xx -> 大端 float + 尾部字节。"""
    return {
        "value": AdapterBase.f32(payload, 4),
        "tail": AdapterBase.u16(payload, 8),
    }
