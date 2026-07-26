from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Callable


if sys.platform != "win32":
    raise SystemExit("This tool only runs on Windows.")


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_CREATE_THREAD = 0x0002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
THREAD_SUSPEND_RESUME = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READ = 0x20
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_BAD_LENGTH = 24
ERROR_NO_MORE_FILES = 18
ERROR_INSUFFICIENT_BUFFER = 122

GAME_EXE = "DespicableMe_w8.exe"
PACKAGE_FAMILY = "GAMELOFTSA.DespicableMeMinionRush_0pp20fcewvvtj"
AUMID = f"{PACKAGE_FAMILY}!App"

# Version lock: Minion Rush 4.1.4.1 x86.
EXPECTED_PE_TIMESTAMP = 0x5DB8FB3F
EXPECTED_IMAGE_SIZE = 0x012E3000
EXPECTED_MACHINE = 0x014C
EXPECTED_EXE_SHA256 = (
    "759E0CFA785E03AD93199565D9AF852CBC1CA355BEFD223C4FD5686A817D3A13"
)
CURRENCY_MAX = 999_999_999
STALE_BALANCE_STATUS = 0xE0010003

# All addresses are image-relative so ASLR is handled correctly.
ECONOMY_MGR_PTR_RVA = 0x011C7ECC
GAME_APP_PTR_RVA = 0x011C7E4C
BANANA_GETTER_RVA = 0x0070C850
TOKEN_GETTER_RVA = 0x0070D0A0
BANANA_MUTATOR_RVA = 0x0070A9E0
TOKEN_MUTATOR_RVA = 0x0070B3A0
TELEMETRY_DIRTY_RVA = 0x006D8690
DEFERRED_SAVE_REQUEST_RVA = 0x006D5010
SAVE_REASON_SHARED_PTR_RVA = 0x011DB42C

ROTATE_1_RVA = 0x011DB454
KEY_1_RVA = 0x011DB458
ROTATE_2_RVA = 0x011DB45C
KEY_2_RVA = 0x011DB460

RESULT_STATUS = 0
RESULT_STAGE = 4
RESULT_BEFORE_BANANAS = 8
RESULT_BEFORE_TOKENS = 12
RESULT_AFTER_BANANAS = 16
RESULT_AFTER_TOKENS = 20
RESULT_DELTA_BANANAS = 24
RESULT_DELTA_TOKENS = 28
RESULT_ECONOMY_MGR = 32
RESULT_GAME_APP = 36
RESULT_FLAGS_BEFORE = 40
RESULT_FLAGS_AFTER = 44
RESULT_GATE = 48
RESULT_SIZE = 52


class SaveConfirmationTimeout(TimeoutError):
    """The live values changed, but the corresponding disk write was not proven."""

    def __init__(
        self,
        message: str,
        *,
        pid: int,
        target_bananas: int,
        target_tokens: int,
        backup_dir: Path,
    ) -> None:
        super().__init__(message)
        self.pid = pid
        self.target_bananas = target_bananas
        self.target_tokens = target_tokens
        self.backup_dir = backup_dir


class GameRestartRequired(RuntimeError):
    """A native attempt ended without enough proof to permit a safe retry."""

    def __init__(
        self,
        message: str,
        *,
        pid: int,
        backup_dir: Path,
    ) -> None:
        super().__init__(message)
        self.pid = pid
        self.backup_dir = backup_dir


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32First.restype = wintypes.BOOL
kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32Next.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ProcessIdToSessionId.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
kernel32.GetPackageFamilyName.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(ctypes.c_uint32),
    wintypes.LPWSTR,
]
kernel32.GetPackageFamilyName.restype = wintypes.LONG
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
advapi32.EqualSid.restype = wintypes.BOOL
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.SuspendThread.argtypes = [wintypes.HANDLE]
kernel32.SuspendThread.restype = wintypes.DWORD
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualProtectEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.VirtualProtectEx.restype = wintypes.BOOL
kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.FlushInstructionCache.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
]
kernel32.FlushInstructionCache.restype = wintypes.BOOL
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeThread.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL


def win_error(prefix: str) -> OSError:
    error = ctypes.get_last_error()
    return OSError(error, f"{prefix}: {ctypes.FormatError(error).strip()}")


def find_pid(exe_name: str) -> int:
    current_session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(
        os.getpid(), ctypes.byref(current_session)
    ):
        raise win_error("ProcessIdToSessionId(current)")
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise win_error("CreateToolhelp32Snapshot(processes)")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        matches: list[int] = []
        while ok:
            if entry.szExeFile.casefold() == exe_name.casefold():
                candidate_session = wintypes.DWORD()
                if kernel32.ProcessIdToSessionId(
                    entry.th32ProcessID, ctypes.byref(candidate_session)
                ) and candidate_session.value == current_session.value:
                    matches.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if not matches:
        raise RuntimeError(
            f"{exe_name} is not running. Launch the game and wait at its main menu."
        )
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {exe_name} process, found PIDs {matches}.")
    return matches[0]


def find_module(pid: int, module_name: str) -> tuple[int, int, str]:
    last_error = 0
    for _ in range(20):
        last_error = 0
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
        )
        if snapshot == INVALID_HANDLE_VALUE:
            last_error = ctypes.get_last_error()
            if last_error == ERROR_BAD_LENGTH:
                time.sleep(0.05)
                continue
            raise win_error("CreateToolhelp32Snapshot(modules)")
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
            if not ok:
                last_error = ctypes.get_last_error()
            while ok:
                if entry.szModule.casefold() == module_name.casefold():
                    base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                    if base is None:
                        raise RuntimeError(
                            "The game module has a null base address."
                        )
                    return (
                        int(base),
                        int(entry.modBaseSize),
                        str(entry.szExePath),
                    )
                ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
            if not ok:
                next_error = ctypes.get_last_error()
                if next_error not in (0, ERROR_NO_MORE_FILES):
                    last_error = next_error
        finally:
            kernel32.CloseHandle(snapshot)
        if last_error in (ERROR_BAD_LENGTH, ERROR_NO_MORE_FILES):
            time.sleep(0.05)
            continue
        break
    if last_error:
        raise OSError(
            last_error,
            f"Could not enumerate {module_name}: "
            f"{ctypes.FormatError(last_error).strip()}",
        )
    raise RuntimeError(f"{module_name} was not found in PID {pid}.")


