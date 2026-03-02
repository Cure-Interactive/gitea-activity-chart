#!/usr/bin/env python3
# =============================================================================
# [Python Script] [CustomTkinter GUI] [Gitea Activity Chart]
# =============================================================================
"""
Fetch Gitea contribution activity and render a trend chart plus contribution heatmap.

Notes:
- First run creates config.json from config_default.json.
- Token may be stored in config.json, but using token_env is preferred.
- The app resolves the current user automatically if username is blank.
- Daily activity is gathered from the Gitea activity feeds endpoint and then
  grouped by day, week, or month for charting.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, messagebox
from typing import Any

try:
  import customtkinter as ctk
except Exception as e:
  raise SystemExit(
    "\n".join([
      "Missing dependency: customtkinter",
      "",
      "Install:",
      "  pip install customtkinter",
      "",
      f"Original error: {e}",
    ])
  )

try:
  import requests
except Exception as e:
  raise SystemExit(
    "\n".join([
      "Missing dependency: requests",
      "",
      "Install:",
      "  pip install requests",
      "",
      f"Original error: {e}",
    ])
  )


APP_TITLE = "Gitea Activity Chart - Cure Interactive"
APP_USER_MODEL_ID = "CureInteractive.GiteaActivityChart"

PATH_DIR_SCRIPT = os.path.abspath(os.path.dirname(__file__))
PATH_CONFIG_JSON = os.path.join(PATH_DIR_SCRIPT, "config.json")
PATH_CONFIG_DEFAULT_JSON = os.path.join(PATH_DIR_SCRIPT, "config_default.json")

DEFAULT_CONFIG: dict[str, Any] = {
  "window": {
    "width": 1180,
    "height": 900,
  },
  "appearance_mode": "System",
  "color_theme": "blue",
  "gitea": {
    "base_url": "https://git.example.com",
    "api_base_path": "/api/v1",
    "token": "",
    "token_env": "GITEA_TOKEN",
    "username": "",
    "verify_tls": True,
    "timeout_s": 30,
    "page_limit": 50,
    "user_agent": "gitea-activity-chart/1.0",
  },
  "query": {
    "days_back": 180,
    "group_by": "day",
    "rolling_average_days": 7,
    "only_performed_by": True,
    "include_today": True,
  },
}


def _read_json(path: str) -> dict[str, Any] | None:
  try:
    if not os.path.isfile(path):
      return None
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except Exception:
    return None


def _write_json_atomic(path: str, data: dict[str, Any]) -> None:
  tmp = path + ".tmp"
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
  os.replace(tmp, path)


def _deep_copy_json_dict(data: dict[str, Any]) -> dict[str, Any]:
  return json.loads(json.dumps(data))


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
  out = _deep_copy_json_dict(base)
  for key, value in overlay.items():
    if isinstance(value, dict) and isinstance(out.get(key), dict):
      out[key] = _deep_merge_dict(out[key], value)
    else:
      out[key] = value
  return out


def load_or_create_config() -> dict[str, Any]:
  template = _read_json(PATH_CONFIG_DEFAULT_JSON)
  if not isinstance(template, dict):
    template = _deep_copy_json_dict(DEFAULT_CONFIG)
  user_cfg = _read_json(PATH_CONFIG_JSON)
  if isinstance(user_cfg, dict):
    return _deep_merge_dict(template, user_cfg)
  _write_json_atomic(PATH_CONFIG_JSON, template)
  return _deep_copy_json_dict(template)


def set_windows_app_user_model_id(app_id: str) -> None:
  try:
    if os.name != "nt":
      return
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
  except Exception:
    return


def set_window_icon(root, ico_path: str, png_path: str) -> None:
  ico_abs = os.path.abspath(ico_path) if ico_path else ""
  png_abs = os.path.abspath(png_path) if png_path else ""
  try:
    if ico_abs and os.path.isfile(ico_abs):
      root.iconbitmap(ico_abs)
  except Exception:
    pass
  try:
    if png_abs and os.path.isfile(png_abs):
      img = tk.PhotoImage(file=png_abs)
      root.iconphoto(True, img)
      root._iconphoto_ref = img
  except Exception:
    pass


def _safe_int(value: Any, default: int) -> int:
  try:
    return int(str(value).strip())
  except Exception:
    return default


def _format_number(value: int | float) -> str:
  if isinstance(value, float) and not value.is_integer():
    return f"{value:,.2f}"
  return f"{int(value):,}"


def _iso_date(value: dt.date) -> str:
  return value.isoformat()


def _daterange(start: dt.date, end: dt.date) -> list[dt.date]:
  days = (end - start).days
  return [start + dt.timedelta(days=i) for i in range(days + 1)]


def _week_start(value: dt.date) -> dt.date:
  return value - dt.timedelta(days=value.weekday())


def _month_start(value: dt.date) -> dt.date:
  return value.replace(day=1)


def _roll_average(values: list[int], window_size: int) -> list[float]:
  if window_size <= 1:
    return [float(v) for v in values]
  out: list[float] = []
  running_sum = 0.0
  window: list[int] = []
  for value in values:
    running_sum += value
    window.append(value)
    if len(window) > window_size:
      running_sum -= window.pop(0)
    out.append(running_sum / len(window))
  return out


class GiteaClient:
  def __init__(
    self,
    *,
    base_url: str,
    api_base_path: str,
    token: str,
    verify_tls: bool,
    timeout_s: int,
    user_agent: str,
  ) -> None:
    self._base_url = base_url.rstrip("/")
    self._api_base_path = api_base_path.rstrip("/")
    self._token = token
    self._verify_tls = verify_tls
    self._timeout_s = timeout_s
    self._user_agent = user_agent

  def _api_url(self, path: str) -> str:
    return f"{self._base_url}{self._api_base_path}/{path.lstrip('/')}"

  def _headers(self) -> dict[str, str]:
    return {
      "Accept": "application/json",
      "Authorization": f"token {self._token}",
      "User-Agent": self._user_agent,
    }

  def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
      self._api_url(path),
      headers=self._headers(),
      params=params,
      timeout=self._timeout_s,
      verify=self._verify_tls,
    )
    if response.status_code >= 400:
      raise RuntimeError(f"Gitea API failed: {response.status_code} {response.text}")
    return response.json()

  def get_current_user(self) -> dict[str, Any]:
    data = self._get_json("/user")
    if not isinstance(data, dict):
      raise RuntimeError("Unexpected /user response payload.")
    return data

  def count_user_activity_for_day(
    self,
    *,
    username: str,
    day: dt.date,
    page_limit: int,
    only_performed_by: bool,
  ) -> int:
    page = 1
    total = 0
    params_base = {
      "date": _iso_date(day),
      "limit": max(1, page_limit),
      "only-performed-by": "true" if only_performed_by else "false",
    }
    while True:
      params = dict(params_base)
      params["page"] = page
      data = self._get_json(f"/users/{username}/activities/feeds", params=params)
      if not isinstance(data, list):
        raise RuntimeError("Unexpected activity feed response payload.")
      batch_len = len(data)
      total += batch_len
      if batch_len < page_limit:
        break
      page += 1
    return total


class GiteaActivityChartApp(ctk.CTk):
  def __init__(self) -> None:
    super().__init__()

    self.config_data = load_or_create_config()

    ctk.set_appearance_mode(str(self.config_data.get("appearance_mode", "System")))
    ctk.set_default_color_theme(str(self.config_data.get("color_theme", "blue")))

    w = int(self.config_data.get("window", {}).get("width", 1180))
    h = int(self.config_data.get("window", {}).get("height", 900))

    self.title(APP_TITLE)
    self.geometry(f"{w}x{h}")
    self.minsize(1040, 760)

    set_window_icon(
      self,
      os.path.join(PATH_DIR_SCRIPT, "icon.ico"),
      os.path.join(PATH_DIR_SCRIPT, "icon.png"),
    )

    self._worker_thread: threading.Thread | None = None
    self._busy = False
    self._last_results: list[tuple[dt.date, int]] = []
    self._last_grouped: OrderedDict[str, int] = OrderedDict()
    self._last_username = ""

    gitea_cfg = self.config_data.get("gitea", {})
    query_cfg = self.config_data.get("query", {})
    if not isinstance(gitea_cfg, dict):
      gitea_cfg = {}
    if not isinstance(query_cfg, dict):
      query_cfg = {}

    self.var_base_url = tk.StringVar(value=str(gitea_cfg.get("base_url", "https://git.example.com")))
    self.var_api_base_path = tk.StringVar(value=str(gitea_cfg.get("api_base_path", "/api/v1")))
    self.var_token = tk.StringVar(value=str(gitea_cfg.get("token", "")))
    self.var_token_env = tk.StringVar(value=str(gitea_cfg.get("token_env", "GITEA_TOKEN")))
    self.var_username = tk.StringVar(value=str(gitea_cfg.get("username", "")))
    self.var_verify_tls = tk.BooleanVar(value=bool(gitea_cfg.get("verify_tls", True)))
    self.var_timeout_s = tk.StringVar(value=str(gitea_cfg.get("timeout_s", 30)))
    self.var_page_limit = tk.StringVar(value=str(gitea_cfg.get("page_limit", 50)))
    self.var_user_agent = tk.StringVar(value=str(gitea_cfg.get("user_agent", "gitea-activity-chart/1.0")))
    self.var_days_back = tk.StringVar(value=str(query_cfg.get("days_back", 180)))
    self.var_group_by = tk.StringVar(value=str(query_cfg.get("group_by", "day")))
    self.var_rolling_average_days = tk.StringVar(value=str(query_cfg.get("rolling_average_days", 7)))
    self.var_only_performed_by = tk.BooleanVar(value=bool(query_cfg.get("only_performed_by", True)))
    self.var_include_today = tk.BooleanVar(value=bool(query_cfg.get("include_today", True)))

    self._build_ui()
    self.protocol("WM_DELETE_WINDOW", self._on_close)
    self._draw_empty_states()

  def _build_ui(self) -> None:
    self.grid_columnconfigure(0, weight=1)
    self.grid_rowconfigure(2, weight=1)
    self.grid_rowconfigure(3, weight=1)

    top = ctk.CTkFrame(self)
    top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
    for col in range(8):
      top.grid_columnconfigure(col, weight=1 if col in (1, 3, 5, 7) else 0)

    ctk.CTkLabel(top, text="Base URL").grid(row=0, column=0, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_base_url).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(top, text="API Path").grid(row=0, column=2, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_api_base_path).grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(top, text="Username").grid(row=0, column=4, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_username).grid(row=0, column=5, sticky="ew", padx=(0, 6), pady=8)
    ctk.CTkButton(top, text="Resolve User", width=120, command=self._on_resolve_user).grid(row=0, column=6, padx=(0, 10), pady=8)

    ctk.CTkLabel(top, text="Token").grid(row=1, column=0, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_token, show="*").grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(top, text="Token Env").grid(row=1, column=2, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_token_env).grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(top, text="Timeout").grid(row=1, column=4, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(top, textvariable=self.var_timeout_s).grid(row=1, column=5, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkCheckBox(top, text="Verify TLS", variable=self.var_verify_tls).grid(row=1, column=6, sticky="w", padx=(0, 10), pady=8)

    options = ctk.CTkFrame(self)
    options.grid(row=1, column=0, sticky="ew", padx=12, pady=8)
    for col in range(8):
      options.grid_columnconfigure(col, weight=1)

    ctk.CTkLabel(options, text="Days Back").grid(row=0, column=0, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(options, textvariable=self.var_days_back).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(options, text="Group By").grid(row=0, column=2, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkOptionMenu(options, variable=self.var_group_by, values=["day", "week", "month"], command=lambda _value: self._redraw_from_last_results()).grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(options, text="Rolling Avg").grid(row=0, column=4, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(options, textvariable=self.var_rolling_average_days).grid(row=0, column=5, sticky="ew", padx=(0, 10), pady=8)
    ctk.CTkLabel(options, text="Page Limit").grid(row=0, column=6, sticky="w", padx=(10, 6), pady=8)
    ctk.CTkEntry(options, textvariable=self.var_page_limit).grid(row=0, column=7, sticky="ew", padx=(0, 10), pady=8)

    ctk.CTkCheckBox(options, text="Only performed by this user", variable=self.var_only_performed_by).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10))
    ctk.CTkCheckBox(options, text="Include today", variable=self.var_include_today).grid(row=1, column=2, columnspan=2, sticky="w", padx=10, pady=(0, 10))

    summary = ctk.CTkFrame(self)
    summary.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
    summary.grid_columnconfigure(0, weight=2)
    summary.grid_columnconfigure(1, weight=1)
    summary.grid_rowconfigure(0, weight=1)

    chart_panel = ctk.CTkFrame(summary)
    chart_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
    chart_panel.grid_columnconfigure(0, weight=1)
    chart_panel.grid_rowconfigure(1, weight=1)

    chart_title_row = ctk.CTkFrame(chart_panel, fg_color="transparent")
    chart_title_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
    chart_title_row.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(chart_title_row, text="Trend Chart", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
    self.lbl_chart_subtitle = ctk.CTkLabel(chart_title_row, text="No data loaded")
    self.lbl_chart_subtitle.grid(row=0, column=1, sticky="e")

    self.canvas_chart = tk.Canvas(chart_panel, highlightthickness=0, bg="#0f172a")
    self.canvas_chart.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
    self.canvas_chart.bind("<Configure>", lambda _e: self._draw_line_chart())

    side = ctk.CTkFrame(summary)
    side.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
    side.grid_columnconfigure(0, weight=1)
    side.grid_rowconfigure(1, weight=1)

    metrics = ctk.CTkFrame(side)
    metrics.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
    metrics.grid_columnconfigure(0, weight=1)
    metrics.grid_columnconfigure(1, weight=1)

    self.lbl_total = self._metric(metrics, 0, 0, "Total", "0")
    self.lbl_peak = self._metric(metrics, 0, 1, "Peak", "0")
    self.lbl_avg = self._metric(metrics, 1, 0, "Average", "0")
    self.lbl_streak = self._metric(metrics, 1, 1, "Best Streak", "0")

    heatmap_frame = ctk.CTkFrame(side)
    heatmap_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
    heatmap_frame.grid_columnconfigure(0, weight=1)
    heatmap_frame.grid_rowconfigure(1, weight=1)
    ctk.CTkLabel(heatmap_frame, text="Contribution Heatmap", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))
    self.canvas_heatmap = tk.Canvas(heatmap_frame, highlightthickness=0, bg="#0f172a")
    self.canvas_heatmap.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
    self.canvas_heatmap.bind("<Configure>", lambda _e: self._draw_heatmap())

    bottom = ctk.CTkFrame(self)
    bottom.grid(row=3, column=0, sticky="nsew", padx=12, pady=(8, 12))
    bottom.grid_columnconfigure(0, weight=1)
    bottom.grid_rowconfigure(1, weight=1)

    actions = ctk.CTkFrame(bottom)
    actions.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
    actions.grid_columnconfigure(8, weight=1)

    self.btn_fetch = ctk.CTkButton(actions, text="Fetch Activity", command=self._on_fetch_activity)
    self.btn_fetch.grid(row=0, column=0, padx=(0, 8), pady=8)
    ctk.CTkButton(actions, text="Save Config", command=self._on_save).grid(row=0, column=1, padx=(0, 8), pady=8)
    ctk.CTkButton(actions, text="Export CSV", command=self._on_export_csv).grid(row=0, column=2, padx=(0, 8), pady=8)
    ctk.CTkButton(actions, text="Clear Data", command=self._clear_results).grid(row=0, column=3, padx=(0, 8), pady=8)
    self.progress = ctk.CTkProgressBar(actions)
    self.progress.grid(row=0, column=4, sticky="ew", padx=(10, 10), pady=8)
    self.progress.set(0)
    self.lbl_status = ctk.CTkLabel(actions, text="Idle")
    self.lbl_status.grid(row=0, column=8, sticky="e", padx=(8, 0), pady=8)

    self.text_log = ctk.CTkTextbox(bottom)
    self.text_log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
    self.text_log.configure(state="disabled")

  def _metric(self, parent, row: int, col: int, title: str, value: str):
    card = ctk.CTkFrame(parent)
    card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
    card.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(card, text=title).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
    lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=22, weight="bold"))
    lbl.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
    return lbl

  def _log(self, msg: str) -> None:
    line = str(msg).rstrip()
    if not line:
      return
    self.text_log.configure(state="normal")
    self.text_log.insert("end", line + "\n")
    self.text_log.see("end")
    self.text_log.configure(state="disabled")

  def _set_status(self, text: str, progress: float | None = None) -> None:
    self.lbl_status.configure(text=text)
    if progress is not None:
      self.progress.set(max(0.0, min(1.0, progress)))

  def _collect_config_from_ui(self) -> dict[str, Any]:
    cfg = _deep_copy_json_dict(DEFAULT_CONFIG)
    cfg["window"]["width"] = max(self.winfo_width(), 1040)
    cfg["window"]["height"] = max(self.winfo_height(), 760)
    cfg["appearance_mode"] = str(self.config_data.get("appearance_mode", "System"))
    cfg["color_theme"] = str(self.config_data.get("color_theme", "blue"))
    cfg["gitea"]["base_url"] = self.var_base_url.get().strip()
    cfg["gitea"]["api_base_path"] = self.var_api_base_path.get().strip() or "/api/v1"
    cfg["gitea"]["token"] = self.var_token.get().strip()
    cfg["gitea"]["token_env"] = self.var_token_env.get().strip() or "GITEA_TOKEN"
    cfg["gitea"]["username"] = self.var_username.get().strip()
    cfg["gitea"]["verify_tls"] = bool(self.var_verify_tls.get())
    cfg["gitea"]["timeout_s"] = max(1, _safe_int(self.var_timeout_s.get(), 30))
    cfg["gitea"]["page_limit"] = max(1, min(100, _safe_int(self.var_page_limit.get(), 50)))
    cfg["gitea"]["user_agent"] = self.var_user_agent.get().strip() or "gitea-activity-chart/1.0"
    cfg["query"]["days_back"] = max(1, min(3660, _safe_int(self.var_days_back.get(), 180)))
    cfg["query"]["group_by"] = self.var_group_by.get().strip() or "day"
    cfg["query"]["rolling_average_days"] = max(1, min(365, _safe_int(self.var_rolling_average_days.get(), 7)))
    cfg["query"]["only_performed_by"] = bool(self.var_only_performed_by.get())
    cfg["query"]["include_today"] = bool(self.var_include_today.get())
    return cfg

  def _save_config(self) -> None:
    self.config_data = self._collect_config_from_ui()
    _write_json_atomic(PATH_CONFIG_JSON, self.config_data)

  def _on_save(self) -> None:
    try:
      self._save_config()
      self._log(f"Saved config: {PATH_CONFIG_JSON}")
    except Exception as e:
      messagebox.showerror(APP_TITLE, f"Failed to save config:\n{e}")

  def _resolve_token(self) -> str:
    inline = self.var_token.get().strip()
    if inline:
      return inline
    token_env = self.var_token_env.get().strip()
    if token_env:
      env_value = os.environ.get(token_env, "").strip()
      if env_value:
        return env_value
    return ""

  def _build_client(self) -> GiteaClient:
    token = self._resolve_token()
    if not token:
      raise ValueError("Provide a Gitea token or set the configured token environment variable.")
    base_url = self.var_base_url.get().strip()
    if not base_url:
      raise ValueError("Base URL is required.")
    return GiteaClient(
      base_url=base_url,
      api_base_path=self.var_api_base_path.get().strip() or "/api/v1",
      token=token,
      verify_tls=bool(self.var_verify_tls.get()),
      timeout_s=max(1, _safe_int(self.var_timeout_s.get(), 30)),
      user_agent=self.var_user_agent.get().strip() or "gitea-activity-chart/1.0",
    )

  def _set_busy(self, busy: bool) -> None:
    self._busy = busy
    self.btn_fetch.configure(state="disabled" if busy else "normal")

  def _on_resolve_user(self) -> None:
    if self._busy:
      return
    try:
      client = self._build_client()
      self._set_busy(True)
      self._set_status("Resolving user...", 0.05)
    except Exception as e:
      messagebox.showerror(APP_TITLE, str(e))
      return

    def worker() -> None:
      try:
        user = client.get_current_user()
        username = str(user.get("login") or user.get("username") or "").strip()
        if not username:
          raise RuntimeError("Unable to resolve username from Gitea /user response.")
        self.after(0, lambda: self._on_user_resolved(username))
      except Exception as e:
        self.after(0, lambda: self._on_fetch_failed(str(e)))

    threading.Thread(target=worker, daemon=True).start()

  def _on_user_resolved(self, username: str) -> None:
    self.var_username.set(username)
    self._set_busy(False)
    self._set_status("Idle", 0)
    self._log(f"Resolved current user: {username}")

  def _on_fetch_activity(self) -> None:
    if self._busy:
      return
    try:
      cfg = self._collect_config_from_ui()
      client = self._build_client()
      username = self.var_username.get().strip()
      if not username:
        user = client.get_current_user()
        username = str(user.get("login") or user.get("username") or "").strip()
        if not username:
          raise RuntimeError("Username is blank and could not be resolved from /user.")
        self.var_username.set(username)
      self._save_config()
    except Exception as e:
      messagebox.showerror(APP_TITLE, str(e))
      return

    days_back = int(cfg["query"]["days_back"])
    include_today = bool(cfg["query"]["include_today"])
    end_date = dt.date.today() if include_today else (dt.date.today() - dt.timedelta(days=1))
    start_date = end_date - dt.timedelta(days=days_back - 1)
    only_performed_by = bool(cfg["query"]["only_performed_by"])
    page_limit = int(cfg["gitea"]["page_limit"])

    self._set_busy(True)
    self._set_status("Fetching activity...", 0)
    self._log(f"Fetching {days_back} day(s) for {username} from {start_date.isoformat()} to {end_date.isoformat()}")

    def worker() -> None:
      try:
        points: list[tuple[dt.date, int]] = []
        days = _daterange(start_date, end_date)
        total_days = len(days)
        for idx, day in enumerate(days, start=1):
          count = client.count_user_activity_for_day(
            username=username,
            day=day,
            page_limit=page_limit,
            only_performed_by=only_performed_by,
          )
          points.append((day, count))
          pct = idx / total_days if total_days else 1.0
          self.after(0, lambda idx=idx, total_days=total_days, day=day, count=count, pct=pct: self._on_fetch_progress(idx, total_days, day, count, pct))
        self.after(0, lambda: self._on_fetch_complete(username, points))
      except Exception as e:
        self.after(0, lambda: self._on_fetch_failed(str(e)))

    self._worker_thread = threading.Thread(target=worker, daemon=True)
    self._worker_thread.start()

  def _on_fetch_progress(self, idx: int, total_days: int, day: dt.date, count: int, pct: float) -> None:
    self._set_status(f"{idx}/{total_days} {day.isoformat()} = {count}", pct)

  def _on_fetch_complete(self, username: str, points: list[tuple[dt.date, int]]) -> None:
    self._set_busy(False)
    self._set_status("Idle", 1.0 if points else 0.0)
    self._last_username = username
    self._last_results = list(points)
    self._log(f"Fetched {len(points)} daily rows for {username}.")
    self._redraw_from_last_results()

  def _on_fetch_failed(self, error_text: str) -> None:
    self._set_busy(False)
    self._set_status("Idle", 0)
    self._log(f"Fetch failed: {error_text}")
    messagebox.showerror(APP_TITLE, error_text)

  def _clear_results(self) -> None:
    self._last_results = []
    self._last_grouped = OrderedDict()
    self._last_username = ""
    self._draw_empty_states()
    self._log("Cleared in-memory results.")
    self._set_status("Idle", 0)

  def _draw_empty_states(self) -> None:
    self.lbl_total.configure(text="0")
    self.lbl_peak.configure(text="0")
    self.lbl_avg.configure(text="0")
    self.lbl_streak.configure(text="0")
    self.lbl_chart_subtitle.configure(text="No data loaded")
    self.canvas_chart.delete("all")
    self.canvas_heatmap.delete("all")
    self.canvas_chart.create_text(120, 60, text="Fetch activity to render the chart.", fill="#cbd5e1", anchor="w", font=("Segoe UI", 14, "bold"))
    self.canvas_heatmap.create_text(120, 60, text="Heatmap will appear after loading data.", fill="#cbd5e1", anchor="w", font=("Segoe UI", 14, "bold"))

  def _group_daily_counts(self, points: list[tuple[dt.date, int]]) -> OrderedDict[str, int]:
    mode = self.var_group_by.get().strip() or "day"
    grouped: OrderedDict[str, int] = OrderedDict()
    if mode == "day":
      for day, count in points:
        grouped[day.isoformat()] = grouped.get(day.isoformat(), 0) + count
      return grouped
    if mode == "week":
      for day, count in points:
        start = _week_start(day)
        key = f"{start.isoformat()} week"
        grouped[key] = grouped.get(key, 0) + count
      return grouped
    for day, count in points:
      start = _month_start(day)
      key = start.strftime("%Y-%m")
      grouped[key] = grouped.get(key, 0) + count
    return grouped

  def _redraw_from_last_results(self) -> None:
    if not self._last_results:
      self._draw_empty_states()
      return

    grouped = self._group_daily_counts(self._last_results)
    self._last_grouped = grouped
    total = sum(v for _, v in self._last_results)
    peak = max(v for _, v in self._last_results)
    avg = total / len(self._last_results) if self._last_results else 0
    best_streak = 0
    streak = 0
    for _day, count in self._last_results:
      if count > 0:
        streak += 1
        best_streak = max(best_streak, streak)
      else:
        streak = 0

    self.lbl_total.configure(text=_format_number(total))
    self.lbl_peak.configure(text=_format_number(peak))
    self.lbl_avg.configure(text=_format_number(avg))
    self.lbl_streak.configure(text=_format_number(best_streak))
    if self._last_results:
      start = self._last_results[0][0].isoformat()
      end = self._last_results[-1][0].isoformat()
      self.lbl_chart_subtitle.configure(text=f"{self._last_username or 'user'} | {start} to {end} | grouped by {self.var_group_by.get()}")

    self._draw_line_chart()
    self._draw_heatmap()

  def _draw_line_chart(self) -> None:
    canvas = self.canvas_chart
    canvas.delete("all")
    w = max(320, canvas.winfo_width())
    h = max(220, canvas.winfo_height())
    canvas.configure(bg="#0f172a")

    if not self._last_grouped:
      canvas.create_text(120, 60, text="Fetch activity to render the chart.", fill="#cbd5e1", anchor="w", font=("Segoe UI", 14, "bold"))
      return

    left = 60
    top = 20
    right = w - 20
    bottom = h - 50
    chart_w = max(40, right - left)
    chart_h = max(40, bottom - top)

    labels = list(self._last_grouped.keys())
    values = list(self._last_grouped.values())
    rolling = _roll_average([int(v) for v in values], max(1, _safe_int(self.var_rolling_average_days.get(), 7)))
    max_value = max(max(values), max(rolling) if rolling else 0, 1)

    canvas.create_rectangle(0, 0, w, h, fill="#0f172a", outline="")
    for idx in range(5):
      y = top + (chart_h * idx / 4)
      value = max_value - ((max_value * idx) / 4)
      canvas.create_line(left, y, right, y, fill="#1e293b", width=1)
      canvas.create_text(left - 10, y, text=_format_number(value), fill="#94a3b8", anchor="e", font=("Segoe UI", 9))

    def to_point(index: int, value: float, count: int) -> tuple[float, float]:
      x = left if count <= 1 else left + (chart_w * index / (count - 1))
      y = bottom - ((value / max_value) * chart_h)
      return x, y

    count = len(values)
    if count == 1:
      x, y = to_point(0, values[0], 1)
      canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#38bdf8", outline="")
    else:
      line_points: list[float] = []
      avg_points: list[float] = []
      for idx, value in enumerate(values):
        x, y = to_point(idx, value, count)
        line_points.extend([x, y])
        avg_x, avg_y = to_point(idx, rolling[idx], count)
        avg_points.extend([avg_x, avg_y])
        canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#38bdf8", outline="")
      canvas.create_line(*line_points, fill="#38bdf8", width=3, smooth=True)
      canvas.create_line(*avg_points, fill="#f59e0b", width=2, smooth=True, dash=(6, 4))

    ticks = min(6, len(labels))
    for tick_index in range(ticks):
      source_index = 0 if ticks == 1 else round((len(labels) - 1) * tick_index / (ticks - 1))
      x, _ = to_point(source_index, 0, max(1, len(labels)))
      canvas.create_text(x, bottom + 16, text=labels[source_index], fill="#94a3b8", anchor="n", font=("Segoe UI", 8))

    canvas.create_line(left, top, left, bottom, fill="#475569", width=2)
    canvas.create_line(left, bottom, right, bottom, fill="#475569", width=2)
    canvas.create_text(right, top - 2, text=f"Rolling avg: {max(1, _safe_int(self.var_rolling_average_days.get(), 7))}", fill="#fbbf24", anchor="ne", font=("Segoe UI", 9, "bold"))

  def _draw_heatmap(self) -> None:
    canvas = self.canvas_heatmap
    canvas.delete("all")
    w = max(260, canvas.winfo_width())
    h = max(220, canvas.winfo_height())
    canvas.configure(bg="#0f172a")

    if not self._last_results:
      canvas.create_text(120, 60, text="Heatmap will appear after loading data.", fill="#cbd5e1", anchor="w", font=("Segoe UI", 14, "bold"))
      return

    points = list(self._last_results)
    max_count = max((count for _, count in points), default=1)
    square = 13
    gap = 4
    x0 = 36
    y0 = 26

    first_day = points[0][0]
    start = first_day - dt.timedelta(days=(first_day.weekday() + 1) % 7)
    day_map = {day: count for day, count in points}

    def color_for_count(value: int) -> str:
      if value <= 0:
        return "#1e293b"
      ratio = value / max_count if max_count else 0
      if ratio < 0.25:
        return "#0f766e"
      if ratio < 0.5:
        return "#14b8a6"
      if ratio < 0.75:
        return "#22c55e"
      return "#84cc16"

    weekday_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    for row, label in enumerate(weekday_labels):
      y = y0 + row * (square + gap) + square / 2
      canvas.create_text(8, y, text=label, fill="#94a3b8", anchor="w", font=("Segoe UI", 8))

    month_markers: dict[tuple[int, int], str] = {}
    for day, _count in points:
      month_markers.setdefault((day.year, day.month), day.strftime("%b"))

    weeks = math.ceil(((points[-1][0] - start).days + 1) / 7)
    for week in range(weeks):
      week_start = start + dt.timedelta(days=week * 7)
      label_key = (week_start.year, week_start.month)
      if label_key in month_markers and week_start.day <= 7:
        x = x0 + week * (square + gap)
        canvas.create_text(x, 10, text=month_markers[label_key], fill="#94a3b8", anchor="nw", font=("Segoe UI", 8))

    for week in range(weeks):
      for row in range(7):
        day = start + dt.timedelta(days=week * 7 + row)
        if day not in day_map:
          continue
        x = x0 + week * (square + gap)
        y = y0 + row * (square + gap)
        count = day_map.get(day, 0)
        canvas.create_rectangle(x, y, x + square, y + square, fill=color_for_count(count), outline="")

    legend_y = min(h - 24, y0 + 7 * (square + gap) + 18)
    canvas.create_text(8, legend_y, text="Less", fill="#94a3b8", anchor="w", font=("Segoe UI", 8))
    for idx, color in enumerate(["#1e293b", "#0f766e", "#14b8a6", "#22c55e", "#84cc16"]):
      x = 44 + idx * (square + 3)
      canvas.create_rectangle(x, legend_y - 7, x + square, legend_y + 6, fill=color, outline="")
    canvas.create_text(44 + 5 * (square + 3) + 4, legend_y, text="More", fill="#94a3b8", anchor="w", font=("Segoe UI", 8))

  def _on_export_csv(self) -> None:
    if not self._last_results:
      messagebox.showinfo(APP_TITLE, "No activity data to export.")
      return
    path = filedialog.asksaveasfilename(
      title="Export Activity CSV",
      defaultextension=".csv",
      filetypes=[("CSV", "*.csv"), ("All Files", "*.*")],
      initialfile=f"gitea_activity_{self._last_username or 'user'}.csv",
    )
    if not path:
      return
    try:
      with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("date,count\n")
        for day, count in self._last_results:
          f.write(f"{day.isoformat()},{count}\n")
      self._log(f"Exported CSV: {path}")
    except Exception as e:
      messagebox.showerror(APP_TITLE, f"Failed to export CSV:\n{e}")

  def _on_close(self) -> None:
    try:
      self._save_config()
    except Exception:
      pass
    if self._busy:
      if not messagebox.askyesno(APP_TITLE, "A fetch is still running. Close the app anyway?"):
        return
    self.destroy()


def main() -> int:
  set_windows_app_user_model_id(APP_USER_MODEL_ID)
  app = GiteaActivityChartApp()
  app.mainloop()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
