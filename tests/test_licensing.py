import hashlib
import unittest
from pathlib import Path


PROJECT_PATH = Path(__file__).resolve().parents[1]
EXPECTED_LICENSE_HASHES = {
    "LICENSE": (
        "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"
    ),
    "LICENSES/CC-BY-SA-4.0.txt": (
        "8d8fd68f111ee28719ecb913deaf086f4217a162abd1f6d25a92a7c8d52e5c22"
    ),
}


class LicensingTests(unittest.TestCase):
    def test_official_license_texts_are_unchanged(self) -> None:
        for relative_path, expected_hash in EXPECTED_LICENSE_HASHES.items():
            with self.subTest(relative_path=relative_path):
                content = (PROJECT_PATH / relative_path).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected_hash)

    def test_scope_map_separates_public_and_private_material(self) -> None:
        scope = (PROJECT_PATH / "LICENSE-SCOPE.md").read_text(
            encoding="utf-8"
        )
        normalized_scope = " ".join(scope.split())

        self.assertIn("Mozilla Public License 2.0", normalized_scope)
        self.assertIn(
            "Attribution-ShareAlike 4.0 International",
            normalized_scope,
        )
        self.assertIn("Content under `data/`", scope)
        self.assertIn("excluded from both", scope)
        self.assertIn("ASD-STE100", scope)


if __name__ == "__main__":
    unittest.main()
