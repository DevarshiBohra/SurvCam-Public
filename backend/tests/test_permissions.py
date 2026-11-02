import os
import unittest

os.environ.setdefault("SURVCAM_JWT_SECRET", "unit-test-secret-" + "x" * 32)

from fastapi import HTTPException

import auth


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self._saved = set(auth.CONTROL_USERS)
        auth.CONTROL_USERS.clear()
        auth.CONTROL_USERS.add("owner")

    def tearDown(self):
        auth.CONTROL_USERS.clear()
        auth.CONTROL_USERS.update(self._saved)

    def test_admin_role_passes_admin_check(self):
        user = {"username": "alice", "role": "admin"}
        self.assertEqual(auth.require_admin(user), user)

    def test_control_user_passes_admin_check_even_with_user_role(self):
        user = {"username": "owner", "role": "user"}
        self.assertEqual(auth.require_admin(user), user)

    def test_plain_user_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.require_admin({"username": "bob", "role": "user"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_is_control_user_matches_exact_username_only(self):
        self.assertTrue(auth.is_control_user({"username": "owner"}))
        self.assertFalse(auth.is_control_user({"username": "Owner"}))
        self.assertFalse(auth.is_control_user({"username": "alice"}))


if __name__ == "__main__":
    unittest.main()