def open_process(pid: int, write: bool) -> wintypes.HANDLE:
    access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    if write:
        access |= (
            PROCESS_CREATE_THREAD
            | PROCESS_TERMINATE
            | PROCESS_VM_OPERATION
            | PROCESS_VM_WRITE
        )
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:
            raise RuntimeError(
                "Access to the game process was denied. Run this script normally "
                "from the same interactive Windows account that launched the "
                "game; do not use another admin/service account."
            )
        raise win_error(f"OpenProcess({pid}, write={write})")
    return handle


def validate_package_family(handle: wintypes.HANDLE) -> None:
    length = ctypes.c_uint32(0)
    status = kernel32.GetPackageFamilyName(
        handle, ctypes.byref(length), None
    )
    if status != ERROR_INSUFFICIENT_BUFFER or length.value == 0:
        raise RuntimeError(
            "The target is not the expected packaged Store game "
            f"(GetPackageFamilyName returned {status})."
        )
    buffer = ctypes.create_unicode_buffer(length.value)
    status = kernel32.GetPackageFamilyName(
        handle, ctypes.byref(length), buffer
    )
    if status != 0:
        raise OSError(
            status,
            f"GetPackageFamilyName: {ctypes.FormatError(status).strip()}",
        )
    if buffer.value.casefold() != PACKAGE_FAMILY.casefold():
        raise RuntimeError(
            f"Wrong package family: {buffer.value}; expected {PACKAGE_FAMILY}."
        )


def validate_same_user(handle: wintypes.HANDLE) -> None:
    def token_user(process: wintypes.HANDLE) -> tuple[wintypes.HANDLE, ctypes.Array]:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            process, TOKEN_QUERY, ctypes.byref(token)
        ):
            raise win_error("OpenProcessToken")
        try:
            needed = wintypes.DWORD()
            advapi32.GetTokenInformation(
                token,
                TOKEN_USER_CLASS,
                None,
                0,
                ctypes.byref(needed),
            )
            if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
                raise win_error("GetTokenInformation(size)")
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(
                token,
                TOKEN_USER_CLASS,
                buffer,
                needed,
                ctypes.byref(needed),
            ):
                raise win_error("GetTokenInformation(TokenUser)")
            return token, buffer
        except Exception:
            kernel32.CloseHandle(token)
            raise

    current_token, current_buffer = token_user(kernel32.GetCurrentProcess())
    target_token = None
    try:
        target_token, target_buffer = token_user(handle)
        current_sid = ctypes.cast(
            current_buffer, ctypes.POINTER(TOKEN_USER)
        ).contents.User.Sid
        target_sid = ctypes.cast(
            target_buffer, ctypes.POINTER(TOKEN_USER)
        ).contents.User.Sid
        if not advapi32.EqualSid(current_sid, target_sid):
            raise RuntimeError(
                "The script and game are running as different Windows users. "
                "Run this tool normally from the same interactive account that "
                "launched Minion Rush; do not use another admin/service account."
            )
    finally:
        if target_token:
            kernel32.CloseHandle(target_token)
        kernel32.CloseHandle(current_token)


def read_memory(handle: wintypes.HANDLE, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    received = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(received),
    )
    if not ok or received.value != size:
        raise win_error(f"ReadProcessMemory(0x{address:08X}, {size})")
    return buffer.raw


def write_memory(handle: wintypes.HANDLE, address: int, data: bytes) -> None:
    source = ctypes.create_string_buffer(data)
    written = ctypes.c_size_t()
    ok = kernel32.WriteProcessMemory(
        handle,
        ctypes.c_void_p(address),
        source,
        len(data),
        ctypes.byref(written),
    )
    if not ok or written.value != len(data):
        raise win_error(f"WriteProcessMemory(0x{address:08X}, {len(data)})")


def read_u32(handle: wintypes.HANDLE, address: int) -> int:
    return struct.unpack("<I", read_memory(handle, address, 4))[0]


