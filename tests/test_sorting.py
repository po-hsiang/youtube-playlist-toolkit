"""sorting 模組單元測試：LIS 正確性與最少搬移計畫的模擬驗證。

執行：uv run python -m unittest discover -s tests -v
"""

import random
import unittest

from youtube_toolkit.sorting import longest_increasing_subsequence_indices, plan_minimal_moves


def apply_moves(current_order, moves):
    """依計畫逐步執行搬移（模擬 YouTube API 的移除→插入語意），回傳最終順序。"""
    working = list(current_order)
    for move in moves:
        working.remove(move.item_id)
        working.insert(move.position, move.item_id)
    return working


class TestLongestIncreasingSubsequence(unittest.TestCase):
    def test_empty_sequence(self):
        self.assertEqual(longest_increasing_subsequence_indices([]), [])

    def test_single_element(self):
        self.assertEqual(longest_increasing_subsequence_indices([42]), [0])

    def test_already_sorted(self):
        self.assertEqual(longest_increasing_subsequence_indices([1, 2, 3, 4]), [0, 1, 2, 3])

    def test_reversed(self):
        indices = longest_increasing_subsequence_indices([4, 3, 2, 1])
        self.assertEqual(len(indices), 1)

    def test_known_case(self):
        # [3, 0, 1, 4, 2] 的 LIS 是 [0, 1, 4] 或 [0, 1, 2]，長度 3
        sequence = [3, 0, 1, 4, 2]
        indices = longest_increasing_subsequence_indices(sequence)
        self.assertEqual(len(indices), 3)
        values = [sequence[i] for i in indices]
        self.assertEqual(values, sorted(values))  # 必須嚴格遞增
        self.assertEqual(indices, sorted(indices))  # 索引必須遞增（是子序列）

    def test_random_sequences_are_valid_subsequences(self):
        rng = random.Random(20260715)
        for _ in range(50):
            sequence = [rng.randint(0, 30) for _ in range(rng.randint(0, 40))]
            indices = longest_increasing_subsequence_indices(sequence)
            values = [sequence[i] for i in indices]
            self.assertEqual(indices, sorted(set(indices)))
            for a, b in zip(values, values[1:]):
                self.assertLess(a, b)


class TestPlanMinimalMoves(unittest.TestCase):
    def test_already_sorted_needs_zero_moves(self):
        order = ["a", "b", "c", "d"]
        self.assertEqual(plan_minimal_moves(order, order), [])

    def test_single_backward_move_beats_greedy(self):
        # 舊的逐位置演算法要搬 3 次（A、B、C 逐一往前拉）；LIS 只需把 D 搬到最後（1 次）
        current = ["D", "A", "B", "C"]
        ideal = ["A", "B", "C", "D"]
        moves = plan_minimal_moves(current, ideal)
        self.assertEqual(len(moves), 1)
        self.assertEqual(apply_moves(current, moves), ideal)

    def test_move_positions_account_for_prior_shifts(self):
        # 這個案例若直接用「理想位置」當搬移目標會得到錯誤結果，
        # 必須以「執行當下」的清單狀態計算位置。
        current = ["C", "A", "D", "B", "E"]
        ideal = ["A", "B", "C", "D", "E"]
        moves = plan_minimal_moves(current, ideal)
        self.assertEqual(apply_moves(current, moves), ideal)
        self.assertEqual(len(moves), 2)  # n - LIS = 5 - 3

    def test_move_count_equals_n_minus_lis(self):
        rng = random.Random(20260715)
        for _ in range(100):
            n = rng.randint(1, 60)
            ideal = list(range(n))
            current = ideal[:]
            rng.shuffle(current)

            rank = {item: idx for idx, item in enumerate(ideal)}
            lis_length = len(longest_increasing_subsequence_indices([rank[x] for x in current]))
            moves = plan_minimal_moves(current, ideal)

            self.assertEqual(len(moves), n - lis_length)
            self.assertEqual(apply_moves(current, moves), ideal)

    def test_rejects_duplicate_items(self):
        with self.assertRaises(ValueError):
            plan_minimal_moves(["a", "a", "b"], ["a", "b", "a"])

    def test_rejects_mismatched_items(self):
        with self.assertRaises(ValueError):
            plan_minimal_moves(["a", "b"], ["a", "c"])
        with self.assertRaises(ValueError):
            plan_minimal_moves(["a"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
