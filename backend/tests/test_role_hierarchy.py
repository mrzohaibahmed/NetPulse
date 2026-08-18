from __future__ import annotations

import unittest

from utils.auth import normalize_role, role_satisfies


class RoleHierarchyTests(unittest.TestCase):
    def test_admin_satisfies_user(self):
        self.assertTrue(role_satisfies("admin", ["user"]))

    def test_user_rejected_for_admin(self):
        self.assertFalse(role_satisfies("user", ["admin"]))

    def test_legacy_viewer_maps_to_user(self):
        self.assertTrue(role_satisfies("viewer", ["user"]))

    def test_legacy_operator_maps_to_user(self):
        self.assertTrue(role_satisfies("operator", ["user"]))

    def test_legacy_super_admin_maps_to_admin(self):
        self.assertTrue(role_satisfies("super-admin", ["admin"]))

    def test_normalize_unknown_role(self):
        self.assertEqual(normalize_role("nope"), "user")
        self.assertEqual(normalize_role("SUPER-ADMIN"), "admin")


if __name__ == "__main__":
    unittest.main()