def validate_game(
    handle: wintypes.HANDLE, module_base: int, module_size: int
) -> tuple[int, int]:
    if module_size != EXPECTED_IMAGE_SIZE:
        raise RuntimeError(
            f"Unsupported game image size 0x{module_size:X}; "
            f"expected 0x{EXPECTED_IMAGE_SIZE:X}."
        )
    dos = read_memory(handle, module_base, 0x40)
    if dos[:2] != b"MZ":
        raise RuntimeError("The live main module does not have a valid DOS header.")
    pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
    header = read_memory(handle, module_base + pe_offset, 0x100)
    if header[:4] != b"PE\0\0":
        raise RuntimeError("The live main module does not have a valid PE header.")
    machine, _, timestamp = struct.unpack_from("<HHI", header, 4)
    optional_magic = struct.unpack_from("<H", header, 24)[0]
    image_size = struct.unpack_from("<I", header, 24 + 56)[0]
    if (
        machine != EXPECTED_MACHINE
        or optional_magic != 0x10B
        or timestamp != EXPECTED_PE_TIMESTAMP
        or image_size != EXPECTED_IMAGE_SIZE
    ):
        raise RuntimeError(
            "Unsupported executable version: "
            f"machine=0x{machine:04X}, timestamp=0x{timestamp:08X}, "
            f"image_size=0x{image_size:X}."
        )

    signatures = {
        BANANA_GETTER_RVA: bytes.fromhex("55 8B EC 83 EC 08"),
        TOKEN_GETTER_RVA: bytes.fromhex("55 8B EC 83 EC 08"),
        BANANA_MUTATOR_RVA: bytes.fromhex("55 8B EC 83 EC 0C"),
        TOKEN_MUTATOR_RVA: bytes.fromhex("55 8B EC 83 EC 0C"),
        TELEMETRY_DIRTY_RVA: bytes.fromhex("C6 81 C7 04 00 00 01 C3"),
        DEFERRED_SAVE_REQUEST_RVA: bytes.fromhex(
            "55 8B EC 57 8B F9 80 BF 14 07 00 00 00"
        ),
    }
    for rva, expected in signatures.items():
        actual = read_memory(handle, module_base + rva, len(expected))
        if actual != expected:
            raise RuntimeError(
                f"Code signature mismatch at RVA 0x{rva:X}: {actual.hex(' ')}"
            )

    economy_mgr = read_u32(handle, module_base + ECONOMY_MGR_PTR_RVA)
    game_app = read_u32(handle, module_base + GAME_APP_PTR_RVA)
    if not economy_mgr:
        raise RuntimeError(
            "The economy manager is not initialized. Wait at the main menu and retry."
        )
    if not game_app:
        raise RuntimeError(
            "The game app object is not initialized. Wait at the main menu and retry."
        )
    readiness = read_memory(handle, game_app, 0x715)
    required = {0x230: 1, 0x570: 1, 0x714: 1}
    bad = {offset: readiness[offset] for offset, wanted in required.items() if readiness[offset] != wanted}
    if bad:
        details = ", ".join(f"+0x{k:X}={v}" for k, v in bad.items())
        raise RuntimeError(
            f"The game is not save-ready ({details}). Wait at its main menu and retry."
        )
    return economy_mgr, game_app


def ror32(value: int, count: int) -> int:
    count &= 31
    value &= 0xFFFFFFFF
    if count == 0:
        return value
    return ((value >> count) | (value << (32 - count))) & 0xFFFFFFFF


def signed32(value: int) -> int:
    return value if value < 0x80000000 else value - 0x1_0000_0000


def validate_currency_value(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number.")
    if not 0 <= value <= CURRENCY_MAX:
        raise ValueError(
            f"{name} must be between 0 and {CURRENCY_MAX:,}."
        )
    return value


def decode_protected_int(
    handle: wintypes.HANDLE,
    field_address: int,
    rotate_1: int,
    key_1: int,
    rotate_2: int,
    key_2: int,
) -> int:
    encoded_1, _, encoded_2 = struct.unpack(
        "<III", read_memory(handle, field_address, 12)
    )
    first = signed32(ror32(encoded_1 ^ key_1 ^ field_address, rotate_1))
    second = signed32(ror32(encoded_2 ^ key_2 ^ field_address, rotate_2))
    if first == second:
        return first
    return max(0, min(first, second))


def read_currencies(
    handle: wintypes.HANDLE, module_base: int
) -> tuple[int, int]:
    economy_mgr = read_u32(handle, module_base + ECONOMY_MGR_PTR_RVA)
    if not economy_mgr:
        raise RuntimeError("The economy manager disappeared.")
    profile = read_u32(handle, economy_mgr + 0x18)
    if not profile:
        raise RuntimeError("The active save profile is not available.")
    record = profile + 8
    rotate_1 = read_u32(handle, module_base + ROTATE_1_RVA)
    key_1 = read_u32(handle, module_base + KEY_1_RVA)
    rotate_2 = read_u32(handle, module_base + ROTATE_2_RVA)
    key_2 = read_u32(handle, module_base + KEY_2_RVA)
    bananas = decode_protected_int(
        handle, record + 0x10, rotate_1, key_1, rotate_2, key_2
    )
    tokens = decode_protected_int(
        handle, record + 0x20, rotate_1, key_1, rotate_2, key_2
    )
    return bananas, tokens


def pack_u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"Value does not fit x86 u32: {value}")
    return struct.pack("<I", value)


class X86Builder:
    def __init__(self) -> None:
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    def emit(self, data: bytes) -> None:
        self.code += data

    def u32(self, value: int) -> None:
        self.emit(pack_u32(value))

    def label(self, name: str) -> None:
        if name in self.labels:
            raise AssertionError(f"Duplicate x86 label: {name}")
        self.labels[name] = len(self.code)

    def jz(self, label: str) -> None:
        self.emit(b"\x0F\x84")
        self.fixups.append((len(self.code), label))
        self.emit(b"\x00\x00\x00\x00")

    def jne(self, label: str) -> None:
        self.emit(b"\x0F\x85")
        self.fixups.append((len(self.code), label))
        self.emit(b"\x00\x00\x00\x00")

    def finish(self) -> bytes:
        for position, label in self.fixups:
            if label not in self.labels:
                raise AssertionError(f"Missing x86 label: {label}")
            displacement = self.labels[label] - (position + 4)
            struct.pack_into("<i", self.code, position, displacement)
        return bytes(self.code)


