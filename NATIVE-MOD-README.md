# Minion Rush native currency mod

This method does not copy an encrypted save from another PC. It attaches to
that PC's running game, calls the game's own protected currency functions, and
asks the normal game thread to serialize and save the result.

## Supported game

- Package: `GAMELOFTSA.DespicableMeMinionRush_4.1.4.1_x86`
- Executable: `DespicableMe_w8.exe`
- Required SHA-256:
  `759E0CFA785E03AD93199565D9AF852CBC1CA355BEFD223C4FD5686A817D3A13`

The script refuses to write if the package, Windows user, executable hash, PE
metadata, live code signatures, or save-ready state does not match.

## Simple Windows app

Use:

- `dist\MinionRushModder.exe`

The app has separate boxes for bananas and tokens. Each box is an amount to
add, not a final target. Leave either box empty to leave that currency
unchanged. It reads the live balances again when **Add to game** is clicked,
rejects totals above `999,999,999`, creates a safety backup, performs the
native change, and verifies that the encrypted save content changed.

Python is bundled inside the EXE, so the second PC does not need Python or any
CMD/PowerShell files. The other PC does need:

- The exact supported x86 game package installed.
- 64-bit Windows.
- The app and game running under the same interactive Windows account.

The old `apply-mod-portable.cmd`, `apply-mod-portable.ps1`, and
`payload\mod\savegame` are not used by this native method.

### Run the app

1. Launch Minion Rush normally from the Windows Start menu.
2. Wait until the game reaches its main menu.
3. Open `MinionRushModder.exe`.
4. Enter the bananas and/or tokens to add.
5. Click **Add to game** and wait for **Change saved and independently
   verified**.

The interface is fixed and non-resizable, with compact DPI-aware sizing and
no scrolling. The **About** tab contains the Someless Tricks brand, credits,
GitHub link, and supporter message.

The EXE is currently unsigned. Windows SmartScreen or antivirus software may
inspect or warn about it because it attaches to the running game. Do not
disable Windows security; code-sign the EXE before broad public distribution.

Tool by Someless Tricks  
GitHub by <https://github.com/boyoftime>

## Advanced command-line version

The source version remains available as `minion_native_mod.py` and requires
Python 3.10 or newer. Run:

```powershell
python .\minion_native_mod.py
```

The default target is `99,999,999` bananas and `99,999,999` tokens.

To use another exact target:

```powershell
python .\minion_native_mod.py --target 50000000
```

To inspect the protected values without changing anything:

```powershell
python .\minion_native_mod.py --read-only
```

Do not run the script from another admin account or a service. The script and
game must belong to the same interactive Windows user.

## Backups and verification

Before each write, the script makes a hash-verified backup in:

```text
%LOCALAPPDATA%\MinionRushNativeMod\Backups
```

It then verifies all of the following:

- The native getters report the requested in-memory values.
- An independent external decoder reports the same protected values.
- The encrypted save content receives a new SHA-256.

For the decisive persistence check, close and relaunch the game, then run the
script with `--read-only`.

This workflow was tested end to end on July 26, 2026. After a full process
restart, the game loaded `99,999,999` bananas and `99,999,999` tokens.
