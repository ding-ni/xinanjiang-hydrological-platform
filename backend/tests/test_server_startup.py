"""Port conflicts must not modify an already running platform's task state."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import PlatformHTTPServer, main


class StartupTests(unittest.TestCase):
    def test_port_has_one_owner(self):
        server = PlatformHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):
                duplicate = PlatformHTTPServer(server.server_address, BaseHTTPRequestHandler)
                duplicate.server_close()
        finally:
            server.server_close()

    def test_conflict_does_not_recover_runtime(self):
        server = PlatformHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with patch('sys.argv', ['server.py', '--port', str(server.server_port)]), patch('server.TaskManager') as manager:
                with self.assertRaises(OSError):
                    main()
                manager.assert_not_called()
        finally:
            server.server_close()