def build_forge_stub(
    module_base: int,
    result: int,
    expected_before_bananas: int,
    expected_before_tokens: int,
    target_bananas: int,
    target_tokens: int,
) -> bytes:
    economy_global = module_base + ECONOMY_MGR_PTR_RVA
    app_global = module_base + GAME_APP_PTR_RVA
    get_bananas = module_base + BANANA_GETTER_RVA
    get_tokens = module_base + TOKEN_GETTER_RVA
    add_bananas = module_base + BANANA_MUTATOR_RVA
    add_tokens = module_base + TOKEN_MUTATOR_RVA
    telemetry_dirty = module_base + TELEMETRY_DIRTY_RVA
    save_request = module_base + DEFERRED_SAVE_REQUEST_RVA
    save_reason = module_base + SAVE_REASON_SHARED_PTR_RVA

    b = X86Builder()
    b.emit(b"\x55\x8B\xEC\x83\xEC\x04")  # prologue, one local DWORD
    b.emit(b"\xC7\x45\xFC\x00\x00\x00\x00")  # selector = 0
    # Signal that this new thread has completed normal Windows thread startup,
    # then wait outside the critical section. The host freezes the pre-existing
    # game threads only after seeing this signal, avoiding loader-lock deadlocks.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STATUS)
    b.u32(0x100)
    b.label("wait_for_gate")
    b.emit(b"\x83\x3D")
    b.u32(result + RESULT_GATE)
    b.emit(b"\x01")
    b.jz("gate_open")
    b.emit(b"\x83\x3D")
    b.u32(result + RESULT_GATE)
    b.emit(b"\x02")
    b.jz("aborted")
    b.emit(b"\xF3\x90")  # pause
    b.emit(b"\xE9")
    gate_wait_jump = len(b.code)
    b.emit(b"\x00\x00\x00\x00")
    b.label("gate_open")
    struct.pack_into(
        "<i",
        b.code,
        gate_wait_jump,
        b.labels["wait_for_gate"] - (gate_wait_jump + 4),
    )
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(1)

    # Resolve and validate the primary economy manager.
    b.emit(b"\xA1")
    b.u32(economy_global)
    b.emit(b"\x85\xC0")
    b.jz("failure")
    b.emit(b"\x83\x78\x18\x00")
    b.jz("failure")
    b.emit(b"\xA3")
    b.u32(result + RESULT_ECONOMY_MGR)

    # Recheck save readiness inside the frozen section, before either value is
    # touched. The host-side readiness check may have gone stale during setup.
    b.emit(b"\xA1")
    b.u32(app_global)
    b.emit(b"\x85\xC0")
    b.jz("failure")
    for offset in (0x230, 0x570, 0x714):
        b.emit(b"\x80\xB8")
        b.u32(offset)
        b.emit(b"\x01")
        b.jne("failure")
    b.emit(b"\xA3")
    b.u32(result + RESULT_GAME_APP)
    b.emit(b"\x8B\x88\x70\x05\x00\x00")
    b.emit(b"\x89\x0D")
    b.u32(result + RESULT_FLAGS_BEFORE)

    # Read initial bananas.
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_bananas)
    b.emit(b"\xFF\xD0\xA3")
    b.u32(result + RESULT_BEFORE_BANANAS)

    # Read initial tokens.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(2)
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_tokens)
    b.emit(b"\xFF\xD0\xA3")
    b.u32(result + RESULT_BEFORE_TOKENS)

    # The GUI expresses changes relative to a balance read just before the
    # backup. Recheck both balances after every pre-existing game thread is
    # frozen. If either changed in the meantime, exit before either mutator is
    # called instead of applying an addition to stale data.
    b.emit(b"\xA1")
    b.u32(result + RESULT_BEFORE_BANANAS)
    b.emit(b"\x3D")
    b.u32(expected_before_bananas)
    b.jne("stale_balance")
    b.emit(b"\xA1")
    b.u32(result + RESULT_BEFORE_TOKENS)
    b.emit(b"\x3D")
    b.u32(expected_before_tokens)
    b.jne("stale_balance")

    # Compute a signed delta and let the game's banana mutator encode it.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(3)
    b.emit(b"\xB8")
    b.u32(target_bananas)
    b.emit(b"\x2B\x05")
    b.u32(result + RESULT_BEFORE_BANANAS)
    b.emit(b"\xA3")
    b.u32(result + RESULT_DELTA_BANANAS)
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x50\xB8")
    b.u32(add_bananas)
    b.emit(b"\xFF\xD0")

    # Compute and apply the token delta through the native token mutator.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(4)
    b.emit(b"\xB8")
    b.u32(target_tokens)
    b.emit(b"\x2B\x05")
    b.u32(result + RESULT_BEFORE_TOKENS)
    b.emit(b"\xA3")
    b.u32(result + RESULT_DELTA_TOKENS)
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x50\xB8")
    b.u32(add_tokens)
    b.emit(b"\xFF\xD0")

    # Verify both values again with the game's own getters.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(5)
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_bananas)
    b.emit(b"\xFF\xD0\xA3")
    b.u32(result + RESULT_AFTER_BANANAS)
    b.emit(b"\x3D")
    b.u32(target_bananas)
    b.jne("rollback_failure")
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_tokens)
    b.emit(b"\xFF\xD0\xA3")
    b.u32(result + RESULT_AFTER_TOKENS)
    b.emit(b"\x3D")
    b.u32(target_tokens)
    b.jne("rollback_failure")

    # Mirror the game's normal follow-up: mark telemetry dirty, then ask for a
    # deferred Game-save. With immediate=0 AD5010 only sets request flags; the
    # native main loop later serializes and writes the save on its own thread.
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STAGE)
    b.u32(6)
    b.emit(b"\x8B\x0D")
    b.u32(app_global)
    b.emit(b"\xB8")
    b.u32(telemetry_dirty)
    b.emit(b"\xFF\xD0")
    b.emit(b"\x68")
    b.u32(save_reason)
    b.emit(b"\x6A\x01\x6A\x00\x6A\x00")
    b.emit(b"\x8B\x0D")
    b.u32(app_global)
    b.emit(b"\xB8")
    b.u32(save_request)
    b.emit(b"\xFF\xD0")
    b.emit(b"\xA1")
    b.u32(app_global)
    b.emit(b"\x8B\x88\x70\x05\x00\x00")
    b.emit(b"\x89\x0D")
    b.u32(result + RESULT_FLAGS_AFTER)
    b.emit(b"\x80\xB8\x71\x05\x00\x00\x01")
    b.jne("rollback_failure")
    b.emit(b"\x80\xB8\x73\x05\x00\x00\x01")
    b.jne("rollback_failure")

    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STATUS)
    b.u32(1)
    b.emit(b"\x33\xC0\x8B\xE5\x5D\xC2\x04\x00")

    b.label("aborted")
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STATUS)
    b.u32(0xE0010002)
    b.emit(b"\xB8")
    b.u32(0xE0010002)
    b.emit(b"\x8B\xE5\x5D\xC2\x04\x00")

    b.label("stale_balance")
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STATUS)
    b.u32(STALE_BALANCE_STATUS)
    b.emit(b"\xB8")
    b.u32(STALE_BALANCE_STATUS)
    b.emit(b"\x8B\xE5\x5D\xC2\x04\x00")

    # If a post-mutation verification or save-request check fails, query the
    # actual current values and restore each exact pre-forge value natively.
    b.label("rollback_failure")
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_bananas)
    b.emit(b"\xFF\xD0\x8B\x15")
    b.u32(result + RESULT_BEFORE_BANANAS)
    b.emit(b"\x2B\xD0\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x52\xB8")
    b.u32(add_bananas)
    b.emit(b"\xFF\xD0")
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_tokens)
    b.emit(b"\xFF\xD0\x8B\x15")
    b.u32(result + RESULT_BEFORE_TOKENS)
    b.emit(b"\x2B\xD0\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x52\xB8")
    b.u32(add_tokens)
    b.emit(b"\xFF\xD0")
    # Cancel any deferred save flags that the rejected attempt changed.
    b.emit(b"\xA1")
    b.u32(app_global)
    b.emit(b"\x8B\x0D")
    b.u32(result + RESULT_FLAGS_BEFORE)
    b.emit(b"\x89\x88\x70\x05\x00\x00")
    # Verify the rollback with both native getters.
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_bananas)
    b.emit(b"\xFF\xD0\x3B\x05")
    b.u32(result + RESULT_BEFORE_BANANAS)
    b.jne("failure")
    b.emit(b"\x8B\x0D")
    b.u32(economy_global)
    b.emit(b"\x8D\x55\xFC\x52\xB8")
    b.u32(get_tokens)
    b.emit(b"\xFF\xD0\x3B\x05")
    b.u32(result + RESULT_BEFORE_TOKENS)
    b.jne("failure")

    b.label("failure")
    b.emit(b"\xC7\x05")
    b.u32(result + RESULT_STATUS)
    b.u32(0xE0010001)
    b.emit(b"\xB8")
    b.u32(0xE0010001)
    b.emit(b"\x8B\xE5\x5D\xC2\x04\x00")
    return b.finish()


