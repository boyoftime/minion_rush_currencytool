from __future__ import annotations

import ctypes
import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Callable

import minion_native_mod as core


APP_TITLE = "Minion Rush Currency Tool"
WINDOW_SIZE = "600x610"
GITHUB_URL = "https://github.com/boyoftime"
ICON_RELATIVE_PATH = Path(
    "assets", "game", "Assets", "DespicableMe_310x310.png"
)
BRAND_RELATIVE_PATH = Path("assets", "SplashScreen.scale-200.png")

BG = "#F4F1E8"
CARD = "#FFFFFF"
NAVY = "#17324D"
MUTED = "#637083"
YELLOW = "#F7C948"
YELLOW_ACTIVE = "#E7B735"
GREEN = "#178A5B"
AMBER = "#A86500"
RED = "#B43A3A"
BORDER = "#DDE2E7"


def resource_path(relative_path: Path) -> Path:
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    )
    return bundle_root / relative_path


def parse_addition(raw: str, label: str) -> int:
    value_text = raw.strip()
    if not value_text:
        return 0
    plain = value_text.isascii() and value_text.isdigit()
    grouped = any(
        re.fullmatch(rf"[0-9]{{1,3}}(?:{separator}[0-9]{{3}})+", value_text)
        for separator in (",", "_", " ")
    )
    if not plain and not grouped:
        raise ValueError(
            f"{label} must be a non-negative whole number."
        )
    cleaned = value_text.replace(",", "").replace("_", "").replace(" ", "")
    value = int(cleaned, 10)
    if value > core.CURRENCY_MAX:
        raise ValueError(
            f"{label} cannot be more than {core.CURRENCY_MAX:,}."
        )
    return value


class MinionRushApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.app_icon: tk.PhotoImage | None = None
        try:
            self.app_icon = tk.PhotoImage(file=str(resource_path(ICON_RELATIVE_PATH)))
            self.root.iconphoto(True, self.app_icon)
        except (OSError, tk.TclError):
            pass

        self._prepare_responsive_scale()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.game_ready = False
        self.current_pid: int | None = None
        self.restart_required_pid: int | None = None

        self.status_text = tk.StringVar(value="Checking for the game...")
        self.bananas_text = tk.StringVar(value="—")
        self.tokens_text = tk.StringVar(value="—")
        self.banana_add = tk.StringVar()
        self.token_add = tk.StringVar()
        self.activity_text = tk.StringVar()
        self.activity_lines: list[str] = []

        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_events)
        self.root.after(250, self.refresh)

    def _prepare_responsive_scale(self) -> None:
        work_left, work_top, work_right, work_bottom = self._get_work_area()
        work_width = max(320, work_right - work_left)
        work_height = max(360, work_bottom - work_top)
        current_scale = float(self.root.tk.call("tk", "scaling"))
        width_cap = max(0.75, (work_width - 32) / 500)
        height_cap = max(0.75, (work_height - 64) / 480)
        target_scale = min(current_scale, width_cap, height_cap)
        if target_scale < current_scale - 0.01:
            self.root.tk.call("tk", "scaling", target_scale)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 9), foreground=NAVY)
        style.configure("Root.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(16, 6),
            font=("Segoe UI Semibold", 9),
        )
        style.layout(
            "TNotebook.Tab",
            [
                (
                    "Notebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [
                                        (
                                            "Notebook.label",
                                            {"side": "top", "sticky": ""},
                                        )
                                    ],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=NAVY,
            font=("Segoe UI Semibold", 19),
        )
        style.configure(
            "Subtitle.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "CardTitle.TLabel",
            background=CARD,
            foreground=NAVY,
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Muted.TLabel",
            background=CARD,
            foreground=MUTED,
            font=("Segoe UI", 8),
        )
        style.configure(
            "Balance.TLabel",
            background=CARD,
            foreground=NAVY,
            font=("Segoe UI Semibold", 18),
        )
        style.configure(
            "Field.TLabel",
            background=CARD,
            foreground=NAVY,
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "Secondary.TButton",
            padding=(11, 6),
            background="#EEF1F4",
            foreground=NAVY,
            bordercolor=BORDER,
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#E2E7EB"), ("disabled", "#F2F3F4")],
        )
        style.configure(
            "Primary.TButton",
            padding=(16, 8),
            background=YELLOW,
            foreground=NAVY,
            bordercolor=YELLOW,
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("active", YELLOW_ACTIVE),
                ("disabled", "#E4E0D5"),
            ],
            foreground=[("disabled", "#929292")],
        )
        style.configure(
            "Currency.TEntry",
            padding=(8, 6),
            fieldbackground="#FAFBFC",
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.configure(
            "Yellow.Horizontal.TProgressbar",
            troughcolor="#E7EBEF",
            background=YELLOW,
            bordercolor="#E7EBEF",
            lightcolor=YELLOW,
            darkcolor=YELLOW,
        )

    def _card(self, parent: tk.Misc) -> ttk.Frame:
        return ttk.Frame(parent, style="Card.TFrame", padding=10)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="Root.TFrame", padding=(20, 10))
        shell.pack(fill="both", expand=True)
        self.shell = shell

        ttk.Label(
            shell, text=APP_TITLE, style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            shell,
            text="Trusted in-process save  •  Minion Rush 4.1.4.1 x86",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(1, 7))

        self.notebook = ttk.Notebook(shell, takefocus=False)
        self.notebook.pack(fill="both", expand=True)
        currency_tab = ttk.Frame(
            self.notebook, style="Root.TFrame", padding=(10, 8)
        )
        about_tab = ttk.Frame(
            self.notebook, style="Root.TFrame", padding=(10, 8)
        )
        self.notebook.add(currency_tab, text="Currency")
        self.notebook.add(about_tab, text="About")

        status_card = self._card(currency_tab)
        status_card.pack(fill="x", pady=(0, 6))
        status_row = ttk.Frame(status_card, style="Card.TFrame")
        status_row.pack(fill="x")

        self.status_dot = tk.Label(
            status_row,
            text="●",
            font=("Segoe UI", 14),
            bg=CARD,
            fg=AMBER,
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_text,
            font=("Segoe UI Semibold", 9),
            bg=CARD,
            fg=NAVY,
            anchor="w",
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        self.launch_button = ttk.Button(
            status_row,
            text="Launch game",
            command=self.launch_game,
            style="Secondary.TButton",
        )
        self.launch_button.pack(side="right", padx=(8, 0))
        self.refresh_button = ttk.Button(
            status_row,
            text="Refresh",
            command=self.refresh,
            style="Secondary.TButton",
        )
        self.refresh_button.pack(side="right")

        balance_card = self._card(currency_tab)
        balance_card.pack(fill="x", pady=(0, 6))
        ttk.Label(
            balance_card, text="Current balance", style="CardTitle.TLabel"
        ).pack(anchor="w", pady=(0, 5))
        balance_row = ttk.Frame(balance_card, style="Card.TFrame")
        balance_row.pack(fill="x")
        balance_row.columnconfigure((0, 1), weight=1)

        banana_box = ttk.Frame(balance_row, style="Card.TFrame")
        banana_box.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            banana_box, text="BANANAS", style="Muted.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            banana_box,
            textvariable=self.bananas_text,
            style="Balance.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        token_box = ttk.Frame(balance_row, style="Card.TFrame")
        token_box.grid(row=0, column=1, sticky="ew", padx=(24, 0))
        ttk.Label(
            token_box, text="TOKENS", style="Muted.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            token_box,
            textvariable=self.tokens_text,
            style="Balance.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        add_card = self._card(currency_tab)
        add_card.pack(fill="x", pady=(0, 6))
        ttk.Label(
            add_card, text="Amount to add", style="CardTitle.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            add_card,
            text="Leave either box empty to keep that balance unchanged.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(1, 7))

        fields = ttk.Frame(add_card, style="Card.TFrame")
        fields.pack(fill="x")
        fields.columnconfigure((0, 1), weight=1)

        banana_field = ttk.Frame(fields, style="Card.TFrame")
        banana_field.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(
            banana_field, text="Bananas to add", style="Field.TLabel"
        ).pack(anchor="w", pady=(0, 5))
        self.banana_entry = ttk.Entry(
            banana_field,
            textvariable=self.banana_add,
            style="Currency.TEntry",
            font=("Segoe UI", 10),
        )
        self.banana_entry.pack(fill="x")

        token_field = ttk.Frame(fields, style="Card.TFrame")
        token_field.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(
            token_field, text="Tokens to add", style="Field.TLabel"
        ).pack(anchor="w", pady=(0, 5))
        self.token_entry = ttk.Entry(
            token_field,
            textvariable=self.token_add,
            style="Currency.TEntry",
            font=("Segoe UI", 10),
        )
        self.token_entry.pack(fill="x")

        self.apply_button = ttk.Button(
            add_card,
            text="Add to game",
            command=self.apply,
            style="Primary.TButton",
        )
        self.apply_button.pack(fill="x", pady=(10, 0))

        self.progress = ttk.Progressbar(
            currency_tab,
            mode="indeterminate",
            style="Yellow.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(1, 5))

        activity = tk.Label(
            currency_tab,
            textvariable=self.activity_text,
            height=2,
            wraplength=530,
            justify="left",
            anchor="nw",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
            cursor="arrow",
            padx=0,
            pady=0,
        )
        activity.pack(fill="x")
        self._log("Ready. Launch the game and wait at its main menu.")

        footer = ttk.Label(
            currency_tab,
            text=(
                "A timestamped save backup is created before every change. "
                f"Maximum balance: {core.CURRENCY_MAX:,}."
            ),
            style="Subtitle.TLabel",
        )
        footer.pack(anchor="w", pady=(2, 0))

        self._build_about_tab(about_tab)
        self.root.after_idle(self._fit_window_to_content)
        self.root.bind("<Return>", lambda _event: self.apply())
        self._update_controls()

    def _build_about_tab(self, parent: ttk.Frame) -> None:
        about = tk.Frame(parent, bg=BG)
        about.pack(fill="both", expand=True)

        self.brand_source: tk.PhotoImage | None = None
        self.brand_image: tk.PhotoImage | None = None
        try:
            self.brand_source = tk.PhotoImage(
                file=str(resource_path(BRAND_RELATIVE_PATH))
            )
            work_left, work_top, work_right, work_bottom = (
                self._get_work_area()
            )
            work_width = work_right - work_left
            work_height = work_bottom - work_top
            maximum_logo_width = max(180, min(500, work_width - 100))
            maximum_logo_height = max(100, min(240, work_height - 330))
            width_divisor = (
                self.brand_source.width() + maximum_logo_width - 1
            ) // maximum_logo_width
            height_divisor = (
                self.brand_source.height() + maximum_logo_height - 1
            ) // maximum_logo_height
            self.brand_divisor = max(
                4 if work_height < 680 else 3,
                width_divisor,
                height_divisor,
            )
            self.brand_image = self.brand_source.subsample(
                self.brand_divisor, self.brand_divisor
            )
            self.brand_label = tk.Label(
                about,
                image=self.brand_image,
                bg=BG,
                borderwidth=0,
            )
            self.brand_label.pack(pady=(5, 5))
        except (OSError, tk.TclError):
            tk.Label(
                about,
                text="Someless Tricks",
                bg=BG,
                fg=NAVY,
                font=("Segoe UI Semibold", 22),
            ).pack(pady=(20, 10))

        tk.Label(
            about,
            text="Tool by Someless Tricks",
            bg=BG,
            fg=NAVY,
            font=("Segoe UI Semibold", 12),
        ).pack()
        github = tk.Label(
            about,
            text=f"GitHub by {GITHUB_URL}",
            bg=BG,
            fg="#1967B3",
            activeforeground="#0B4F8A",
            font=("Segoe UI", 9, "underline"),
            cursor="hand2",
        )
        github.pack(pady=(3, 12))
        github.bind("<Button-1>", self._open_github)

        tk.Label(
            about,
            text="Made with love ♥",
            bg=BG,
            fg="#C9472D",
            font=("Segoe UI Semibold", 12),
        ).pack(pady=(3, 4))
        tk.Label(
            about,
            text=(
                "For all my fans and everyone who supports me.\n"
                "Thank you for being part of the Someless Tricks journey."
            ),
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="center",
            wraplength=max(240, min(520, self.root.winfo_screenwidth() - 80)),
        ).pack()

    def _get_work_area(self) -> tuple[int, int, int, int]:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        result = (0, 0, screen_width, screen_height)
        try:
            class WorkArea(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            area = WorkArea()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(area), 0
            ):
                result = (
                    int(area.left),
                    int(area.top),
                    int(area.right),
                    int(area.bottom),
                )
        except (AttributeError, OSError):
            pass
        return result

    def _fit_window_to_content(self) -> None:
        self.root.update_idletasks()
        work_left, work_top, work_right, work_bottom = self._get_work_area()
        maximum_width = max(320, work_right - work_left - 32)
        maximum_height = max(360, work_bottom - work_top - 64)

        width = min(max(600, self.shell.winfo_reqwidth()), maximum_width)
        height = min(max(560, self.shell.winfo_reqheight()), maximum_height)
        left = work_left + max(0, (work_right - work_left - width) // 2)
        top = work_top + max(0, (work_bottom - work_top - height) // 2)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.deiconify()

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.activity_lines.append(f"{stamp}  {message}")
        self.activity_lines = self.activity_lines[-2:]
        self.activity_text.set("\n".join(self.activity_lines))

    def _open_github(self, _event: tk.Event[tk.Misc]) -> None:
        webbrowser.open_new_tab(GITHUB_URL)

    def _set_status(self, message: str, color: str) -> None:
        self.status_text.set(message)
        self.status_dot.configure(fg=color)

    def _update_controls(self) -> None:
        general_state = "disabled" if self.busy else "normal"
        self.launch_button.configure(state=general_state)
        self.refresh_button.configure(state=general_state)
        entry_state = "disabled" if self.busy else "normal"
        self.banana_entry.configure(state=entry_state)
        self.token_entry.configure(state=entry_state)
        can_apply = (
            not self.busy
            and self.game_ready
            and self.restart_required_pid is None
        )
        self.apply_button.configure(state="normal" if can_apply else "disabled")

    def _start_task(
        self,
        task_name: str,
        worker: Callable[[], object],
    ) -> None:
        if self.busy:
            return
        self.busy = True
        self._update_controls()
        self.progress.start(12)

        def run() -> None:
            try:
                result = worker()
            except Exception as exc:
                self.events.put(("error", (task_name, exc)))
            else:
                self.events.put(("success", (task_name, result)))
            finally:
                self.events.put(("finished", task_name))

        self.worker = threading.Thread(
            target=run,
            name=f"minion-{task_name}",
            daemon=False,
        )
        self.worker.start()

    def _progress_message(self, message: str) -> None:
        self.events.put(("progress", message))

    def refresh(self) -> None:
        if self.busy:
            return
        self._set_status("Checking the running game...", AMBER)
        self._start_task(
            "refresh",
            lambda: core.inspect_game(progress=self._progress_message),
        )

    def launch_game(self) -> None:
        if self.busy:
            return
        self._set_status("Launching Minion Rush...", AMBER)
        self._log("Launch requested.")

        def launch_and_wait() -> dict[str, int | str]:
            core.activate_game()
            deadline = time.monotonic() + 30.0
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                time.sleep(0.75)
                try:
                    return core.inspect_game(progress=self._progress_message)
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                raise RuntimeError(
                    "The game did not become ready within 30 seconds. "
                    f"Last check: {last_error}"
                ) from last_error
            raise RuntimeError("The game did not become ready within 30 seconds.")

        self._start_task("launch", launch_and_wait)

    def apply(self) -> None:
        if self.busy or not self.game_ready:
            return
        if self.restart_required_pid is not None:
            self._set_status("Restart the game before another change.", RED)
            return
        try:
            banana_add = parse_addition(
                self.banana_add.get(), "Bananas to add"
            )
            token_add = parse_addition(
                self.token_add.get(), "Tokens to add"
            )
            if banana_add == 0 and token_add == 0:
                raise ValueError("Enter an amount for bananas, tokens, or both.")
        except ValueError as exc:
            self._set_status(str(exc), RED)
            self._log(str(exc))
            self.root.bell()
            return

        self._set_status("Preparing the verified change...", AMBER)
        self._log(
            f"Requested addition: bananas +{banana_add:,}, "
            f"tokens +{token_add:,}."
        )
        self._start_task(
            "apply",
            lambda: core.apply_currency_additions(
                banana_add,
                token_add,
                progress=self._progress_message,
            ),
        )

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    message = str(payload)
                    self._set_status(message, AMBER)
                elif event == "success":
                    task_name, result = payload  # type: ignore[misc]
                    self._handle_success(str(task_name), result)
                elif event == "error":
                    task_name, exc = payload  # type: ignore[misc]
                    self._handle_error(str(task_name), exc)
                elif event == "finished":
                    self.busy = False
                    self.progress.stop()
                    self._update_controls()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_events)

    def _handle_success(self, task_name: str, result: object) -> None:
        if not isinstance(result, dict):
            self._set_status("Unexpected internal result.", RED)
            return

        if task_name in {"refresh", "launch"}:
            bananas = int(result["bananas"])
            tokens = int(result["tokens"])
            pid = int(result["pid"])
            self.current_pid = pid
            self.bananas_text.set(f"{bananas:,}")
            self.tokens_text.set(f"{tokens:,}")
            self.game_ready = True
            if (
                self.restart_required_pid is not None
                and pid != self.restart_required_pid
            ):
                self.restart_required_pid = None
                self._log("A new game process was verified; additions are enabled.")
            if self.restart_required_pid is None:
                self._set_status(f"Game ready  •  PID {pid}", GREEN)
                self._log(
                    f"Balances refreshed: {bananas:,} bananas, "
                    f"{tokens:,} tokens."
                )
            else:
                self._set_status(
                    "Close and relaunch the game before adding again.", RED
                )
            return

        if task_name == "apply":
            before_bananas = int(result["before_bananas"])
            before_tokens = int(result["before_tokens"])
            after_bananas = int(result["after_bananas"])
            after_tokens = int(result["after_tokens"])
            banana_add = int(result["banana_add"])
            token_add = int(result["token_add"])
            backup_dir = str(result["backup_dir"])
            self.bananas_text.set(f"{after_bananas:,}")
            self.tokens_text.set(f"{after_tokens:,}")
            self.current_pid = int(result["pid"])
            self.banana_add.set("")
            self.token_add.set("")
            self.game_ready = True
            self._set_status("Change saved and independently verified.", GREEN)
            self._log(
                f"Saved: bananas +{banana_add:,} "
                f"({before_bananas:,} → {after_bananas:,}); "
                f"tokens +{token_add:,} "
                f"({before_tokens:,} → {after_tokens:,})."
            )
            self._log(f"Safety backup: {backup_dir}")

    def _handle_error(self, task_name: str, exc: object) -> None:
        message = str(exc)
        if isinstance(
            exc, (core.SaveConfirmationTimeout, core.GameRestartRequired)
        ):
            self.restart_required_pid = exc.pid
            self.game_ready = False
            self._set_status(
                "Save not confirmed—close and relaunch the game.", RED
            )
            self._log(message)
            self._log(f"Safety backup: {exc.backup_dir}")
            self.root.bell()
            return

        lowered = message.casefold()
        if task_name == "apply":
            # Even known preflight failures require one fresh read before the
            # same inputs can be applied again.
            self.game_ready = False
        if "is not running" in lowered:
            self.game_ready = False
            self.current_pid = None
            self.bananas_text.set("—")
            self.tokens_text.set("—")
            self._set_status("Game not running", AMBER)
        elif "not save-ready" in lowered or "not initialized" in lowered:
            self.game_ready = False
            self._set_status("Game is not ready—wait at the main menu.", AMBER)
        elif "unsupported" in lowered or "signature mismatch" in lowered:
            self.game_ready = False
            self._set_status("Unsupported game build", RED)
        else:
            if task_name in {"refresh", "launch"}:
                self.game_ready = False
            self._set_status("Operation failed—see details below.", RED)
        self._log(message)
        if task_name == "apply":
            self.root.bell()

    def _on_close(self) -> None:
        if self.busy:
            self._set_status(
                "Please wait for the current safety operation to finish.", RED
            )
            self._log("Close was postponed while an operation is active.")
            self.root.bell()
            return
        self.root.destroy()


def enable_high_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main() -> None:
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    MinionRushApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
