"""播放清單設定載入器：集中管理所有清單 ID（專案根目錄的 playlists.toml）。

各工具透過「清單名稱」取得 ID——要換目標清單，改 playlists.toml 裡的
target 字串即可；要調整每日排序的集合／順序，改 [sorter].order。
"""

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from youtube_toolkit import config

PLAYLISTS_FILE = config.BASE_DIR / "playlists.toml"


def load_all(path: Optional[Path] = None) -> Dict[str, str]:
    """回傳 {清單名稱: 清單 ID}。"""
    data = _read(path)
    playlists = data.get("playlists")
    if not isinstance(playlists, dict) or not playlists:
        raise ValueError(f"{_resolve(path)} 缺少 [playlists] 區段或內容為空")
    return dict(playlists)


def get_playlist_id(name: str, path: Optional[Path] = None) -> str:
    """依名稱取得清單 ID；名稱不存在時列出所有可用名稱。"""
    playlists = load_all(path)
    if name not in playlists:
        raise KeyError(f"找不到播放清單「{name}」。可用名稱：{'、'.join(playlists)}")
    return playlists[name]


def sorter_playlists(path: Optional[Path] = None) -> List[Tuple[str, str]]:
    """回傳每日排序的 [(名稱, 清單 ID)]，順序即 [sorter].order 的設定順序。"""
    data = _read(path)
    playlists = load_all(path)
    order = data.get("sorter", {}).get("order")
    if not isinstance(order, list) or not order:
        raise ValueError(f"{_resolve(path)} 缺少 [sorter].order 或內容為空")
    unknown = [name for name in order if name not in playlists]
    if unknown:
        raise ValueError(f"[sorter].order 出現未定義的清單名稱：{'、'.join(unknown)}。可用名稱：{'、'.join(playlists)}")
    return [(name, playlists[name]) for name in order]


def tool_target(section: str, path: Optional[Path] = None) -> Tuple[str, str]:
    """回傳指定工具區段（如 [playlist_search]）target 的 (名稱, 清單 ID)。"""
    data = _read(path)
    name = data.get(section, {}).get("target")
    if not name:
        raise ValueError(f"{_resolve(path)} 缺少 [{section}].target 設定")
    return name, get_playlist_id(name, path)


def _resolve(path: Optional[Path]) -> Path:
    return Path(path) if path else PLAYLISTS_FILE


def _read(path: Optional[Path] = None) -> Dict[str, Any]:
    file = _resolve(path)
    if not file.exists():
        raise FileNotFoundError(f"找不到播放清單設定檔：{file}（應位於專案根目錄，可入版控）")
    with open(file, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"播放清單設定檔格式錯誤（{file}）：{e}") from e