def suspend_process_threads(
    pid: int, exclude_thread_id: int | None = None
) -> list[wintypes.HANDLE]:
    def snapshot_thread_ids() -> set[int]:
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise win_error("CreateToolhelp32Snapshot(threads)")
        found: set[int] = set()
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                thread_id = int(entry.th32ThreadID)
                if (
                    int(entry.th32OwnerProcessID) == pid
                    and thread_id != exclude_thread_id
                ):
                    found.add(thread_id)
                ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return found

    suspended_ids: set[int] = set()
    suspended: list[wintypes.HANDLE] = []
    try:
        for _ in range(8):
            thread_ids = snapshot_thread_ids()
            new_ids = thread_ids - suspended_ids
            if not new_ids:
                if not suspended:
                    raise RuntimeError("No game threads could be suspended.")
                return suspended
            for thread_id in sorted(new_ids):
                handle = kernel32.OpenThread(
                    THREAD_SUSPEND_RESUME, False, thread_id
                )
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:
                        continue  # Exited between snapshot and OpenThread.
                    raise win_error(f"OpenThread({thread_id})")
                previous = kernel32.SuspendThread(handle)
                if previous == 0xFFFFFFFF:
                    kernel32.CloseHandle(handle)
                    raise win_error(f"SuspendThread({thread_id})")
                suspended.append(handle)
                suspended_ids.add(thread_id)
        raise RuntimeError(
            "The game kept creating threads during the safety freeze."
        )
    except Exception:
        resume_process_threads(suspended)
        raise


def resume_process_threads(handles: list[wintypes.HANDLE]) -> None:
    failures: list[int] = []
    for handle in reversed(handles):
        if kernel32.ResumeThread(handle) == 0xFFFFFFFF:
            failures.append(ctypes.get_last_error())
        kernel32.CloseHandle(handle)
    handles.clear()
    if failures:
        error = failures[0]
        raise OSError(
            error,
            f"ResumeThread failed for {len(failures)} game thread(s): "
            f"{ctypes.FormatError(error).strip()}",
        )


