import unittest
import tempfile
from pathlib import Path

import controller
import discovery
from mcp import server


class ArtifactTests(unittest.TestCase):
    def test_groups_all_split_gguf_shards(self):
        siblings = [
            {"rfilename": "model-Q4_K_M-00002-of-00002.gguf", "size": 20},
            {"rfilename": "model-Q4_K_M-00001-of-00002.gguf", "size": 10},
        ]
        artifact = discovery._choose_file(siblings)
        self.assertEqual(artifact["size"], 30)
        self.assertEqual(len(artifact["files"]), 2)
        self.assertTrue(artifact["filename"].endswith("00001-of-00002.gguf"))

    def test_rejects_incomplete_split(self):
        siblings = [{"rfilename": "model-Q4_K_M-00001-of-00002.gguf", "size": 10}]
        self.assertIsNone(discovery._choose_file(siblings))


class FitTests(unittest.TestCase):
    def test_cpu_only_machine(self):
        fit = discovery._fit(2 * discovery.GIB, {
            "gpus": [],
            "max_single_gpu_vram_bytes": 0,
            "total_vram_bytes": 0,
            "ram_available_bytes": 16 * discovery.GIB,
        })
        self.assertEqual(fit["tier"], "cpu_only")


class AvailabilityTests(unittest.TestCase):
    def test_rejects_partial_artifact_even_when_path_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "partial.gguf"
            artifact.write_bytes(b"partial")
            model = {
                "id": "partial",
                "manager": "direct",
                "required_files": [{"path": str(artifact), "size_bytes": 100}],
            }
            is_available, reasons = controller.available(model)
        self.assertFalse(is_available)
        self.assertIn("size", reasons[0])


class McpTests(unittest.TestCase):
    def test_initialize_and_tool_inventory(self):
        initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "local-model-control")
        self.assertEqual(initialized["result"]["serverInfo"]["version"], "0.2.0")
        self.assertEqual(len(listed["result"]["tools"]), 11)


if __name__ == "__main__":
    unittest.main()
