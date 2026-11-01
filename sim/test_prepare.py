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
from build import remote_control


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
        adapter.events.queue=[('complete',dict(samples=[{}]))]
        cfg=dict(host='192.168.2.99',remote_root='/home/xilinx/pynq_cnn_delivery/releases/release_test')
        with patch('pscnn.runtime_config.load_config',return_value=cfg), \
             patch('pscnn.runtime_config.save_config'),patch.object(prepare,'register_host'), \
             patch('sim.verify_package.check',return_value=dict(status='PASS')), \
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

    def test_task_deadline_and_cancellation_stop_child(self):
        import queue,threading,time,json
        from task_runner import run_task
        for mode in ('timeout','cancel'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                events=queue.Queue();cancel=threading.Event()
                if mode=='cancel':cancel.set()
                # Uses an actual child that prints a stage then blocks, matching
                # a transport stuck inside a C socket call.
                code="import sys,time,json;sys.stdin.readline();print(json.dumps(dict(kind='stage',text='blocked',timeout=.3)),flush=True);time.sleep(60)"
                started=time.monotonic()
                with self.assertRaises((TimeoutError,RuntimeError)):
                    run_task([sys.executable,'-u','-c',code],dict(password='private'),events,cancel,
                             Path(directory)/'task.log',startup_timeout=10,idle_timeout=10)
                self.assertLess(time.monotonic()-started,10)

    def test_task_redacts_secrets_and_requires_success(self):
        import queue,threading
        from task_runner import run_task
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'task.log';events=queue.Queue()
            code="import sys,json;p=json.loads(sys.stdin.readline())['password'];print(json.dumps(dict(kind='log',text='password='+p)));print(json.dumps(dict(kind='success')))"
            run_task([sys.executable,'-u','-c',code],dict(password='private-secret'),events,threading.Event(),path)
            self.assertNotIn('private-secret',path.read_text())
            self.assertIn('***',path.read_text())

    def test_network_timeout_does_not_redeploy(self):
        cfg=dict(host='192.168.2.99',remote_root='/home/xilinx/pynq_cnn_delivery/releases/release_test')
        with patch('pscnn.runtime_config.load_config',return_value=cfg), \
             patch('pscnn.runtime_config.save_config'),patch.object(prepare,'register_host'), \
             patch('sim.verify_package.check',return_value=dict(status='PASS')), \
             patch('build.remote_control.board_check',side_effect=TimeoutError('network timeout')), \
             patch('build.remote_control.deploy') as deploy:
            with self.assertRaises(TimeoutError):prepare.prepare_board('192.168.2.99','secret')
        deploy.assert_not_called()

    def test_same_subnet_does_not_prompt_to_change_network(self):
        import queue,json
        app=SimpleNamespace(events=queue.Queue())
        adapters=[dict(index=20,addresses=['192.168.2.1'])]
        with patch.object(prepare,'reachable',return_value=False), \
             patch('subprocess.check_output',return_value=json.dumps(adapters).encode()):
            prepare.Wizard.ensure_network(app,'192.168.2.99')
        self.assertFalse(hasattr(app,'selection_ready'))

    def test_completion_receipt_finishes_without_ssh_exit_status(self):
        from build.remote_control import execute,RemoteCommandError
        from unittest.mock import Mock
        for code in (0,3):
            channel=Mock()
            channel.recv_ready.side_effect=[True,False]
            channel.recv.return_value=f'result\nPSCNN_DONE_test:{code}\n'.encode()
            channel.recv_stderr_ready.return_value=False
            channel.exit_status_ready.return_value=False
            stdin=Mock();stdout=SimpleNamespace(channel=channel)
            client=SimpleNamespace(exec_command=lambda *a,**k:(stdin,stdout,Mock()))
            with self.subTest(code=code),patch('build.remote_control.uuid.uuid4',return_value=SimpleNamespace(hex='test')):
                if code:
                    with self.assertRaises(RemoteCommandError):execute(client,'false')
                else:self.assertEqual(execute(client,'true'),'result')
            channel.recv_exit_status.assert_not_called()

    def test_preparation_lock_releases_after_process_exit(self):
        from process_lock import SingleInstance
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prepare.lock'
            first=SingleInstance(path);second=SingleInstance(path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:first.close();second.close()

    def test_completed_remote_command_recovers_from_durable_receipt(self):
        import itertools
        from unittest.mock import Mock
        from build.remote_control import execute
        channel=Mock()
        channel.recv_ready.return_value=False
        channel.recv_stderr_ready.return_value=False
        channel.exit_status_ready.return_value=False
        probe=Mock()
        probe.recv_ready.side_effect=[True,False]
        probe.recv.return_value=b'0\ncompleted without SSH exit\n'
        probe.exit_status_ready.return_value=True
        client=Mock()
        client.exec_command.return_value=(Mock(),SimpleNamespace(channel=channel),Mock())
        client.get_transport.return_value.open_session.return_value=probe
        with patch('build.remote_control.time.monotonic',side_effect=itertools.count()), \
             patch('build.remote_control.time.sleep'):
            self.assertEqual(execute(client,'true'),'completed without SSH exit\n')
        channel.recv_exit_status.assert_not_called()


if __name__=='__main__':unittest.main()