def execute_forge(
    handle: wintypes.HANDLE,
    pid: int,
    module_base: int,
    expected_before_bananas: int,
    expected_before_tokens: int,
    target_bananas: int,
    target_tokens: int,
    save_path: Path,
    save_baseline: tuple[int, int, str | None],
) -> dict[str, int]:
    validate_currency_value(
        "Expected banana balance", expected_before_bananas
    )
    validate_currency_value(
        "Expected token balance", expected_before_tokens
    )
    validate_currency_value("Banana target", target_bananas)
    validate_currency_value("Token target", target_tokens)

    code_address = kernel32.VirtualAllocEx(
        handle, None, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    )
    if not code_address:
        raise win_error("VirtualAllocEx(code)")
    result_address = kernel32.VirtualAllocEx(
        handle, None, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    )
    if not result_address:
        kernel32.VirtualFreeEx(handle, code_address, 0, MEM_RELEASE)
        raise win_error("VirtualAllocEx(result)")

    thread = None
    suspended: list[wintypes.HANDLE] = []
    thread_finished = False
    gate_opened = False
    try:
        code = build_forge_stub(
            module_base,
            int(result_address),
            expected_before_bananas,
            expected_before_tokens,
            target_bananas,
            target_tokens,
        )
        if len(code) > 0x1000:
            raise AssertionError("The x86 forge stub exceeded one page.")
        write_memory(handle, int(code_address), code)
        if read_memory(handle, int(code_address), len(code)) != code:
            raise RuntimeError("Remote code verification failed.")
        write_memory(handle, int(result_address), b"\x00" * RESULT_SIZE)
        old_protection = wintypes.DWORD()
        if not kernel32.VirtualProtectEx(
            handle,
            code_address,
            0x1000,
            PAGE_EXECUTE_READ,
            ctypes.byref(old_protection),
        ):
            raise win_error("VirtualProtectEx(code)")
        if not kernel32.FlushInstructionCache(handle, code_address, len(code)):
            raise win_error("FlushInstructionCache")

        thread_id = wintypes.DWORD()
        thread = kernel32.CreateRemoteThread(
            handle,
            None,
            0,
            code_address,
            None,
            0,
            ctypes.byref(thread_id),
        )
        if not thread:
            raise win_error("CreateRemoteThread")

        # Wait until the remote thread has completed normal Windows thread
        # startup and reached our gate. Only then freeze the old game threads;
        # this avoids suspending a thread that owns the loader lock while the
        # new thread is still receiving DLL_THREAD_ATTACH notifications.
        ready_deadline = time.monotonic() + 5.0
        while time.monotonic() < ready_deadline:
            status = read_u32(handle, int(result_address) + RESULT_STATUS)
            if status == 0x100:
                break
            early_wait = kernel32.WaitForSingleObject(thread, 0)
            if early_wait == WAIT_OBJECT_0:
                thread_finished = True
                raise RuntimeError(
                    "The native forge thread exited before reaching its safety gate."
                )
            if early_wait == WAIT_FAILED:
                raise win_error("WaitForSingleObject(readiness)")
            time.sleep(0.01)
        else:
            raise TimeoutError(
                "The native forge thread did not reach its safety gate."
            )

        # The core mutators briefly rewrite a 12-byte protected integer. Freeze
        # the existing game threads so none can observe that field mid-update.
        try:
            suspended = suspend_process_threads(pid, int(thread_id.value))
            # The backup was made while the game was live. Recheck its cheap
            # stat fingerprint after the freeze; if an autosave raced it, abort
            # before any native mutation and let the caller retry safely.
            frozen_stat = save_path.stat()
            if (
                frozen_stat.st_mtime_ns != save_baseline[0]
                or frozen_stat.st_size != save_baseline[1]
                or file_sha256(save_path) != save_baseline[2]
            ):
                raise RuntimeError(
                    "The save changed after its backup was captured. "
                    "No currency was changed; run the tool again."
                )
            write_memory(
                handle,
                int(result_address) + RESULT_GATE,
                struct.pack("<I", 1),
            )
            gate_opened = True
        except Exception:
            # Gate state 2 exits without touching game state. This prevents a
            # permanently spinning remote thread if the safety freeze fails.
            try:
                write_memory(
                    handle,
                    int(result_address) + RESULT_GATE,
                    struct.pack("<I", 2),
                )
                if (
                    kernel32.WaitForSingleObject(thread, 5_000)
                    == WAIT_OBJECT_0
                ):
                    thread_finished = True
            finally:
                resume_process_threads(suspended)
            raise
        wait = kernel32.WaitForSingleObject(thread, 10_000)
        if wait == WAIT_TIMEOUT:
            status = read_u32(handle, int(result_address) + RESULT_STATUS)
            if status == 0x100:
                # The stub may be inside a multiword protected-value update.
                # Never release readers into that state. Stop the process so
                # its unchanged on-disk save remains authoritative.
                if not kernel32.TerminateProcess(handle, 0xE0010004):
                    error = ctypes.get_last_error()
                    for suspended_handle in suspended:
                        kernel32.CloseHandle(suspended_handle)
                    suspended.clear()
                    raise OSError(
                        error,
                        "TerminateProcess(timeout safety) failed; the game was "
                        "left frozen to protect its save. End it in Task Manager "
                        f"before relaunching: {ctypes.FormatError(error).strip()}",
                    )
                for suspended_handle in suspended:
                    kernel32.CloseHandle(suspended_handle)
                suspended.clear()
                raise TimeoutError(
                    "The native operation exceeded 10 seconds, so the game was "
                    "stopped before a partial in-memory value could be saved. "
                    "Relaunch it; the safety backup and original disk save remain."
                )
            # A terminal status means all mutation/rollback code completed and
            # only Windows thread teardown is delayed, so readers are safe.
            resume_process_threads(suspended)
            wait = kernel32.WaitForSingleObject(thread, 10_000)
        if wait != WAIT_OBJECT_0:
            if wait == WAIT_TIMEOUT:
                raise TimeoutError(
                    "The native forge thread did not finish. Remote pages were left "
                    "allocated because freeing executing code would be unsafe."
                )
            raise win_error("WaitForSingleObject")
        thread_finished = True
        resume_process_threads(suspended)

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
            raise win_error("GetExitCodeThread")
        values = struct.unpack(
            "<IIiiiiiiIIIII",
            read_memory(handle, int(result_address), RESULT_SIZE),
        )
        keys = (
            "status",
            "stage",
            "before_bananas",
            "before_tokens",
            "after_bananas",
            "after_tokens",
            "delta_bananas",
            "delta_tokens",
            "economy_mgr",
            "game_app",
            "flags_before",
            "flags_after",
            "gate",
        )
        result = dict(zip(keys, values, strict=True))
        if result["status"] == STALE_BALANCE_STATUS:
            raise RuntimeError(
                "The game balance changed while the operation was being prepared. "
                "Nothing was changed; refresh and try again."
            )
        if result["status"] != 1 or exit_code.value != 0:
            raise RuntimeError(
                f"Native forge failed at stage {result['stage']}: "
                f"status=0x{result['status']:08X}, "
                f"thread=0x{exit_code.value:08X}."
            )
        return result
    finally:
        cleanup_error: Exception | None = None
        try:
            resume_process_threads(suspended)
        except Exception as exc:
            cleanup_error = exc

        # If setup failed before gate=1, tell a started thread to exit without
        # running any game calls. This also covers readiness-poll exceptions.
        if thread and not gate_opened and not thread_finished:
            try:
                write_memory(
                    handle,
                    int(result_address) + RESULT_GATE,
                    struct.pack("<I", 2),
                )
                if (
                    kernel32.WaitForSingleObject(thread, 5_000)
                    == WAIT_OBJECT_0
                ):
                    thread_finished = True
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        if thread:
            kernel32.CloseHandle(thread)
        if not thread or thread_finished:
            kernel32.VirtualFreeEx(handle, result_address, 0, MEM_RELEASE)
            kernel32.VirtualFreeEx(handle, code_address, 0, MEM_RELEASE)
        if cleanup_error is not None:
            raise cleanup_error


