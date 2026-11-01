"""First-use security and network selection checks; no real network mutation."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'build'),str(ROOT/'src/stream')]
import prepare
import paramiko


class PrepareTests(unittest.TestCase):
    def test_address_validation(self):
        self.assertEqual(prepare.board_address(' 192.168.2.99 '),'192.168.2.99')
        for address in ('127.0.0.1','192.168.2.255','192.168.2.0','8.8.8.8','1;whoami','::1'):
            with self.subTest(address=address),self.assertRaises(ValueError): prepare.board_address(address)

    def test_authenticated_first_use_only_and_changed_key_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'known_hosts'
            key=paramiko.RSAKey.generate(1024)
            fake=SimpleNamespace(hostkeys=paramiko.HostKeys(),close=lambda:None)
            fake.get_host_keys=lambda:fake.hostkeys
            fake.set_missing_host_key_policy=lambda policy:setattr(fake,'policy',policy)
            def connect(*args,**kwargs): fake.policy.missing_host_key(fake,'192.168.2.99',key)
            fake.connect=connect
            fake.load_host_keys=lambda name:fake.hostkeys.load(name)
            config=dict(known_hosts=str(path),port=22,username='xilinx')
            with patch('pscnn.runtime_config.load_config',return_value=config),patch('paramiko.SSHClient',return_value=fake):
                fake.connect=lambda *a,**k:(_ for _ in ()).throw(paramiko.AuthenticationException())
                with self.assertRaises(paramiko.AuthenticationException): prepare.register_host('192.168.2.99','secret')
                self.assertFalse(path.exists())
                fake.connect=connect
                prepare.register_host('192.168.2.99','secret')
                original=path.read_bytes()
                with self.assertRaises(RuntimeError): prepare.register_host('192.168.2.99','secret')
                self.assertEqual(path.read_bytes(),original)

    def test_reachable_board_does_not_touch_network(self):
        with patch.object(prepare,'reachable',return_value=True),patch('subprocess.check_output') as inventory:
            prepare.Wizard.ensure_network(SimpleNamespace(),'192.168.2.99')
            inventory.assert_not_called()

    def test_finite_remote_command_sends_password_then_eof(self):
        from build.remote_control import execute
        from unittest.mock import Mock
        stdin=Mock();stdout=Mock();stderr=Mock()
        stdout.channel.recv_ready.side_effect=[True,False,False]
        stdout.channel.recv.return_value=b'done\n'
        stdout.channel.recv_stderr_ready.return_value=False
        stdout.channel.exit_status_ready.return_value=True
        stdout.channel.recv_exit_status.return_value=0
        client=SimpleNamespace(exec_command=lambda *a,**k:(stdin,stdout,stderr))
        self.assertEqual(execute(client,'command',password='secret'),'done\n')
        stdin.write.assert_called_once_with('secret\n')
        stdin.flush.assert_called_once()
        stdin.channel.shutdown_write.assert_called_once()

    def test_elevation_keeps_password_out_and_quotes_paths(self):
        import base64
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            prepare.Wizard.elevate(SimpleNamespace(),'apply',20,'192.168.2.100','192.168.2.99')
            command=base64.b64decode(run.call_args.args[0][-1]).decode('utf-16le')
            self.assertIn('-Verb RunAs',command)
            self.assertIn("'-Address','192.168.2.100'",command)
            self.assertNotIn('password',command.lower())

    def test_existing_deployment_reuses_one_in_memory_password(self):
        from unittest.mock import Mock
        adapter=Mock(ready=True)
        cfg=dict(host='192.168.2.99',remote_root='/home/xilinx/pynq_cnn_delivery/releases/release_test')
        with patch('pscnn.runtime_config.load_config',return_value=cfg), \
             patch('pscnn.runtime_config.save_config'),patch.object(prepare,'register_host'), \
             patch('build.remote_control.board_check') as check, \
             patch('build.remote_control.deploy') as deploy, \
             patch('board_adapter.BoardAdapter',return_value=adapter):
            prepare.prepare_board('192.168.2.99','memory-secret')
        deploy.assert_not_called()
        self.assertEqual([c.kwargs['password'] for c in check.call_args_list],['memory-secret']*2)
        self.assertEqual([c.args[0].boot for c in check.call_args_list],[False,True])
        adapter.connect.assert_called_once_with('192.168.2.99','memory-secret')
        adapter.process.assert_called_once()
        adapter.close.assert_called_once()


if __name__=='__main__':unittest.main()
