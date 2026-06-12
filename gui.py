import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List

try:
    import winreg
except ImportError:
    winreg = None

from config import DB_DESTINO_CONFIG, DB_ORIGEN_CONFIG
from db.connection import DatabaseConnection
from db.table_data_cleaner import DeletionPlan, TableDataCleaner
from generator.deletion_report import deletion_plan_lines, write_deletion_report
from main import (
    comparar_metadata,
    escribir_reporte_conexiones,
    escribir_reporte_integridad,
    escribir_reporte_metadata,
    escribir_scripts,
    escribir_scripts_por_impacto,
    escribir_sql_fix_huerfanos,
    revisar_integridad_datos,
    validar_conexion,
)
from generator.pdf_report import SimplePDFReport


class SchemaComparatorDashboard(tk.Tk):
    """Dashboard Tkinter para ejecutar comparaciones y descargar resultados."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Schema Comparator Dashboard")
        self.geometry("1280x780")
        self.minsize(1040, 680)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.generated_files: Dict[str, Path] = {}
        self.style = ttk.Style(self)
        self.style.theme_use("clam")

        self.status_var = tk.StringVar(value="Listo")
        self.summary_var = tk.StringVar(value="Sin ejecuciones todavia")
        self.progress_var = tk.StringVar(value="Sin proceso activo")
        self.source_var = tk.StringVar(value=self._connection_label("ORIGEN", DB_ORIGEN_CONFIG))
        self.target_var = tk.StringVar(value=self._connection_label("DESTINO", DB_DESTINO_CONFIG))
        self.dark_mode = self._system_prefers_dark()
        self.theme_var = tk.StringVar(
            value="Modo claro" if self.dark_mode else "Modo oscuro"
        )
        self.theme_colors: Dict[str, str] = {}
        self.loading_dialog: LoadingDialog | None = None

        self._build_ui()
        self._apply_theme()
        self.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        navbar = ttk.Frame(self, style="Navbar.TFrame", padding=(22, 14))
        navbar.grid(row=0, column=0, sticky="ew")
        navbar.columnconfigure(0, weight=1)

        ttk.Label(
            navbar,
            text="Schema Comparator",
            style="NavbarTitle.TLabel",
            font=("Segoe UI", 18, "bold"),
        ).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            navbar,
            text="Panel de comparacion y control de bases de datos",
            style="NavbarMuted.TLabel",
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(navbar, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, rowspan=2, sticky="e", padx=(20, 12)
        )
        ttk.Button(
            navbar,
            textvariable=self.theme_var,
            command=self.toggle_theme,
            style="Theme.TButton",
        ).grid(
            row=0, column=2, rowspan=2, sticky="e"
        )

        dashboard = ttk.Frame(self)
        dashboard.grid(row=1, column=0, sticky="nsew")
        dashboard.columnconfigure(1, weight=1)
        dashboard.rowconfigure(0, weight=1)

        sidebar_shell = ttk.Frame(dashboard, style="Sidebar.TFrame")
        sidebar_shell.grid(row=0, column=0, sticky="ns")
        sidebar_shell.configure(width=330)
        sidebar_shell.grid_propagate(False)
        sidebar_shell.rowconfigure(0, weight=1)
        sidebar_shell.columnconfigure(0, weight=1)

        self.sidebar_canvas = tk.Canvas(
            sidebar_shell,
            width=312,
            highlightthickness=0,
            bd=0,
        )
        self.sidebar_canvas.grid(row=0, column=0, sticky="nsew")
        sidebar_scrollbar = ttk.Scrollbar(
            sidebar_shell,
            orient="vertical",
            command=self.sidebar_canvas.yview,
        )
        sidebar_scrollbar.grid(row=0, column=1, sticky="ns")
        self.sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)

        sidebar = ttk.Frame(self.sidebar_canvas, style="Sidebar.TFrame", padding=(14, 16))
        self.sidebar_window = self.sidebar_canvas.create_window(
            (0, 0),
            window=sidebar,
            anchor="nw",
        )
        sidebar.columnconfigure(0, weight=1)
        sidebar.bind("<Configure>", self._update_sidebar_scroll_region)
        self.sidebar_canvas.bind("<Configure>", self._resize_sidebar_content)
        self.sidebar_canvas.bind("<MouseWheel>", self._scroll_sidebar)

        ttk.Label(sidebar, text="MODULOS", style="SidebarHeading.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=(0, 10)
        )
        self.process_buttons: List[ttk.Button] = []
        modules = [
            (
                "Flujo completo",
                "Valida conexiones, compara estructura y revisa la integridad.",
                [("Ejecutar todo", self.run_all, True)],
            ),
            (
                "Conexiones",
                "Comprueba acceso a ORIGEN y DESTINO y genera su reporte.",
                [
                    ("Ejecutar validacion", self.run_validate_connections, True),
                    ("Descargar PDF", self.download_connections_pdf, False),
                ],
            ),
            (
                "Estructura",
                "Compara schemas, tablas, columnas, llaves e indices.",
                [
                    ("Comparar", self.run_compare_schema, True),
                    ("PDF", self.download_metadata_pdf, False),
                    ("Ver SQL", self.open_sql_viewer, False),
                    ("Guardar SQL", self.download_sql_scripts, False),
                ],
            ),
            (
                "Integridad",
                "Busca nulos, duplicados y relaciones huerfanas.",
                [
                    ("Ejecutar revision", self.run_check_integrity, True),
                    ("Descargar PDF", self.download_integrity_pdf, False),
                ],
            ),
            (
                "Borrado de datos",
                "Analiza dependencias antes de vaciar una tabla.",
                [
                    ("Abrir herramienta", self.open_data_deletion_dialog, True),
                    ("Descargar PDF", self.download_deletion_pdf, False),
                ],
            ),
            (
                "Panel de proceso",
                "Limpia los resultados visibles del contenedor central.",
                [("Limpiar proceso", self.clear_log, True)],
            ),
        ]
        for row, (title, description, actions) in enumerate(modules, start=1):
            self._add_sidebar_module(sidebar, row, title, description, actions)

        body = ttk.Frame(dashboard, style="Content.TFrame", padding=(24, 20, 24, 24))
        body.grid(row=0, column=1, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text="Proceso ejecutado", style="PageTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(body, textvariable=self.summary_var, style="PageMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 14)
        )

        log_frame = ttk.Frame(body, style="LogPanel.TFrame", padding=1)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            padx=12,
            pady=10,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self._log("Dashboard iniciado.")
        self._log("Configura las credenciales en .env y ejecuta una accion.")

    def _update_sidebar_scroll_region(self, _event: tk.Event) -> None:
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _resize_sidebar_content(self, event: tk.Event) -> None:
        self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width)

    def _scroll_sidebar(self, event: tk.Event) -> None:
        self.sidebar_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _add_sidebar_module(
        self,
        parent: ttk.Frame,
        row: int,
        title: str,
        description: str,
        actions: List[tuple[str, Callable[[], None], bool]],
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 9))
        card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            card,
            text=description,
            style="CardDescription.TLabel",
            wraplength=275,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(2, 7))

        action_frame = ttk.Frame(card, style="Card.TFrame")
        action_frame.grid(row=2, column=0, sticky="ew")
        for index, (text, command, is_process) in enumerate(actions):
            row_index, column = divmod(index, 2)
            action_frame.columnconfigure(column, weight=1)
            button = ttk.Button(
                action_frame,
                text=text,
                command=command,
                style="Primary.TButton" if is_process else "Secondary.TButton",
            )
            button.grid(
                row=row_index,
                column=column,
                sticky="ew",
                padx=(0 if column == 0 else 4, 0),
                pady=(0 if row_index == 0 else 4, 0),
            )
            if is_process:
                self.process_buttons.append(button)

    def run_validate_connections(self) -> None:
        self._start_task("Validando conexiones", self._validate_connections_task)

    def run_compare_schema(self) -> None:
        self._start_task("Comparando estructura", self._compare_schema_task)

    def run_check_integrity(self) -> None:
        self._start_task("Revisando integridad", self._check_integrity_task)

    def run_all(self) -> None:
        self._start_task("Ejecutando flujo completo", self._run_all_task)

    def open_data_deletion_dialog(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Proceso en ejecucion", "Espera a que termine el proceso actual.")
            return
        DataDeletionDialog(self)

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.theme_var.set("Modo claro" if self.dark_mode else "Modo oscuro")
        self._apply_theme()

    def download_connections_pdf(self) -> None:
        pdf_path = self.generated_files.get("connections_pdf") or Path("output/reporte_conexiones.pdf")
        self._download_file(pdf_path, "Guardar PDF de conexiones", [("PDF", "*.pdf")])

    def download_metadata_pdf(self) -> None:
        pdf_path = self.generated_files.get("metadata_pdf") or Path("output/reporte_metadata.pdf")
        self._download_file(pdf_path, "Guardar PDF de estructura", [("PDF", "*.pdf")])

    def download_integrity_pdf(self) -> None:
        pdf_path = self.generated_files.get("integrity_pdf") or Path("output/reporte_integridad_datos.pdf")
        self._download_file(pdf_path, "Guardar PDF de integridad", [("PDF", "*.pdf")])

    def download_deletion_pdf(self) -> None:
        pdf_path = self.generated_files.get("deletion_pdf") or Path("output/reporte_borrado_datos.pdf")
        self._download_file(pdf_path, "Guardar PDF de borrado", [("PDF", "*.pdf")])

    def download_sql_scripts(self) -> None:
        sql_paths = self._available_sql_paths()
        if not sql_paths:
            messagebox.showwarning(
                "Sin scripts",
                "Primero ejecuta la comparacion de estructura o el flujo completo.",
            )
            return

        target_dir = filedialog.askdirectory(title="Selecciona carpeta para guardar scripts SQL")
        if not target_dir:
            return

        copied = []
        for path in sql_paths:
            destination = Path(target_dir) / path.name
            shutil.copyfile(path, destination)
            copied.append(destination)

        self._log(f"Scripts SQL descargados en: {target_dir}")
        self._log(f"Archivos copiados: {', '.join(path.name for path in copied)}")
        messagebox.showinfo("Scripts descargados", f"Se copiaron {len(copied)} archivo(s) SQL.")

    def open_sql_viewer(self) -> None:
        sql_paths = self._available_sql_paths()
        initial_path = sql_paths[0] if sql_paths else None
        SQLViewerDialog(self, initial_path)

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.status_var.set("Listo")

    def _validate_connections_task(self) -> None:
        resultados = [
            validar_conexion("BD ORIGEN", DatabaseConnection(DB_ORIGEN_CONFIG)),
            validar_conexion("BD DESTINO", DatabaseConnection(DB_DESTINO_CONFIG)),
        ]
        report_path = escribir_reporte_conexiones(resultados)
        pdf_path = self._write_pdf_from_text(report_path, Path("output/reporte_conexiones.pdf"))
        self.generated_files["connections_report"] = report_path
        self.generated_files["connections_pdf"] = pdf_path

        ok_count = sum(1 for resultado in resultados if resultado["estado"] == "OK")
        self._log(f"Reporte de conexiones generado: {report_path}")
        self._log(f"Reporte de conexiones PDF generado: {pdf_path}")
        for resultado in resultados:
            if resultado["estado"] == "OK":
                self._log(
                    f"[OK] {resultado['nombre']}: {resultado['database']} / {resultado['schema']}"
                )
            else:
                self._log(f"[ERROR] {resultado['nombre']}: {resultado['error']}")
        self._summary(f"Conexiones OK: {ok_count}/2")

        if ok_count != 2:
            raise RuntimeError("Una o mas conexiones fallaron. Revisa output/reporte_conexiones.txt.")

    def _compare_schema_task(self) -> None:
        diferencias = comparar_metadata(
            DatabaseConnection(DB_ORIGEN_CONFIG),
            DatabaseConnection(DB_DESTINO_CONFIG),
        )
        metadata_path = escribir_reporte_metadata(diferencias)
        metadata_pdf_path = self._write_pdf_from_text(metadata_path, Path("output/reporte_metadata.pdf"))
        scripts_path = escribir_scripts(diferencias)
        scripts_impacto_paths = escribir_scripts_por_impacto(diferencias)

        self.generated_files["metadata_report"] = metadata_path
        self.generated_files["metadata_pdf"] = metadata_pdf_path
        self.generated_files["latest_pdf"] = metadata_pdf_path
        self.generated_files["scripts"] = scripts_path
        for path in scripts_impacto_paths:
            self.generated_files[f"script_{path.stem}"] = path

        self._log(f"Reporte de metadata generado: {metadata_path}")
        self._log(f"Reporte de metadata PDF generado: {metadata_pdf_path}")
        self._log(f"Script SQL consolidado generado: {scripts_path}")
        for path in scripts_impacto_paths:
            self._log(f"Script SQL por impacto generado: {path}")

        if diferencias:
            self._log(f"Diferencias encontradas: {len(diferencias)}")
            self._summary(f"Estructura: {len(diferencias)} diferencia(s)")
        else:
            self._log("Las estructuras son iguales. No se encontraron diferencias de metadata.")
            self._summary("Estructura: iguales")

    def _check_integrity_task(self) -> None:
        hallazgos = revisar_integridad_datos(
            DatabaseConnection(DB_ORIGEN_CONFIG),
            DatabaseConnection(DB_DESTINO_CONFIG),
        )
        txt_path, pdf_path = escribir_reporte_integridad(hallazgos)
        fix_path = escribir_sql_fix_huerfanos(hallazgos)

        self.generated_files["integrity_txt"] = txt_path
        self.generated_files["integrity_pdf"] = pdf_path
        self.generated_files["latest_pdf"] = pdf_path
        self.generated_files["fix_orphans"] = fix_path

        self._log(f"Reporte de integridad TXT generado: {txt_path}")
        self._log(f"Reporte de integridad PDF generado: {pdf_path}")
        self._log(f"SQL de fix para huerfanos generado: {fix_path}")

        if hallazgos:
            self._log(f"Hallazgos de integridad encontrados: {len(hallazgos)}")
            self._summary(f"Integridad: {len(hallazgos)} hallazgo(s)")
        else:
            self._log("No se encontraron problemas de integridad de datos.")
            self._summary("Integridad: sin hallazgos")

    def _run_all_task(self) -> None:
        self._log("Paso 1/3: validando conexiones...")
        self._validate_connections_task()
        self._log("Paso 2/3: comparando estructura...")
        self._compare_schema_task()
        self._log("Paso 3/3: revisando integridad de datos...")
        self._check_integrity_task()
        self._summary("Flujo completo terminado")

    def _start_task(self, status: str, task: Callable[[], None]) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Proceso en ejecucion", "Espera a que termine el proceso actual.")
            return

        self.status_var.set(status)
        self.progress_var.set(status)
        self._set_actions_enabled(False)
        self._show_loading_dialog(status)
        self._log("")
        self._log(f"== {status} ==")

        self.worker = threading.Thread(target=self._run_task, args=(status, task), daemon=True)
        self.worker.start()

    def _run_task(self, status: str, task: Callable[[], None]) -> None:
        try:
            task()
            self.log_queue.put(f"__STATUS__Listo")
            self.log_queue.put(f"{status}: terminado.")
        except Exception as error:
            self.log_queue.put(f"__STATUS__Error")
            self.log_queue.put(f"[ERROR] {error}")
        finally:
            self.log_queue.put("__ENABLE_ACTIONS__")

    def _drain_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if message == "__ENABLE_ACTIONS__":
                self._set_actions_enabled(True)
                self._close_loading_dialog()
                self.progress_var.set("Sin proceso activo")
            elif message.startswith("__STATUS__"):
                self.status_var.set(message.replace("__STATUS__", "", 1))
            elif message.startswith("__SUMMARY__"):
                self.summary_var.set(message.replace("__SUMMARY__", "", 1))
            else:
                self._append_log(message)

        self.after(100, self._drain_log_queue)

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _summary(self, message: str) -> None:
        self.log_queue.put(f"__SUMMARY__{message}")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_actions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.process_buttons:
            button.configure(state=state)

    def _show_loading_dialog(self, status: str) -> None:
        self.loading_dialog = LoadingDialog(self, status, self.theme_colors)

    def _close_loading_dialog(self) -> None:
        if self.loading_dialog is not None:
            self.loading_dialog.close()
            self.loading_dialog = None

    def _system_prefers_dark(self) -> bool:
        if winreg is None:
            return False
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return apps_use_light_theme == 0
        except (FileNotFoundError, OSError):
            return False

    def _apply_theme(self) -> None:
        if self.dark_mode:
            colors = {
                "bg": "#111827",
                "panel": "#1f2937",
                "text": "#f9fafb",
                "muted": "#cbd5e1",
                "field": "#0f172a",
                "accent": "#1e40af",
                "button": "#334155",
                "button_active": "#475569",
                "border": "#334155",
            }
        else:
            colors = {
                "bg": "#f5f7fb",
                "panel": "#ffffff",
                "text": "#172033",
                "muted": "#475569",
                "field": "#ffffff",
                "accent": "#1e3a8a",
                "button": "#e8eef8",
                "button_active": "#d7e3f5",
                "border": "#cbd5e1",
            }

        self.theme_colors = colors
        self.configure(bg=colors["bg"])
        self.style.configure(".", background=colors["bg"], foreground=colors["text"], font=("Segoe UI", 10))
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure("Navbar.TFrame", background=colors["panel"])
        self.style.configure("NavbarTitle.TLabel", background=colors["panel"], foreground=colors["text"])
        self.style.configure("NavbarMuted.TLabel", background=colors["panel"], foreground=colors["muted"])
        self.style.configure(
            "Status.TLabel",
            background=colors["panel"],
            foreground=colors["accent"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure("Sidebar.TFrame", background=colors["panel"])
        self.sidebar_canvas.configure(bg=colors["panel"])
        self.style.configure(
            "SidebarHeading.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure("Content.TFrame", background=colors["bg"])
        self.style.configure("Card.TFrame", background=colors["bg"])
        self.style.configure(
            "CardTitle.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "CardDescription.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=("Segoe UI", 8),
        )
        self.style.configure(
            "PageTitle.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI", 18, "bold"),
        )
        self.style.configure("PageMuted.TLabel", background=colors["bg"], foreground=colors["muted"])
        self.style.configure("LogPanel.TFrame", background=colors["border"])
        self.style.configure("TLabelframe", background=colors["bg"], bordercolor=colors["border"])
        self.style.configure(
            "TLabelframe.Label",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
        self.style.configure(
            "TButton",
            background=colors["button"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            focusthickness=1,
            padding=(10, 7),
        )
        self.style.configure(
            "Primary.TButton",
            background=colors["accent"],
            foreground="#ffffff",
            bordercolor=colors["accent"],
            padding=(8, 6),
            font=("Segoe UI", 8, "bold"),
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", colors["accent"]), ("disabled", colors["border"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "Secondary.TButton",
            background=colors["button"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=(8, 6),
            font=("Segoe UI", 8),
        )
        self.style.configure(
            "Theme.TButton",
            background=colors["button"],
            foreground=colors["text"],
            bordercolor=colors["border"],
        )
        self.style.configure("Dialog.TFrame", background=colors["bg"])
        self.style.configure(
            "Dialog.TLabelframe",
            background=colors["panel"],
            bordercolor=colors["border"],
        )
        self.style.configure(
            "Dialog.TLabelframe.Label",
            background=colors["panel"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "Dialog.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
        )
        self.style.configure(
            "DialogPanel.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
        )
        self.style.configure(
            "Dialog.TEntry",
            fieldbackground=colors["field"],
            foreground=colors["text"],
            insertcolor=colors["text"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
        )
        self.style.configure(
            "Dialog.TCombobox",
            fieldbackground=colors["field"],
            background=colors["button"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
        )
        self.style.map(
            "Dialog.TCombobox",
            fieldbackground=[
                ("readonly", colors["field"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[
                ("readonly", colors["text"]),
                ("disabled", colors["muted"]),
            ],
            selectbackground=[("readonly", colors["field"])],
            selectforeground=[("readonly", colors["text"])],
        )
        self.style.configure(
            "DialogPrimary.TButton",
            background=colors["accent"],
            foreground="#ffffff",
            bordercolor=colors["accent"],
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        self.style.map(
            "DialogPrimary.TButton",
            background=[("active", colors["accent"]), ("disabled", colors["border"])],
            foreground=[("active", "#ffffff"), ("disabled", colors["muted"])],
        )
        self.style.configure(
            "DialogSecondary.TButton",
            background=colors["button"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=(10, 7),
        )
        self.style.map(
            "DialogSecondary.TButton",
            background=[
                ("active", colors["button_active"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[
                ("active", colors["text"]),
                ("disabled", colors["muted"]),
            ],
        )
        self.option_add("*TCombobox*Listbox.background", colors["field"])
        self.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.style.map(
            "TButton",
            background=[("active", colors["button_active"]), ("disabled", colors["panel"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.log_text.configure(
            bg=colors["field"],
            fg=colors["text"],
            insertbackground=colors["text"],
            selectbackground=colors["accent"],
            selectforeground="#ffffff",
            relief="solid",
            borderwidth=1,
        )

    def _download_file(self, source: Path, title: str, filetypes: list[tuple[str, str]]) -> None:
        if not source.exists():
            messagebox.showwarning("Archivo no disponible", f"No existe el archivo: {source}")
            return

        target = filedialog.asksaveasfilename(
            title=title,
            initialfile=source.name,
            defaultextension=source.suffix,
            filetypes=filetypes,
        )
        if not target:
            return

        shutil.copyfile(source, target)
        self._log(f"Archivo descargado: {target}")
        messagebox.showinfo("Descarga lista", f"Archivo guardado en:\n{target}")

    def _write_pdf_from_text(self, text_path: Path, pdf_path: Path) -> Path:
        lines = text_path.read_text(encoding="utf-8").splitlines()
        SimplePDFReport(str(pdf_path)).write(lines)
        return pdf_path

    def _available_sql_paths(self) -> List[Path]:
        paths = [
            self.generated_files.get("scripts") or Path("output/scripts.sql"),
            Path("output/scripts_critica.sql"),
            Path("output/scripts_alto.sql"),
            Path("output/scripts_medio.sql"),
            Path("output/scripts_bajo.sql"),
            self.generated_files.get("fix_orphans") or Path("output/fix_huerfanos.sql"),
        ]
        return [path for path in paths if path and path.exists()]

    def _connection_label(self, name: str, config: Dict[str, str]) -> str:
        try:
            parsed = DatabaseConnection(config).parsed_url
            return (
                f"BD {name}: {parsed['engine']}://{parsed['host']}:{parsed['port']}/"
                f"{parsed['database']} schema={config.get('schema', '') or '(default)'}"
            )
        except Exception as error:
            return f"BD {name}: configuracion invalida ({error})"


class LoadingDialog(tk.Toplevel):
    """Dialogo modal que bloquea el dashboard mientras termina una tarea."""

    def __init__(
        self,
        parent: SchemaComparatorDashboard,
        status: str,
        colors: Dict[str, str],
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.colors = colors
        self.spinner_job: str | None = None
        self.spinner_angle = 90

        self.title("Proceso en ejecucion")
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.configure(bg=colors["panel"])

        container = tk.Frame(self, bg=colors["panel"], padx=28, pady=24)
        container.pack(fill="both", expand=True)

        self.spinner_canvas = tk.Canvas(
            container,
            width=58,
            height=58,
            bg=colors["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.spinner_canvas.pack(pady=(0, 12))
        self.spinner_arc = self.spinner_canvas.create_arc(
            7,
            7,
            51,
            51,
            start=self.spinner_angle,
            extent=250,
            style="arc",
            width=5,
            outline=colors["accent"],
        )
        tk.Label(
            container,
            text=status,
            bg=colors["panel"],
            fg=colors["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack()
        tk.Label(
            container,
            text="Espera a que el proceso termine.",
            bg=colors["panel"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
        ).pack(pady=(4, 0))

        self.update_idletasks()
        self._center_over_parent()
        self.grab_set()
        self._animate()

    def _center_over_parent(self) -> None:
        width = 340
        height = 180
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - width) // 2
        y = self.parent.winfo_rooty() + (self.parent.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _animate(self) -> None:
        self.spinner_angle = (self.spinner_angle - 18) % 360
        self.spinner_canvas.itemconfigure(self.spinner_arc, start=self.spinner_angle)
        self.spinner_job = self.after(55, self._animate)

    def close(self) -> None:
        if self.spinner_job is not None:
            self.after_cancel(self.spinner_job)
            self.spinner_job = None
        if self.grab_current() == self:
            self.grab_release()
        self.destroy()


class SQLViewerDialog(tk.Toplevel):
    """Carga y muestra scripts SQL como texto plano dentro del programa."""

    def __init__(
        self,
        parent: SchemaComparatorDashboard,
        initial_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.colors = parent.theme_colors
        self.path_var = tk.StringVar(value="Ningun archivo cargado")

        self.title("Visor de archivos SQL")
        self.geometry("940x640")
        self.minsize(700, 480)
        self.transient(parent)
        self.configure(bg=self.colors["bg"])

        header = tk.Frame(self, bg=self.colors["panel"], padx=18, pady=14)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Visor SQL",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")
        tk.Button(
            header,
            text="Cargar archivo",
            command=self.load_file,
            bg=self.colors["accent"],
            fg="#ffffff",
            activebackground=self.colors["accent"],
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=7,
            cursor="hand2",
        ).pack(side="right")

        tk.Label(
            self,
            textvariable=self.path_var,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            anchor="w",
            padx=18,
            pady=10,
        ).pack(fill="x")

        editor = tk.Frame(self, bg=self.colors["border"], padx=1, pady=1)
        editor.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        editor.rowconfigure(0, weight=1)
        editor.columnconfigure(0, weight=1)

        self.text = tk.Text(
            editor,
            wrap="none",
            bg=self.colors["field"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            font=("Consolas", 10),
            padx=12,
            pady=12,
            relief="flat",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(editor, orient="vertical", command=self.text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(editor, orient="horizontal", command=self.text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.text.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        if initial_path is not None:
            self._load_path(initial_path)
        else:
            self.text.insert(
                "1.0",
                "-- No hay scripts generados todavia.\n"
                "-- Usa 'Cargar archivo' para abrir un archivo .sql.",
            )

    def load_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Selecciona un archivo SQL",
            initialdir=str(Path("output").resolve()),
            filetypes=[("Archivos SQL", "*.sql"), ("Texto", "*.txt"), ("Todos", "*.*")],
        )
        if selected:
            self._load_path(Path(selected))

    def _load_path(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="latin-1")
        except OSError as error:
            messagebox.showerror(
                "No se pudo cargar",
                f"No fue posible leer el archivo:\n{error}",
                parent=self,
            )
            return

        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self.path_var.set(str(path.resolve()))


class DataDeletionDialog(tk.Toplevel):
    """Dialogo para analizar y ejecutar el vaciado seguro de una tabla."""

    CONNECTIONS = {
        "BD ORIGEN": DB_ORIGEN_CONFIG,
        "BD DESTINO": DB_DESTINO_CONFIG,
    }

    def __init__(self, dashboard: SchemaComparatorDashboard) -> None:
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.colors = dashboard.theme_colors
        self.title("Vaciar datos de tabla")
        self.geometry("760x590")
        self.minsize(680, 520)
        self.transient(dashboard)
        self.grab_set()
        self.configure(bg=self.colors["bg"])

        self.result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.plan: DeletionPlan | None = None
        self.plan_connection_name = ""

        self.connection_var = tk.StringVar(value="BD DESTINO")
        self.table_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Selecciona una conexion, escribe la tabla y genera el informe previo."
        )

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll_results)
        self.table_entry.focus_set()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(
            self,
            text="Tabla a vaciar",
            padding=14,
            style="Dialog.TLabelframe",
        )
        form.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Base de datos:", style="DialogPanel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.connection_combo = ttk.Combobox(
            form,
            textvariable=self.connection_var,
            values=list(self.CONNECTIONS),
            state="readonly",
            style="Dialog.TCombobox",
        )
        self.connection_combo.grid(row=0, column=1, sticky="ew")

        ttk.Label(form, text="Nombre de tabla:", style="DialogPanel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0)
        )
        self.table_entry = ttk.Entry(
            form,
            textvariable=self.table_var,
            style="Dialog.TEntry",
        )
        self.table_entry.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self.table_entry.bind("<Return>", lambda _event: self.analyze())

        actions = ttk.Frame(self, padding=(16, 4), style="Dialog.TFrame")
        actions.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.columnconfigure(2, weight=1)

        self.analyze_button = ttk.Button(
            actions,
            text="Generar informe previo",
            command=self.analyze,
            style="DialogPrimary.TButton",
        )
        self.analyze_button.grid(row=0, column=0, sticky="ew", padx=4)
        self.delete_button = ttk.Button(
            actions,
            text="Confirmar y borrar",
            command=self.confirm_delete,
            state="disabled",
            style="DialogPrimary.TButton",
        )
        self.delete_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.close_button = ttk.Button(
            actions,
            text="Cerrar",
            command=self._close,
            style="DialogSecondary.TButton",
        )
        self.close_button.grid(row=0, column=2, sticky="ew", padx=4)

        report_frame = ttk.LabelFrame(
            self,
            text="Informe",
            padding=10,
            style="Dialog.TLabelframe",
        )
        report_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)

        self.report_text = tk.Text(
            report_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            padx=10,
            pady=10,
            bg=self.colors["field"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            relief="flat",
        )
        self.report_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            report_frame, orient="vertical", command=self.report_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.report_text.configure(yscrollcommand=scrollbar.set)

        ttk.Label(
            self,
            textvariable=self.status_var,
            style="Dialog.TLabel",
        ).grid(
            row=3, column=0, sticky="w", padx=20, pady=(0, 14)
        )

    def analyze(self) -> None:
        table_name = self.table_var.get().strip()
        if not table_name:
            messagebox.showwarning("Falta la tabla", "Escribe el nombre de la tabla.", parent=self)
            return

        connection_name = self.connection_var.get()
        config = dict(self.CONNECTIONS[connection_name])
        self.plan = None
        self.plan_connection_name = ""
        self._set_busy(True, "Analizando claves foraneas y contando filas...")
        self.worker = threading.Thread(
            target=self._analyze_worker,
            args=(connection_name, config, table_name),
            daemon=True,
        )
        self.worker.start()

    def confirm_delete(self) -> None:
        if self.plan is None:
            return

        confirmed = messagebox.askyesno(
            "Confirmar borrado irreversible",
            (
                f"Se borraran {self.plan.total_rows} fila(s) de "
                f"{len(self.plan.deletion_order)} tabla(s).\n\n"
                "La operacion no se puede deshacer despues del commit. "
                "¿Deseas continuar?"
            ),
            icon="warning",
            parent=self,
        )
        if not confirmed:
            return

        config = dict(self.CONNECTIONS[self.plan_connection_name])
        self._set_busy(True, "Ejecutando borrado dentro de una transaccion...")
        self.worker = threading.Thread(
            target=self._delete_worker,
            args=(self.plan_connection_name, config, self.plan),
            daemon=True,
        )
        self.worker.start()

    def _analyze_worker(
        self,
        connection_name: str,
        config: Dict[str, str],
        table_name: str,
    ) -> None:
        cleaner = TableDataCleaner(DatabaseConnection(config))
        try:
            plan = cleaner.build_plan(table_name)
            self.result_queue.put(("plan", (connection_name, plan)))
        except Exception as error:
            self.result_queue.put(("error", str(error)))
        finally:
            cleaner.close()

    def _delete_worker(
        self,
        connection_name: str,
        config: Dict[str, str],
        plan: DeletionPlan,
    ) -> None:
        cleaner = TableDataCleaner(DatabaseConnection(config))
        try:
            deleted_rows = cleaner.execute(plan)
            txt_path, pdf_path = write_deletion_report(
                plan, deleted_rows, connection_name
            )
            self.result_queue.put(
                ("deleted", (deleted_rows, txt_path, pdf_path))
            )
        except Exception as error:
            self.result_queue.put(("error", f"El borrado fue revertido. Detalle: {error}"))
        finally:
            cleaner.close()

    def _poll_results(self) -> None:
        while True:
            try:
                result_type, payload = self.result_queue.get_nowait()
            except queue.Empty:
                break

            if result_type == "plan":
                connection_name, plan = payload
                self.plan = plan
                self.plan_connection_name = connection_name
                self._show_lines(deletion_plan_lines(plan, connection_name))
                self._set_busy(False, "Informe previo listo. Revisa el detalle antes de borrar.")
                self.delete_button.configure(state="normal")
            elif result_type == "deleted":
                deleted_rows, txt_path, pdf_path = payload
                total = sum(deleted_rows.values())
                self.dashboard.generated_files["deletion_txt"] = txt_path
                self.dashboard.generated_files["deletion_pdf"] = pdf_path
                self.dashboard.generated_files["latest_pdf"] = pdf_path
                self.dashboard._log(
                    f"Borrado completado: {total} fila(s). Reporte PDF: {pdf_path}"
                )
                self.dashboard._summary(f"Borrado: {total} fila(s)")
                self._set_busy(False, f"Borrado completado. PDF generado: {pdf_path}")
                self.analyze_button.configure(state="disabled")
                self.delete_button.configure(state="disabled")
                messagebox.showinfo(
                    "Borrado completado",
                    f"Se borraron {total} fila(s).\n\nReporte PDF:\n{pdf_path}",
                    parent=self,
                )
            elif result_type == "error":
                self._set_busy(False, "No se pudo completar la operacion.")
                self.delete_button.configure(state="disabled")
                messagebox.showerror("Operacion no completada", payload, parent=self)

        if self.winfo_exists():
            self.after(100, self._poll_results)

    def _show_lines(self, lines: List[str]) -> None:
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", "\n".join(lines))
        self.report_text.configure(state="disabled")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_var.set(status)
        state = "disabled" if busy else "normal"
        self.analyze_button.configure(state=state)
        self.close_button.configure(state=state)
        self.connection_combo.configure(state="disabled" if busy else "readonly")
        self.table_entry.configure(state=state)
        if busy:
            self.delete_button.configure(state="disabled")

    def _close(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(
                "Operacion en curso",
                "Espera a que termine la operacion actual.",
                parent=self,
            )
            return
        self.grab_release()
        self.destroy()


def main() -> None:
    app = SchemaComparatorDashboard()
    app.mainloop()


if __name__ == "__main__":
    main()