def package_local_state() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not defined.")
    return Path(local_app_data) / "Packages" / PACKAGE_FAMILY / "LocalState"


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def backup_save() -> tuple[Path, Path, tuple[int, int, str | None]]:
    local_state = package_local_state()
    save_path = local_state / "savegame"
    if not save_path.is_file():
        raise RuntimeError(f"Save file not found: {save_path}")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not defined.")
    root = Path(local_app_data) / "MinionRushNativeMod" / "Backups"
    for _ in range(4):
        destination = root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination.mkdir(parents=True, exist_ok=False)
        first_stat = save_path.stat()
        first = (
            first_stat.st_mtime_ns,
            first_stat.st_size,
            file_sha256(save_path),
        )
        for name in ("savegame", "magic_file", "settings"):
            source = local_state / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        second_stat = save_path.stat()
        second = (
            second_stat.st_mtime_ns,
            second_stat.st_size,
            file_sha256(save_path),
        )
        copied_save = destination / "savegame"
        if (
            first == second
            and copied_save.is_file()
            and copied_save.stat().st_size == first[1]
            and file_sha256(copied_save) == first[2]
        ):
            return destination, save_path, first
        shutil.rmtree(destination, ignore_errors=True)
        time.sleep(0.05)
    raise RuntimeError(
        "The game kept writing its save during backup. Wait at the main menu "
        "and retry."
    )


