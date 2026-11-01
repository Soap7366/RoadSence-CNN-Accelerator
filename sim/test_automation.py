"""Behavioral checks for relocation, exclusion boundaries and deploy configuration."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src/stream'))
from build.package_files import package_files
from pscnn.runtime_config import service_command, load_config
import manage


class AutomationTests(unittest.TestCase):
    def test_distribution_ignores_machine_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            files=['README.md','src/a.py','.runtime/bin/python','board/local_config.json',
                   'board/known_hosts','src/core/build/out.log','sim/results/report.json']
            for name in files:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('x')
            self.assertEqual({p.relative_to(root).as_posix() for p in package_files(root)},
                             {'README.md','src/a.py'})

    def test_remote_command_quotes_arbitrary_release_paths(self):
        import shlex
        cfg=dict(remote_root='/home/test/release with space',remote_python='/usr/bin/python3')
        args=shlex.split(service_command('/home/test/output with space',golden=30,config=cfg))
        self.assertEqual(args[6],'/home/test/release with space/src/stream/tools/serve_images.py')
        self.assertIn('/home/test/release with space/board/base',args)
        self.assertIn('/home/test/output with space',args)

    def test_no_old_workspace_environment_leak(self):
        with patch.dict('os.environ',{'PYTHONPATH':'old-project','PSCNN_SSH_VENDOR':'old-ssh',
                                    'PSCNN_REMOTE_ROOT':'old-remote'}):
            env=manage.clean_env()
            self.assertNotIn('PYTHONPATH',env)
            self.assertNotIn('PSCNN_SSH_VENDOR',env)
            self.assertNotIn('PSCNN_REMOTE_ROOT',env)

    def test_board_deployment_rejects_legacy_directory(self):
        from build.remote_control import release_base
        for path in ('/','/home/xilinx','/home/xilinx/systolic-cnn-v0.7.0','/tmp/new'):
            with self.assertRaises(ValueError):release_base(dict(remote_base=path))
        self.assertEqual(release_base(dict(remote_base='/home/xilinx/pynq_cnn_delivery')),
                         '/home/xilinx/pynq_cnn_delivery')

    def test_cli_can_run_outside_package(self):
        with tempfile.TemporaryDirectory() as temp:
            result=subprocess.run([sys.executable,'-I',str(ROOT/'manage.py'),'--help'],cwd=temp,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_failed_deploy_keeps_active_config_and_closes_connection(self):
        from build import remote_control
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'config.json';config.write_text('{"remote_root":"previous"}')
            client=Mock()
            with patch.object(remote_control,'CONFIG',config), \
                 patch.object(remote_control,'load_config',return_value=dict(remote_base='/home/test/new_project')), \
                 patch.object(remote_control,'connect',return_value=(client,'secret',0)), \
                 patch.object(remote_control,'execute',side_effect=RuntimeError('preflight failed')):
                with self.assertRaises(RuntimeError):remote_control.deploy(None)
            self.assertEqual(json.loads(config.read_text())['remote_root'],'previous')
            client.close.assert_called_once()

    def test_trust_mismatch_never_writes_key_file(self):
        from build import remote_control
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'known_hosts'
            transport=Mock()
            transport.__enter__=Mock(return_value=transport)
            transport.__exit__=Mock(return_value=False)
            transport.get_remote_server_key.return_value.asbytes.return_value=b'host key'
            sock=Mock();sock.__enter__=Mock(return_value=sock);sock.__exit__=Mock(return_value=False)
            with patch.object(remote_control,'load_config',return_value=dict(host='test',port=22,known_hosts=str(path))), \
                 patch.object(remote_control.socket,'create_connection',return_value=sock), \
                 patch.object(remote_control.paramiko,'Transport',return_value=transport):
                with self.assertRaises(ValueError):remote_control.trust(type('Args',(),dict(fingerprint='SHA256:wrong'))())
            self.assertFalse(path.exists())

    def test_failed_config_publication_keeps_previous_bytes(self):
        from pscnn import runtime_config
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';path.write_text('old configuration')
            with patch.object(runtime_config.os,'replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):runtime_config.save_config({'remote_root':'new'},path)
            self.assertEqual(path.read_text(),'old configuration')
            self.assertEqual([p.name for p in Path(temp).iterdir()],['config.json'])

    def test_invalid_rebuilt_overlay_is_rejected_before_network(self):
        from build import remote_control
        with tempfile.TemporaryDirectory() as temp:
            args=type('Args',(),dict(overlay=temp))()
            with patch.object(remote_control,'connect') as connection:
                with self.assertRaises((ValueError,OSError)):remote_control.deploy(args)
            connection.assert_not_called()


if __name__=='__main__':unittest.main()
