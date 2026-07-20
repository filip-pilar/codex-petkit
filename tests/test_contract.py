from __future__ import annotations

import unittest

from petkit.contract import load_contract


class ContractTests(unittest.TestCase):
    def test_v2_geometry_rows_and_directions_are_self_consistent(self) -> None:
        contract = load_contract(2)
        self.assertEqual((contract.width, contract.height), (1536, 2288))
        self.assertEqual((contract.columns, contract.rows), (8, 11))
        self.assertEqual((contract.cell_width, contract.cell_height), (192, 208))
        self.assertEqual([state.row for state in contract.states], list(range(11)))
        self.assertEqual(len(contract.standard_states), 9)
        self.assertEqual(len(contract.look_states), 2)
        self.assertEqual(contract.look_directions_degrees[0], 0)
        self.assertEqual(contract.look_directions_degrees[-1], 337.5)
        self.assertEqual(contract.neutral_look_frame, (0, 6))
        self.assertTrue(contract.cell_is_used(contract.state("idle"), 6))
        self.assertEqual(contract.state("idle").frame_count, 6)
        self.assertEqual(contract.state("failed").frame_count, 8)
        self.assertEqual(contract.state("running").frame_count, 6)

    def test_v1_is_not_a_supported_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported pet contract version"):
            load_contract(1)


if __name__ == "__main__":
    unittest.main()
