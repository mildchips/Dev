"""Run the suite with network connects and DNS technically blocked in-process."""
import os
import socket
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))

def blocked(*args,**kwargs):
    raise AssertionError('OFFLINE TEST GATE: outbound networking is forbidden')

socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket.create_connection=blocked
socket.getaddrinfo=blocked
raise SystemExit(pytest.main(sys.argv[1:] or ['-q']))
