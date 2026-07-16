"""排序規劃：以最長遞增子序列（LIS）計算「最少搬移次數」的移動計畫。

YouTube 的 playlistItems.update 每次要 50 units，搬移次數直接決定配額成本。
LIS 找出「相對順序已經正確的最大子集」讓它們原地不動，只搬其餘項目：
搬移次數 = n - len(LIS)，這是單項目任意插入模型下數學上的最小值。

本模組只做純計算、不碰網路，方便單元測試。
"""

from bisect import bisect_left
from typing import Hashable, List, NamedTuple, Sequence


class Move(NamedTuple):
    """一步搬移指令。position 是「執行當下」應搬到的位置（0-based），

    已將先前搬移造成的位移計算在內，依序執行即可得到理想順序。
    """

    item_id: Hashable
    position: int


def longest_increasing_subsequence_indices(sequence: Sequence[int]) -> List[int]:
    """回傳嚴格遞增最長子序列的「索引」清單（O(n log n)，patience sorting）。"""
    tail_values: List[int] = []  # tail_values[k]：長度 k+1 的遞增子序列中最小的結尾值
    tail_indices: List[int] = []  # 對應結尾值在 sequence 中的索引
    predecessors: List[int] = [-1] * len(sequence)

    for index, value in enumerate(sequence):
        k = bisect_left(tail_values, value)
        if k > 0:
            predecessors[index] = tail_indices[k - 1]
        if k == len(tail_values):
            tail_values.append(value)
            tail_indices.append(index)
        else:
            tail_values[k] = value
            tail_indices[k] = index

    result: List[int] = []
    i = tail_indices[-1] if tail_indices else -1
    while i != -1:
        result.append(i)
        i = predecessors[i]
    return result[::-1]


def plan_minimal_moves(
    current_order: Sequence[Hashable], ideal_order: Sequence[Hashable]
) -> List[Move]:
    """計算把 current_order 整理成 ideal_order 所需的最少搬移計畫。

    做法：
    1. 以理想順序給每個項目一個名次，LIS 找出可原地保留的最大子集。
    2. 其餘項目依理想位置由前到後逐一搬移；用本地模擬追蹤每一步的實際位置，
       確保回傳的 position 與執行當下的清單狀態一致。
    """
    if len(set(current_order)) != len(current_order):
        raise ValueError("current_order 的項目必須唯一（playlistItemId 天然唯一）")
    if len(current_order) != len(ideal_order) or set(current_order) != set(ideal_order):
        raise ValueError("current_order 與 ideal_order 必須包含完全相同的項目")

    ideal_rank = {item: rank for rank, item in enumerate(ideal_order)}
    ranks = [ideal_rank[item] for item in current_order]
    keep = {current_order[i] for i in longest_increasing_subsequence_indices(ranks)}

    working = list(current_order)
    moves: List[Move] = []
    for position, item in enumerate(ideal_order):
        if item in keep:
            continue
        working.remove(item)
        if position == 0:
            target = 0
        else:
            target = working.index(ideal_order[position - 1]) + 1
        working.insert(target, item)
        moves.append(Move(item_id=item, position=target))

    if working != list(ideal_order):
        raise AssertionError("搬移計畫模擬結果與理想順序不符（演算法錯誤）")
    return moves