def activate_game() -> None:
    explorer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
    try:
        subprocess.Popen(
            [str(explorer), rf"shell:AppsFolder\{AUMID}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def wait_for_save_change(
    save_path: Path,
    before: tuple[int, int, str | None],
    timeout: float,
) -> tuple[int, int, str | None]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            stat = save_path.stat()
            current = (stat.st_mtime_ns, stat.st_size, file_sha256(save_path))
        except (FileNotFoundError, PermissionError):
            continue
        if (
            before[2] is not None
            and current[2] is not None
            and current[2] != before[2]
        ):
            return current
    raise TimeoutError(
        "The in-memory values changed, but the save file did not change within "
        f"{timeout:.0f} seconds. Keep the game open at its main menu and retry."
    )


def _emit_progress(
    callback: Callable[[str], None] | None, message: str
) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        # A UI/logging callback must never interrupt a frozen-process operation.
        pass


def open_validated_game(
    *,
    write: bool,
    progress: Callable[[str], None] | None = None,
) -> tuple[wintypes.HANDLE, int, int, int, str, str]:
    """Open and fully validate the one supported game process/build."""

    _emit_progress(progress, "Looking for the running game...")
    pid = find_pid(GAME_EXE)
    handle = open_process(pid, write=write)
    try:
        validate_same_user(handle)
        validate_package_family(handle)
        module_base, module_size, module_path = find_module(pid, GAME_EXE)
        _emit_progress(progress, "Verifying the supported game build...")
        module_hash = file_sha256(Path(module_path))
        if module_hash != EXPECTED_EXE_SHA256:
            raise RuntimeError(
                "Unsupported executable SHA-256: "
                f"{module_hash or 'unreadable'}; expected "
                f"{EXPECTED_EXE_SHA256}. This app supports Minion Rush "
                "4.1.4.1 x86 only."
            )
        validate_game(handle, module_base, module_size)
        return (
            handle,
            pid,
            module_base,
            module_size,
            module_path,
            module_hash,
        )
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def inspect_game(
    progress: Callable[[str], None] | None = None,
) -> dict[str, int | str]:
    """Return a fresh, independently decoded snapshot for the GUI."""

    handle, pid, module_base, module_size, module_path, module_hash = (
        open_validated_game(write=False, progress=progress)
    )
    try:
        bananas, tokens = read_currencies(handle, module_base)
        validate_currency_value("Current banana balance", bananas)
        validate_currency_value("Current token balance", tokens)
    finally:
        kernel32.CloseHandle(handle)
    _emit_progress(progress, "Balances refreshed.")
    return {
        "pid": pid,
        "module_base": module_base,
        "module_size": module_size,
        "module_path": module_path,
        "module_sha256": module_hash,
        "bananas": bananas,
        "tokens": tokens,
    }


def apply_currency_additions(
    banana_add: int,
    token_add: int,
    *,
    save_timeout: float = 45.0,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Add two non-negative amounts using the game's native currency/save code."""

    validate_currency_value("Bananas to add", banana_add)
    validate_currency_value("Tokens to add", token_add)
    if banana_add == 0 and token_add == 0:
        raise ValueError("Enter an amount for bananas, tokens, or both.")
    if save_timeout <= 0:
        raise ValueError("Save timeout must be positive.")

    (
        handle,
        pid,
        module_base,
        _module_size,
        module_path,
        module_hash,
    ) = open_validated_game(write=True, progress=progress)
    backup_dir: Path | None = None
    save_path: Path | None = None
    save_before: tuple[int, int, str | None] | None = None
    native_result: dict[str, int] | None = None
    target_bananas = 0
    target_tokens = 0
    try:
        # These fresh values—not the balances previously shown by the GUI—are
        # authoritative for "amount to add" semantics.
        before_bananas, before_tokens = read_currencies(handle, module_base)
        validate_currency_value("Current banana balance", before_bananas)
        validate_currency_value("Current token balance", before_tokens)
        target_bananas = before_bananas + banana_add
        target_tokens = before_tokens + token_add
        validate_currency_value("Resulting banana balance", target_bananas)
        validate_currency_value("Resulting token balance", target_tokens)

        _emit_progress(progress, "Creating a safety backup...")
        backup_dir, save_path, save_before = backup_save()
        _emit_progress(
            progress, "Applying the change through the running game..."
        )
        try:
            native_result = execute_forge(
                handle,
                pid,
                module_base,
                before_bananas,
                before_tokens,
                target_bananas,
                target_tokens,
                save_path,
                save_before,
            )
        except Exception as exc:
            raise GameRestartRequired(
                "The native operation did not complete cleanly. Do not press "
                "Add again in this game session. Close and relaunch the game, "
                f"then refresh. Details: {exc}",
                pid=pid,
                backup_dir=backup_dir,
            ) from exc
        try:
            decoded_bananas, decoded_tokens = read_currencies(
                handle, module_base
            )
            if (decoded_bananas, decoded_tokens) != (
                target_bananas,
                target_tokens,
            ):
                raise RuntimeError(
                    f"Read {decoded_bananas}/{decoded_tokens}; expected "
                    f"{target_bananas}/{target_tokens}."
                )
        except Exception as exc:
            raise GameRestartRequired(
                "Independent protected-value verification was inconclusive. "
                "Do not press Add again in this game session. Close and "
                f"relaunch the game, then refresh. Details: {exc}",
                pid=pid,
                backup_dir=backup_dir,
            ) from exc
    finally:
        kernel32.CloseHandle(handle)

    if backup_dir is None or save_path is None or save_before is None:
        raise AssertionError("The native operation did not capture a save backup.")
    if native_result is None:
        raise AssertionError("The native operation returned no result.")

    # Bring the game window forward so its main loop continues servicing the
    # deferred native save request.
    activate_game()
    _emit_progress(progress, "Waiting for the game to write its save...")
    try:
        save_after = wait_for_save_change(
            save_path, save_before, save_timeout
        )
    except TimeoutError as exc:
        raise SaveConfirmationTimeout(
            "The new live balances were verified, but the game did not write "
            "a changed save in time. Do not press Add again. Close and relaunch "
            "the game, then refresh to check whether it persisted.",
            pid=pid,
            target_bananas=target_bananas,
            target_tokens=target_tokens,
            backup_dir=backup_dir,
        ) from exc
    except Exception as exc:
        raise GameRestartRequired(
            "The new live balances were verified, but save confirmation "
            "failed. Do not press Add again in this game session. Close and "
            f"relaunch the game, then refresh. Details: {exc}",
            pid=pid,
            backup_dir=backup_dir,
        ) from exc

    _emit_progress(progress, "Change saved and verified.")
    return {
        **native_result,
        "pid": pid,
        "module_path": module_path,
        "module_sha256": module_hash,
        "banana_add": banana_add,
        "token_add": token_add,
        "target_bananas": target_bananas,
        "target_tokens": target_tokens,
        "backup_dir": str(backup_dir),
        "save_path": str(save_path),
        "save_before_sha256": save_before[2],
        "save_after_sha256": save_after[2],
        "save_before_size": save_before[1],
        "save_after_size": save_after[1],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Trusted in-process currency forge for Minion Rush 4.1.4.1 x86. "
            "The game performs its own encoding and deferred save."
        )
    )
    parser.add_argument(
        "--target",
        type=int,
        default=99_999_999,
        help="Exact banana and token target (default: 99999999).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Report protected values without changing the game.",
    )
    parser.add_argument(
        "--save-timeout",
        type=float,
        default=45.0,
        help="Seconds to wait for the native save write (default: 45).",
    )
    args = parser.parse_args()
    validate_currency_value("--target", args.target)
    if args.save_timeout <= 0:
        raise ValueError("--save-timeout must be positive.")

    pid = find_pid(GAME_EXE)
    identity_handle = open_process(pid, write=False)
    try:
        validate_same_user(identity_handle)
        validate_package_family(identity_handle)
    finally:
        kernel32.CloseHandle(identity_handle)
    module_base, module_size, module_path = find_module(pid, GAME_EXE)
    print(f"Game PID: {pid}")
    print(f"Module: {module_path}")
    print(f"ASLR base: 0x{module_base:08X}")
    module_hash = file_sha256(Path(module_path))
    if module_hash != EXPECTED_EXE_SHA256:
        raise RuntimeError(
            "Unsupported executable SHA-256: "
            f"{module_hash or 'unreadable'}; expected {EXPECTED_EXE_SHA256}."
        )
    print(f"Executable SHA-256: {module_hash}")

    handle = open_process(pid, write=not args.read_only)
    try:
        validate_same_user(handle)
        validate_package_family(handle)
        validate_game(handle, module_base, module_size)
        bananas, tokens = read_currencies(handle, module_base)
        print(f"Current bananas: {bananas:,}")
        print(f"Current tokens:  {tokens:,}")
        if args.read_only:
            return 0

        backup_dir, save_path, save_before = backup_save()
        print(f"Backup: {backup_dir}")
        result = execute_forge(
            handle,
            pid,
            module_base,
            bananas,
            tokens,
            args.target,
            args.target,
            save_path,
            save_before,
        )
        decoded_bananas, decoded_tokens = read_currencies(handle, module_base)
    finally:
        kernel32.CloseHandle(handle)

    print(
        f"Native result: {result['before_bananas']:,}/{result['before_tokens']:,} "
        f"-> {result['after_bananas']:,}/{result['after_tokens']:,}"
    )
    if (decoded_bananas, decoded_tokens) != (args.target, args.target):
        raise RuntimeError(
            "Independent protected-value verification failed: "
            f"{decoded_bananas}/{decoded_tokens}."
        )
    print("Protected-value verification: OK")

    activate_game()
    save_after = wait_for_save_change(save_path, save_before, args.save_timeout)
    print(
        "Native save write: OK "
        f"(size {save_before[1]:,} -> {save_after[1]:,}, "
        f"SHA-256 {save_after[2]})"
    )
    print("Close and relaunch the game once to prove persistence.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
