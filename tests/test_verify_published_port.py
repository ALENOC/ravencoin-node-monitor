import importlib.util
import pathlib
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "verify-published-port.py"
SPEC = importlib.util.spec_from_file_location("verify_published_port", MODULE_PATH)
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)


class VerifyPublishedPortTests(unittest.TestCase):
    def test_bindings_for_port_rejects_missing_or_null_mapping(self):
        self.assertEqual(verify.bindings_for_port({}, 8899), [])
        self.assertEqual(verify.bindings_for_port({"8899/tcp": None}, 8899), [])

    def test_bindings_for_port_accepts_real_host_mapping(self):
        ports = {
            "8899/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": "8899"},
                {"HostIp": "192.168.1.244", "HostPort": "8899"},
            ]
        }
        self.assertEqual(len(verify.bindings_for_port(ports, 8899)), 2)

    @mock.patch.object(verify, "_probe_health")
    @mock.patch.object(verify, "_inspect_ports")
    def test_verify_once_requires_both_docker_mapping_and_host_probe(self, inspect_ports, probe):
        inspect_ports.return_value = {
            "8899/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8899"}]
        }
        bindings = verify.verify_once("ravencoin-node-monitor", "127.0.0.1", 8899, 4.0)
        self.assertEqual(bindings[0]["HostPort"], "8899")
        probe.assert_called_once_with("127.0.0.1", 8899, 4.0)

    @mock.patch.object(verify.time, "sleep", return_value=None)
    @mock.patch.object(verify, "repair_once")
    @mock.patch.object(verify, "verify_once")
    def test_repair_is_attempted_exactly_once(self, verify_once, repair_once, _sleep):
        verify_once.side_effect = [
            verify.PublishError("missing mapping"),
            [{"HostIp": "127.0.0.1", "HostPort": "8899"}],
        ]
        result = verify.verify_with_optional_repair(
            container="ravencoin-node-monitor",
            host="127.0.0.1",
            port=8899,
            timeout=1.0,
            repair=True,
            compose_dir=pathlib.Path("/tmp/stack"),
            compose_files=["compose.yaml", "compose.monitor.yaml"],
            service="monitor",
            wait_seconds=3,
        )
        self.assertEqual(result[0]["HostPort"], "8899")
        repair_once.assert_called_once()

    def test_compose_repair_command_never_recreates_dependencies(self):
        command = verify._compose_command(["compose.yaml", "compose.monitor.yaml"], "monitor")
        self.assertIn("--no-deps", command)
        self.assertIn("--force-recreate", command)
        self.assertEqual(command[-1], "monitor")


if __name__ == "__main__":
    unittest.main()
