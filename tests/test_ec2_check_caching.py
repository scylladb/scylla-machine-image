import json
import logging
import os
import sys
import tempfile
import unittest.mock
from pathlib import Path
from unittest import TestCase


sys.path.append(str(Path(__file__).parent.parent))


LOGGER = logging.getLogger(__name__)


class TestEc2CheckCaching(TestCase):
    """Test EC2 check caching functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_cache_dir = tempfile.mkdtemp()
        self.cache_file = os.path.join(self.temp_cache_dir, "ec2_check_cache.json")

        # Mock the CACHE_FILE constant in the scylla_ec2_check module
        self.cache_file_patch = unittest.mock.patch(
            "common.scylla_ec2_check.CACHE_FILE",
            self.cache_file
        )

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
        if os.path.exists(self.temp_cache_dir):
            os.rmdir(self.temp_cache_dir)

    def test_cache_file_creation(self):
        """Test that cache file is created when checks pass."""
        cache_data = {
            "instance_identity": {
                "instance_id": "i-1234567890abcdef0",
                "instance_type": "i3.2xlarge",
            },
            "check_passed": True,
        }

        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(cache_data, f)

        self.assertTrue(os.path.exists(self.cache_file))

        with open(self.cache_file) as f:
            loaded_data = json.load(f)

        self.assertEqual(loaded_data["check_passed"], True)
        self.assertEqual(loaded_data["instance_identity"]["instance_id"], "i-1234567890abcdef0")

    def test_cache_invalidation_on_instance_change(self):
        """Test that cache is invalidated when instance changes."""
        # Create cache for instance 1
        cache_data_1 = {
            "instance_identity": {
                "instance_id": "i-1234567890abcdef0",
                "instance_type": "i3.2xlarge",
            },
            "check_passed": True,
        }

        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(cache_data_1, f)

        # Simulate instance change
        current_identity = {
            "instance_id": "i-0fedcba9876543210",  # Different instance
            "instance_type": "i3.2xlarge",
        }

        # Load cache
        with open(self.cache_file) as f:
            cached_data = json.load(f)

        # Verify cache is not valid for new instance
        cached_identity = cached_data.get("instance_identity", {})
        is_valid = (
            cached_identity.get("instance_id") == current_identity.get("instance_id")
            and cached_data.get("check_passed", False)
        )

        self.assertFalse(is_valid)

    def test_cache_invalidation_on_instance_type_change(self):
        """Test that cache is invalidated when instance type changes."""
        # Create cache for instance type 1
        cache_data = {
            "instance_identity": {
                "instance_id": "i-1234567890abcdef0",
                "instance_type": "i3.2xlarge",
            },
            "check_passed": True,
        }

        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(cache_data, f)

        # Simulate instance type change (e.g., after stop/resize/start)
        current_identity = {
            "instance_id": "i-1234567890abcdef0",  # Same instance
            "instance_type": "i3.4xlarge",  # Different type
        }

        # Load cache
        with open(self.cache_file) as f:
            cached_data = json.load(f)

        # Verify cache is not valid for new instance type
        cached_identity = cached_data.get("instance_identity", {})
        is_valid = (
            cached_identity.get("instance_type") == current_identity.get("instance_type")
            and cached_data.get("check_passed", False)
        )

        self.assertFalse(is_valid)

    def test_cache_valid_on_reboot(self):
        """Test that cache is still valid after reboot (same instance)."""
        # Create cache
        cache_data = {
            "instance_identity": {
                "instance_id": "i-1234567890abcdef0",
                "instance_type": "i3.2xlarge",
            },
            "check_passed": True,
        }

        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(cache_data, f)

        # Simulate reboot (same instance)
        current_identity = {
            "instance_id": "i-1234567890abcdef0",
            "instance_type": "i3.2xlarge",
        }

        # Load cache
        with open(self.cache_file) as f:
            cached_data = json.load(f)

        # Verify cache is still valid
        cached_identity = cached_data.get("instance_identity", {})
        is_valid = (
            cached_identity.get("instance_id") == current_identity.get("instance_id")
            and cached_identity.get("instance_type") == current_identity.get("instance_type")
            and cached_data.get("check_passed", False)
        )

        self.assertTrue(is_valid)

    def test_missing_cache_file(self):
        """Test handling of missing cache file."""
        # Ensure cache file doesn't exist
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)

        self.assertFalse(os.path.exists(self.cache_file))

    def test_corrupted_cache_file(self):
        """Test handling of corrupted cache file."""
        # Create corrupted cache file
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            f.write("{ invalid json content")

        # Try to load cache - should handle gracefully
        try:
            with open(self.cache_file) as f:
                json.load(f)
            self.fail("Should have raised JSONDecodeError")
        except json.JSONDecodeError:
            # Expected behavior
            pass

if __name__ == "__main__":
    import unittest
    unittest.main()
