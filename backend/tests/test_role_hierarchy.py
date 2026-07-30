from __future__ import annotations

import unittest

from utils.auth import normalize_role, role_satisfies


class RoleHierarchyTests(unittest.TestCase):
    def test_super_admin_satisfies_admin(self):
        self.assertTrue(role_satisfies("super-admin", ["admin"]))

    def test_admin_does_not_satisfy_super_admin(self):
        self.assertFalse(role_satisfies("admin", ["super-admin"]))

    def test_viewer_rejected_for_admin(self):
        self.assertFalse(role_satisfies("viewer", ["admin"]))

    def test_operator_satisfies_operator(self):
        self.assertTrue(role_satisfies("operator", ["admin", "operator"]))

    def test_viewer_rejected_for_operator(self):
        self.assertFalse(role_satisfies("viewer", ["operator"]))

    def test_admin_satisfies_operator(self):
        self.assertTrue(role_satisfies("admin", ["operator"]))

    def test_normalize_unknown_role(self):
        self.assertEqual(normalize_role("nope"), "viewer")
        self.assertEqual(normalize_role("SUPER-ADMIN"), "super-admin")


if __name__ == "__main__":
    unittest.main()
