#!/usr/bin/env python3
"""
Enhanced GUI Interface for Card Scanner with Arduino Controls
Features: Live camera, CSV export, Arduino monitoring/control
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import cv2
from PIL import Image, ImageTk
import threading
import time
import csv
from datetime import datetime
from optimized_scanner import OptimizedCardScanner


class ScannerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Card Scanner Pro - Enhanced Control Interface")
        self.root.geometry("1650x950")
        self.root.configure(bg='#2b2b2b')
        
        # Scanner & camera
        self.scanner = None
        self.camera = None
        self.running = False
        self.auto_scan = True
        self.last_scan_time = 0
        self.scan_cooldown = 2.0
        
        # Detection state
        self.current_frame = None
        self.detection_info = None
        
        # CSV export
        self.scan_history = []
        self.csv_file = None
        self.auto_export_var = tk.BooleanVar(value=False)
        
        # Arduino monitoring
        self.arduino_monitoring = False
        self.arduino_monitor_thread = None
        self.sensor_data = {'range': 0, 'x_min': 0, 'x_max': 0, 'y_min': 0, 'y_max': 0, 'z_min': 0, 'z_max': 0, 'started': 0}
        self.machine_started = False  # Track if Arduino is in Started state
        # Camera canvas size (adjustable to make UI fit on smaller screens)
        self.canvas_width = 600
        self.canvas_height = 540
        
        self._setup_gui()
        self._init_scanner()
        
    def _setup_gui(self):
        """Setup main GUI with tabbed interface"""
        main_frame = tk.Frame(self.root, bg='#2b2b2b')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left: Camera feed
        left_panel = tk.Frame(main_frame, bg='#2b2b2b')
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        self._setup_camera_panel(left_panel)
        
        # Right: Tabbed control panel
        right_panel = tk.Frame(main_frame, bg='#2b2b2b', width=700)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        right_panel.pack_propagate(False)
        
        # Create notebook (tabs)
        self.notebook = ttk.Notebook(right_panel)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Tab 1: Scanner Settings
        scanner_tab = tk.Frame(self.notebook, bg='#2b2b2b')
        self.notebook.add(scanner_tab, text='📷 Scanner')
        self._setup_scanner_tab(scanner_tab)
        
        # Tab 2: Arduino Control
        arduino_tab = tk.Frame(self.notebook, bg='#2b2b2b')
        self.notebook.add(arduino_tab, text='🤖 Arduino')
        self._setup_arduino_tab(arduino_tab)
        
        # Tab 3: Export & Stats
        export_tab = tk.Frame(self.notebook, bg='#2b2b2b')
        self.notebook.add(export_tab, text='📊 Export')
        self._setup_export_tab(export_tab)
        
    def _setup_camera_panel(self, parent):
        """Setup camera feed panel"""
        tk.Label(parent, text="LIVE CAMERA FEED", bg='#2b2b2b', fg='#ffffff', 
                font=('Arial', 14, 'bold')).pack(pady=(0, 5))

        self.canvas = tk.Canvas(parent, width=self.canvas_width, height=self.canvas_height, bg='#000000', 
                               highlightthickness=2, highlightbackground='#404040')
        self.canvas.pack()
        
        # Controls
        control_frame = tk.Frame(parent, bg='#2b2b2b')
        control_frame.pack(pady=10)
        
        self.start_btn = tk.Button(control_frame, text="▶ Start", command=self.start_camera,
                                   bg='#4CAF50', fg='white', font=('Arial', 11, 'bold'),
                                   padx=15, pady=8, cursor='hand2')
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = tk.Button(control_frame, text="⏸ Stop", command=self.stop_camera,
                                  bg='#f44336', fg='white', font=('Arial', 11, 'bold'),
                                  padx=15, pady=8, cursor='hand2', state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        self.scan_btn = tk.Button(control_frame, text="🔍 Scan", command=self.manual_scan,
                                  bg='#2196F3', fg='white', font=('Arial', 11, 'bold'),
                                  padx=15, pady=8, cursor='hand2')
        self.scan_btn.pack(side=tk.LEFT, padx=5)
        
        self.auto_scan_var = tk.BooleanVar(value=True)
        tk.Checkbutton(parent, text="Auto-Scan", variable=self.auto_scan_var,
                      command=self.toggle_auto_scan, bg='#2b2b2b', fg='#ffffff',
                      selectcolor='#404040', font=('Arial', 10),
                      activebackground='#2b2b2b', activeforeground='#ffffff').pack(pady=5)
    
    def _setup_scanner_tab(self, parent):
        """Setup scanner settings tab"""
        canvas = tk.Canvas(parent, bg='#2b2b2b', highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg='#2b2b2b')
        
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Sorting mode (dropdown)
        self._section_label(frame, "SORTING MODE")
        # internal key var used throughout the code (e.g. 'color', 'price', 'alpha')
        self.sorting_var = tk.StringVar(value="color")

        # Display labels mapped to internal keys
        sorting_options = [
            ("Color", "color"),
            ("Mana Value", "mana_value"),
            ("Set", "set"),
            ("Price", "price"),
            ("Type", "type"),
            ("Buy Mode", "buy"),
            ("Alpha (A-Z)", "alpha"),
            ("Rarity", "rarity"),
            ("Finish (Foil)", "finish")
        ]

        # Combobox shows friendly labels but we store internal keys in self.sorting_var
        display_labels = [label for label, key in sorting_options]
        label_to_key = {label: key for label, key in sorting_options}

        # Create combobox
        self.sorting_combo = ttk.Combobox(frame, values=display_labels, state='readonly', width=28)

        # Set combobox to current sorting_var value (find matching label)
        current_label = next((lbl for lbl, k in sorting_options if k == self.sorting_var.get()), display_labels[0])
        self.sorting_combo.set(current_label)
        self.sorting_combo.pack(fill=tk.X, padx=20, pady=5)

        # When user selects a display label, update the internal sorting_var to the mapped key
        def _on_sort_selected(event=None):
            sel = self.sorting_combo.get()
            if sel in label_to_key:
                self.sorting_var.set(label_to_key[sel])

        self.sorting_combo.bind('<<ComboboxSelected>>', _on_sort_selected)
        
        # Price threshold
        threshold_frame = tk.Frame(frame, bg='#2b2b2b')
        threshold_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(threshold_frame, text="Price Threshold ($):", bg='#2b2b2b', fg='#aaa', font=('Arial', 9)).pack(side=tk.LEFT)
        self.threshold_var = tk.StringVar(value="1000000")
        tk.Entry(threshold_frame, textvariable=self.threshold_var, width=15, 
                bg='#404040', fg='#fff', insertbackground='#fff').pack(side=tk.LEFT, padx=5)
        
        # Filters
        for label, var_name, combo_name in [("GAME FILTER", "game_var", "game_combo"),
                                             ("SET FILTER", "set_var", "set_combo"),
                                             ("RARITY FILTER", "rarity_var", "rarity_combo"),
                                             ("FOIL TYPE", "foil_var", "foil_combo")]:
            self._section_label(frame, label)
            f = tk.Frame(frame, bg='#2b2b2b')
            f.pack(fill=tk.X, padx=20, pady=5)
            setattr(self, var_name, tk.StringVar(value=f"All {label.split()[0]}s" if label != "FOIL TYPE" else "All Foil Types"))
            setattr(self, combo_name, ttk.Combobox(f, textvariable=getattr(self, var_name), state='readonly', width=45))
            getattr(self, combo_name).pack(fill=tk.X, pady=5)
        
        self.game_combo.bind('<<ComboboxSelected>>', self.on_game_change)
        
        # Inventory
        self._section_label(frame, "INVENTORY")
        self.inventory_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame, text="Track Inventory (Reject Duplicates)", variable=self.inventory_var,
                      command=self.toggle_inventory, bg='#2b2b2b', fg='#fff', selectcolor='#404040',
                      font=('Arial', 10), activebackground='#2b2b2b', activeforeground='#fff').pack(anchor=tk.W, padx=20, pady=5)
        
        # Status log
        self._section_label(frame, "STATUS LOG")
        status_frame = tk.Frame(frame, bg='#1a1a1a', relief=tk.SUNKEN, bd=2)
        status_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)
        
        self.status_text = tk.Text(status_frame, height=15, bg='#1a1a1a', fg='#00ff00',
                                   font=('Consolas', 9), wrap=tk.WORD, state=tk.DISABLED)
        status_scroll = tk.Scrollbar(status_frame, command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _setup_arduino_tab(self, parent):
        """Setup Arduino control tab"""
        canvas = tk.Canvas(parent, bg='#2b2b2b', highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg='#2b2b2b')
        
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Connection
        self._section_label(frame, "CONNECTION")
        conn_frame = tk.Frame(frame, bg='#2b2b2b')
        conn_frame.pack(fill=tk.X, padx=20, pady=5)
        
        tk.Label(conn_frame, text="Port:", bg='#2b2b2b', fg='#fff', font=('Arial', 10)).grid(row=0, column=0, sticky=tk.W, padx=5)
        self.serial_port_var = tk.StringVar(value="COM3")
        tk.Entry(conn_frame, textvariable=self.serial_port_var, width=12, bg='#404040', fg='#fff', insertbackground='#fff').grid(row=0, column=1, padx=5)
        
        tk.Label(conn_frame, text="Baud:", bg='#2b2b2b', fg='#fff', font=('Arial', 10)).grid(row=0, column=2, sticky=tk.W, padx=5)
        self.baud_var = tk.StringVar(value="9600")
        tk.Entry(conn_frame, textvariable=self.baud_var, width=10, bg='#404040', fg='#fff', insertbackground='#fff').grid(row=0, column=3, padx=5)
        
        self.connect_btn = tk.Button(frame, text="Connect Arduino", command=self.connect_arduino,
                                     bg='#FF9800', fg='white', font=('Arial', 11, 'bold'),
                                     padx=20, pady=8, cursor='hand2')
        self.connect_btn.pack(pady=10)
        
        # Start/Stop Machine Controls
        self._section_label(frame, "MACHINE STATE CONTROL")
        state_frame = tk.Frame(frame, bg='#2b2b2b')
        state_frame.pack(fill=tk.X, padx=20, pady=10)
        
        self.machine_state_label = tk.Label(state_frame, text="● STOPPED", bg='#2b2b2b', 
                                            fg='#ff4444', font=('Arial', 12, 'bold'))
        self.machine_state_label.pack(pady=5)
        
        btn_row = tk.Frame(state_frame, bg='#2b2b2b')
        btn_row.pack(pady=5)
        
        self.start_machine_btn = tk.Button(btn_row, text="▶ START MACHINE", command=self.start_machine,
                                          bg='#4CAF50', fg='white', font=('Arial', 11, 'bold'),
                                          padx=20, pady=8, cursor='hand2')
        self.start_machine_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_machine_btn = tk.Button(btn_row, text="⏹ STOP MACHINE", command=self.stop_machine,
                                         bg='#f44336', fg='white', font=('Arial', 11, 'bold'),
                                         padx=20, pady=8, cursor='hand2', state=tk.DISABLED)
        self.stop_machine_btn.pack(side=tk.LEFT, padx=5)
        
        tk.Label(state_frame, text="⚠ Manual controls only work when STOPPED", 
                bg='#2b2b2b', fg='#ffaa00', font=('Arial', 9, 'italic')).pack(pady=5)
        
        # Live sensor readings
        self._section_label(frame, "LIVE SENSOR READINGS")
        sensor_frame = tk.Frame(frame, bg='#1a1a1a', relief=tk.SUNKEN, bd=2)
        sensor_frame.pack(fill=tk.X, padx=20, pady=5)
        
        self.range_label = tk.Label(sensor_frame, text="Range Sensor: -- mm", bg='#1a1a1a', fg='#00ff00', font=('Consolas', 11))
        self.range_label.pack(pady=5)
        
        endstop_frame = tk.Frame(sensor_frame, bg='#1a1a1a')
        endstop_frame.pack(pady=5)
        
        self.endstop_labels = {}
        for i, (name, key) in enumerate([("X-Min", "x_min"), ("X-Max", "x_max"), ("Y-Min", "y_min"), 
                                         ("Y-Max", "y_max"), ("Z-Min", "z_min"), ("Z-Max", "z_max")]):
            lbl = tk.Label(endstop_frame, text=f"{name}: ?", bg='#1a1a1a', fg='#888', font=('Consolas', 9), width=10)
            lbl.grid(row=i//3, column=i%3, padx=5, pady=2)
            self.endstop_labels[key] = lbl
        
        self.monitor_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame, text="Enable Live Monitoring", variable=self.monitor_var,
                      command=self.toggle_arduino_monitoring, bg='#2b2b2b', fg='#fff',
                      selectcolor='#404040', font=('Arial', 10)).pack(pady=5)
        
        # Manual controls
        self._section_label(frame, "MANUAL CONTROLS")
        
        self.home_btn = tk.Button(frame, text="🏠 Home Machine", command=self.arduino_home,
                 bg='#9C27B0', fg='white', font=('Arial', 11, 'bold'),
                 padx=20, pady=8, cursor='hand2')
        self.home_btn.pack(pady=5)
        
        # Motor toggles
        motor_frame = tk.Frame(frame, bg='#2b2b2b')
        motor_frame.pack(fill=tk.X, padx=20, pady=10)
        tk.Label(motor_frame, text="Motors & Relays:", bg='#2b2b2b', fg='#fff', font=('Arial', 10, 'bold')).pack(anchor=tk.W)
        
        self.motor_vars = {}
        self.motor_checkboxes = {}
        # Motors enabled by default (LOW = enabled), Vacuums off, Lights on
        defaults = {
            "Xenable": True, "Yenable": True, "Zenable": True, 
            "E0enable": True, "E1enable": True,
            "Vacuum1": False, "Vacuum2": False, "Lights": True
        }
        
        for name in ["Xenable", "Yenable", "Zenable", "E0enable", "E1enable", "Vacuum1", "Vacuum2", "Lights"]:
            var = tk.BooleanVar(value=defaults.get(name, False))
            self.motor_vars[name] = var
            cb = tk.Checkbutton(motor_frame, text=name, variable=var,
                          command=lambda n=name: self.toggle_motor(n), bg='#2b2b2b', fg='#fff',
                          selectcolor='#404040', font=('Arial', 9))
            cb.pack(anchor=tk.W, padx=10)
            self.motor_checkboxes[name] = cb
        
        # Movement controls
        move_frame = tk.Frame(frame, bg='#2b2b2b')
        move_frame.pack(pady=10)
        tk.Label(move_frame, text="Manual Movement:", bg='#2b2b2b', fg='#fff', font=('Arial', 10, 'bold')).pack()
        
        btn_frame = tk.Frame(move_frame, bg='#2b2b2b')
        btn_frame.pack(pady=5)
        
        self.movement_buttons = []
        for row, axis in enumerate([('X', 'CalibrateX'), ('Y', 'CalibrateY')]):
            btn_minus = tk.Button(btn_frame, text=f"{axis[0]}-", command=lambda a=axis[1]: self.send_arduino_command(f"{a}1"),
                     bg='#607D8B', fg='white', font=('Arial', 9), padx=10, pady=5)
            btn_minus.grid(row=row, column=0, padx=2, pady=2)
            self.movement_buttons.append(btn_minus)
            
            tk.Label(btn_frame, text=axis[0], bg='#2b2b2b', fg='#fff', font=('Arial', 10, 'bold'), width=3).grid(row=row, column=1, padx=5)
            
            btn_plus = tk.Button(btn_frame, text=f"{axis[0]}+", command=lambda a=axis[1]: self.send_arduino_command(f"{a}2"),
                     bg='#607D8B', fg='white', font=('Arial', 9), padx=10, pady=5)
            btn_plus.grid(row=row, column=2, padx=2, pady=2)
            self.movement_buttons.append(btn_plus)
        
        # Parameters
        self._section_label(frame, "ADJUSTABLE PARAMETERS")
        params_frame = tk.Frame(frame, bg='#2b2b2b')
        params_frame.pack(fill=tk.X, padx=20, pady=5)
        
        self.param_vars = {}
        params = [
            ("Speed", "speed", "700"),
            ("Z Speed", "zspeed", "75"),
            ("Z E Speed", "zespeed", "120"),
            ("X Cal", "xcal", "350"),
            ("Y Cal", "ycal", "475"),
            ("Z Cal", "zcal", "140"),
            ("Pickup Threshold", "pickup_thresh", "40"),
            ("Release Threshold", "release_thresh", "40"),
            ("Home Cycle Count", "hcc", "10"),
            ("Y Course Correct", "ycc", "1"),
            ("X Course Correct", "xcc", "0")
        ]
        
        for i, (label, key, default) in enumerate(params):
            row_frame = tk.Frame(params_frame, bg='#2b2b2b')
            row_frame.pack(fill=tk.X, pady=2)
            tk.Label(row_frame, text=f"{label}:", bg='#2b2b2b', fg='#aaa', font=('Arial', 9), width=20, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            self.param_vars[key] = var
            tk.Entry(row_frame, textvariable=var, width=10, bg='#404040', fg='#fff', insertbackground='#fff').pack(side=tk.LEFT, padx=5)
        
        tk.Label(frame, text="Note: Parameter changes sent to Arduino on next command", 
                bg='#2b2b2b', fg='#ff9800', font=('Arial', 8, 'italic')).pack(pady=5)
        
        # Parameter sync buttons
        btn_frame = tk.Frame(frame, bg='#2b2b2b')
        btn_frame.pack(pady=10)
        
        self.upload_params_btn = tk.Button(btn_frame, text="📤 Upload Params to Arduino", command=self.upload_params,
                 bg='#4CAF50', fg='white', font=('Arial', 10, 'bold'),
                 padx=15, pady=6, cursor='hand2')
        self.upload_params_btn.pack(side=tk.LEFT, padx=5)
        
        self.fetch_params_btn = tk.Button(btn_frame, text="📥 Fetch Params from Arduino", command=self.fetch_params,
                 bg='#2196F3', fg='white', font=('Arial', 10, 'bold'),
                 padx=15, pady=6, cursor='hand2')
        self.fetch_params_btn.pack(side=tk.LEFT, padx=5)
    
    def _setup_export_tab(self, parent):
        """Setup export & statistics tab"""
        canvas = tk.Canvas(parent, bg='#2b2b2b', highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg='#2b2b2b')
        
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # CSV Export
        self._section_label(frame, "CSV EXPORT")
        
        tk.Checkbutton(frame, text="Auto-Export Each Scan", variable=self.auto_export_var,
                      bg='#2b2b2b', fg='#fff', selectcolor='#404040', font=('Arial', 10),
                      activebackground='#2b2b2b', activeforeground='#fff').pack(anchor=tk.W, padx=20, pady=5)
        
        export_btn_frame = tk.Frame(frame, bg='#2b2b2b')
        export_btn_frame.pack(pady=10)
        
        tk.Button(export_btn_frame, text="📁 Select CSV File", command=self.select_csv_file,
                 bg='#2196F3', fg='white', font=('Arial', 11, 'bold'),
                 padx=15, pady=8, cursor='hand2').pack(side=tk.LEFT, padx=5)
        
        tk.Button(export_btn_frame, text="💾 Export Now", command=self.export_csv_now,
                 bg='#4CAF50', fg='white', font=('Arial', 11, 'bold'),
                 padx=15, pady=8, cursor='hand2').pack(side=tk.LEFT, padx=5)
        
        self.csv_file_label = tk.Label(frame, text="No file selected", bg='#2b2b2b', fg='#aaa', font=('Arial', 9))
        self.csv_file_label.pack(pady=5)
        
        # Statistics
        self._section_label(frame, "SCAN STATISTICS")
        stats_frame = tk.Frame(frame, bg='#1a1a1a', relief=tk.SUNKEN, bd=2)
        stats_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)
        
        self.stats_labels = {}
        for label in ["Total Scans", "Successful", "Rejected", "In Inventory", "Session Time"]:
            lbl_frame = tk.Frame(stats_frame, bg='#1a1a1a')
            lbl_frame.pack(fill=tk.X, pady=3)
            tk.Label(lbl_frame, text=f"{label}:", bg='#1a1a1a', fg='#888', font=('Arial', 10), width=18, anchor=tk.W).pack(side=tk.LEFT, padx=10)
            val_lbl = tk.Label(lbl_frame, text="0", bg='#1a1a1a', fg='#00ff00', font=('Arial', 10, 'bold'))
            val_lbl.pack(side=tk.LEFT)
            self.stats_labels[label] = val_lbl
        
        # Scan history table
        self._section_label(frame, "RECENT SCANS")
        table_frame = tk.Frame(frame, bg='#2b2b2b')
        table_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)
        
        columns = ("Time", "Card", "Game", "Bin", "Price")
        self.scan_table = ttk.Treeview(table_frame, columns=columns, show='headings', height=10)
        
        for col in columns:
            self.scan_table.heading(col, text=col)
            self.scan_table.column(col, width=100 if col != "Card" else 200)
        
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.scan_table.yview)
        self.scan_table.configure(yscrollcommand=table_scroll.set)
        
        self.scan_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        table_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        tk.Button(frame, text="🗑️ Clear History", command=self.clear_history,
                 bg='#f44336', fg='white', font=('Arial', 10, 'bold'),
                 padx=15, pady=6, cursor='hand2').pack(pady=10)
    
    def _section_label(self, parent, text):
        """Create section header"""
        tk.Label(parent, text=text, bg='#2b2b2b', fg='#4CAF50', 
                font=('Arial', 11, 'bold')).pack(anchor=tk.W, padx=15, pady=(10, 5))
    
    def _init_scanner(self):
        """Initialize scanner"""
        try:
            self.scanner = OptimizedCardScanner(max_workers=8, cache_enabled=True)
            self.log_status(f"Scanner initialized: {len(self.scanner.games)} games")
            self._populate_dropdowns()
            self.session_start = time.time()
            self._update_stats()
        except Exception as e:
            self.log_status(f"ERROR: {e}", error=True)
            messagebox.showerror("Scanner Error", f"Failed to initialize:\n{e}")
    
    def _populate_dropdowns(self):
        """Populate dropdowns"""
        if not self.scanner:
            return
        games = ["All Games"] + sorted([info['display_name'] for info in self.scanner.games.values()])
        self.game_combo['values'] = games
        self.set_combo['values'] = ["All Sets"]
        self.rarity_combo['values'] = ["All Rarities"]
        self.foil_combo['values'] = ["All Foil Types"]
        self.log_status(f"Loaded {len(games)-1} games")
    
    def on_game_change(self, event=None):
        """Handle game selection"""
        selected = self.game_var.get()
        if selected == "All Games":
            self.scanner.active_games = list(self.scanner.games.keys())
            self.log_status("Game filter: All")
        else:
            for name, info in self.scanner.games.items():
                if info['display_name'] == selected:
                    self.scanner.set_active_games([name])
                    self.log_status(f"Game: {selected}")
                    self._load_game_data(name)
                    break
    
    def _load_game_data(self, game_name):
        """Load sets/rarities/foils for game"""
        try:
            # Check if game exists in our games dict
            if game_name not in self.scanner.games:
                self.log_status(f"Game '{game_name}' not found in database", error=True)
                return
            
            cursor = self.scanner.get_connection()
            # Sets
            sets = cursor.execute("SELECT DISTINCT code, name FROM sets WHERE game = ? ORDER BY name", (game_name,)).fetchall()
            self.set_combo['values'] = ["All Sets"] + [f"{c} - {n}" for c, n in sets]
            self.set_var.set("All Sets")
            
            # Rarities & foils
            table = self.scanner.games[game_name]['table']
            rarities = cursor.execute(f"SELECT DISTINCT rarity FROM {table} WHERE rarity IS NOT NULL ORDER BY rarity").fetchall()
            self.rarity_combo['values'] = ["All Rarities"] + [r[0] for r in rarities]
            self.rarity_var.set("All Rarities")
            
            foils = cursor.execute(f"SELECT DISTINCT subTypeName FROM {table} WHERE subTypeName IS NOT NULL ORDER BY subTypeName").fetchall()
            self.foil_combo['values'] = ["All Foil Types"] + [f[0] for f in foils]
            self.foil_var.set("All Foil Types")
        except Exception as e:
            self.log_status(f"Error loading game data: {e}", error=True)
    
    def toggle_auto_scan(self):
        """Toggle auto-scan"""
        self.auto_scan = self.auto_scan_var.get()
        self.log_status(f"Auto-scan {'ON' if self.auto_scan else 'OFF'}")
    
    def toggle_inventory(self):
        """Toggle inventory tracking"""
        if self.scanner:
            enabled = self.inventory_var.get()
            self.scanner.enable_inventory_tracking(enabled)
            self.log_status(f"Inventory tracking {'ON' if enabled else 'OFF'}")
    
    def connect_arduino(self):
        """Connect to Arduino"""
        if not self.scanner:
            return
        port = self.serial_port_var.get()
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid baud rate")
            return
        
        self.scanner.serial_port = port
        self.scanner.baud_rate = baud
        
        if self.scanner.init_serial():
            self.log_status(f"✓ Arduino connected: {port} @ {baud}")
            self.connect_btn.configure(text="Reconnect", bg='#4CAF50')
        else:
            self.log_status(f"✗ Connection failed: {port}", error=True)
            messagebox.showerror("Error", f"Cannot connect to {port}")
    
    def arduino_home(self):
        """Send home command"""
        self.send_arduino_command("HomeButton")
    
    def start_machine(self):
        """Start the machine - enable card sorting"""
        if not self.scanner or not self.scanner.ser:
            self.log_status("Arduino not connected", error=True)
            messagebox.showerror("Error", "Connect Arduino first")
            return
        
        response = self.scanner.send_to_arduino("StartMachine")
        if response and "OK" in response:
            self.machine_started = True
            self.machine_state_label.configure(text="● STARTED", fg='#44ff44')
            self.start_machine_btn.configure(state=tk.DISABLED)
            self.stop_machine_btn.configure(state=tk.NORMAL)
            self._update_controls_state()
            self.log_status("✓ Machine STARTED - Accepting cards")
        else:
            self.log_status("✗ Failed to start machine", error=True)
            messagebox.showerror("Error", "Failed to start machine")
    
    def stop_machine(self):
        """Stop the machine - enable manual controls"""
        if not self.scanner or not self.scanner.ser:
            self.log_status("Arduino not connected", error=True)
            return
        
        response = self.scanner.send_to_arduino("StopMachine")
        if response and "OK" in response:
            self.machine_started = False
            self.machine_state_label.configure(text="● STOPPED", fg='#ff4444')
            self.start_machine_btn.configure(state=tk.NORMAL)
            self.stop_machine_btn.configure(state=tk.DISABLED)
            self._update_controls_state()
            self.log_status("✓ Machine STOPPED - Manual controls enabled")
        else:
            self.log_status("✗ Failed to stop machine", error=True)
            messagebox.showerror("Error", "Failed to stop machine")
    
    def _update_controls_state(self):
        """Enable/disable controls based on machine state"""
        # When Started: disable all manual controls
        # When Stopped: enable all manual controls
        state = tk.DISABLED if self.machine_started else tk.NORMAL
        
        # Disable/enable manual controls
        self.home_btn.configure(state=state)
        
        for checkbox in self.motor_checkboxes.values():
            checkbox.configure(state=state)
        
        for button in self.movement_buttons:
            button.configure(state=state)
        
        self.upload_params_btn.configure(state=state)
        self.fetch_params_btn.configure(state=state)
    
    def toggle_motor(self, name):
        """Toggle motor/relay"""
        if self.machine_started:
            self.log_status("⚠ Cannot control motors while machine is started", error=True)
            # Revert checkbox
            self.motor_vars[name].set(not self.motor_vars[name].get())
            return
        
        if not self.scanner or not self.scanner.ser:
            self.log_status("Arduino not connected", error=True)
            return
        
        state = 1 if self.motor_vars[name].get() else 0
        response = self.scanner.send_to_arduino(f"SetMotor,{name},{state}")
        
        if response and "OK" in response:
            self.log_status(f"✓ {name}: {'ON' if state else 'OFF'}")
        elif response and "MachineStarted" in response:
            self.log_status(f"⚠ Machine is started - stop first", error=True)
            self.motor_vars[name].set(not state)
        else:
            self.log_status(f"✗ {name} control failed", error=True)
            # Revert checkbox if failed
            self.motor_vars[name].set(not state)
    
    def send_arduino_command(self, cmd):
        """Send command to Arduino"""
        # Check if manual control command while machine is started
        if self.machine_started and cmd in ["HomeButton", "CalibrateX1", "CalibrateX2", "CalibrateY1", "CalibrateY2"]:
            self.log_status("⚠ Cannot send manual commands while machine is started", error=True)
            messagebox.showwarning("Machine Started", "Stop the machine first to use manual controls")
            return
        
        if self.scanner and self.scanner.ser:
            response = self.scanner.send_to_arduino(cmd)
            self.log_status(f"→ {cmd}")
            if response:
                self.log_status(f"← {response}")
                if "MachineStarted" in response:
                    messagebox.showwarning("Machine Started", "Machine must be STOPPED for this command")
        else:
            self.log_status("Arduino not connected", error=True)
    
    def toggle_arduino_monitoring(self):
        """Start/stop Arduino monitoring"""
        if self.monitor_var.get():
            if not self.scanner or not self.scanner.ser:
                self.log_status("Connect Arduino first", error=True)
                self.monitor_var.set(False)
                return
            self.arduino_monitoring = True
            self.arduino_monitor_thread = threading.Thread(target=self._arduino_monitor_loop, daemon=True)
            self.arduino_monitor_thread.start()
            self.log_status("Arduino monitoring started")
        else:
            self.arduino_monitoring = False
            self.log_status("Arduino monitoring stopped")
    
    def upload_params(self):
        """Upload all parameters to Arduino"""
        if not self.scanner or not self.scanner.ser:
            self.log_status("Arduino not connected", error=True)
            return
        
        if self.machine_started:
            self.log_status("⚠ Cannot upload params while machine is started", error=True)
            messagebox.showwarning("Machine Started", 
                "Machine must be STOPPED to upload parameters.\n\nClick 'Stop Machine' first.")
            return
        
        param_mapping = {
            "speed": "speed",
            "zspeed": "zspeed", 
            "zespeed": "zespeed",
            "xcal": "xcal",
            "ycal": "ycal",
            "zcal": "zcal",
            "pickup_thresh": "pickup_thresh",
            "release_thresh": "release_thresh",
            "hcc": "hcc",
            "ycc": "ycc",
            "xcc": "xcc"
        }
        
        success_count = 0
        for gui_key, arduino_key in param_mapping.items():
            try:
                value = self.param_vars[gui_key].get()
                response = self.scanner.send_to_arduino(f"SetParam,{arduino_key},{value}")
                
                if response and "OK" in response:
                    success_count += 1
                elif response and "MachineStarted" in response:
                    self.log_status("⚠ Machine is started - stop first", error=True)
                    messagebox.showwarning("Machine Started", 
                        "Machine must be STOPPED to upload parameters.\n\nClick 'Stop Machine' first.")
                    return
                elif response and "NotAtHome" in response:
                    self.log_status("⚠ Arduino not at home position - params rejected", error=True)
                    messagebox.showwarning("Not At Home", 
                        "Arduino must be at home position to accept parameter changes.\n\nClick 'Home Machine' first.")
                    return
                else:
                    self.log_status(f"✗ Failed to set {gui_key}", error=True)
                
                time.sleep(0.05)  # Small delay between commands
            except Exception as e:
                self.log_status(f"✗ Error setting {gui_key}: {e}", error=True)
        
        if success_count == len(param_mapping):
            self.log_status(f"✓ Uploaded {success_count} parameters to Arduino")
            messagebox.showinfo("Success", f"Uploaded {success_count} parameters to Arduino")
        else:
            self.log_status(f"⚠ Uploaded {success_count}/{len(param_mapping)} parameters", error=True)
    
    def fetch_params(self):
        """Fetch current parameters from Arduino"""
        if not self.scanner or not self.scanner.ser:
            self.log_status("Arduino not connected", error=True)
            return
        
        try:
            response = self.scanner.send_to_arduino("QueryParams")
            
            if response and "Params" in response:
                # Parse response: <Params,speed=700,zspeed=75,...>
                parts = response.replace("<", "").replace(">", "").split(",")
                
                param_mapping = {
                    "speed": "speed",
                    "zspeed": "zspeed",
                    "zespeed": "zespeed",
                    "xcal": "xcal",
                    "ycal": "ycal",
                    "zcal": "zcal",
                    "pickup_thresh": "pickup_thresh",
                    "release_thresh": "release_thresh",
                    "hcc": "hcc",
                    "ycc": "ycc",
                    "xcc": "xcc"
                }
                
                fetched = 0
                for part in parts[1:]:  # Skip "Params"
                    if "=" in part:
                        key, value = part.split("=")
                        if key in param_mapping:
                            gui_key = param_mapping[key]
                            self.param_vars[gui_key].set(value)
                            fetched += 1
                
                self.log_status(f"✓ Fetched {fetched} parameters from Arduino")
                messagebox.showinfo("Success", f"Fetched {fetched} parameters from Arduino")
            else:
                self.log_status("✗ Failed to fetch parameters", error=True)
        except Exception as e:
            self.log_status(f"✗ Fetch error: {e}", error=True)
    
    def _arduino_monitor_loop(self):
        """Monitor Arduino sensors"""
        while self.arduino_monitoring:
            if self.scanner and self.scanner.ser:
                try:
                    # Query sensor data from Arduino
                    response = self.scanner.send_to_arduino("QuerySensors")
                    
                    if response and "Sensors" in response:
                        # Parse response: <Sensors,range=45,xmin=0,xmax=1,ymin=0,ymax=1,zmin=0,zmax=1,home=1,started=0>
                        parts = response.replace("<", "").replace(">", "").split(",")
                        for part in parts[1:]:  # Skip "Sensors"
                            if "=" in part:
                                key, value = part.split("=")
                                if key == "range":
                                    self.sensor_data['range'] = int(value)
                                    self.range_label.configure(text=f"Range Sensor: {value} mm")
                                elif key == "started":
                                    # Update machine state from Arduino
                                    started_state = int(value) == 1
                                    if started_state != self.machine_started:
                                        self.machine_started = started_state
                                        if started_state:
                                            self.machine_state_label.configure(text="● STARTED", fg='#44ff44')
                                            self.start_machine_btn.configure(state=tk.DISABLED)
                                            self.stop_machine_btn.configure(state=tk.NORMAL)
                                        else:
                                            self.machine_state_label.configure(text="● STOPPED", fg='#ff4444')
                                            self.start_machine_btn.configure(state=tk.NORMAL)
                                            self.stop_machine_btn.configure(state=tk.DISABLED)
                                        self._update_controls_state()
                                elif key in self.sensor_data:
                                    val = int(value)
                                    self.sensor_data[key] = val
                                    if key in self.endstop_labels:
                                        label = self.endstop_labels[key]
                                        status = "●" if val == 1 else "○"
                                        color = "#00ff00" if val == 1 else "#888888"
                                        name = key.replace("_", "-").upper()
                                        label.configure(text=f"{name}: {status}", fg=color)
                except Exception as e:
                    self.log_status(f"Monitor error: {e}", error=True)
            
            time.sleep(0.5)
    
    def select_csv_file(self):
        """Select CSV export file"""
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"scanner_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if filename:
            self.csv_file = filename
            self.csv_file_label.configure(text=filename, fg='#00ff00')
            self.log_status(f"CSV file: {filename}")
    
    def export_csv_now(self):
        """Export current scan history to CSV"""
        if not self.csv_file:
            self.select_csv_file()
            if not self.csv_file:
                return
        
        try:
            with open(self.csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Card Name", "Game", "Set", "Rarity", "Foil Type", 
                               "Confidence", "Market Price", "Bin Assignment", "Sorting Mode"])
                writer.writerows(self.scan_history)
            
            self.log_status(f"✓ Exported {len(self.scan_history)} scans to CSV")
            messagebox.showinfo("Export Complete", f"Exported {len(self.scan_history)} scans to:\n{self.csv_file}")
        except Exception as e:
            self.log_status(f"✗ Export failed: {e}", error=True)
            messagebox.showerror("Export Error", str(e))
    
    def _add_to_history(self, card_info, bin_result, sorting_mode):
        """Add scan to history and optionally export"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        name = card_info.get('name', 'Unknown')
        game = card_info.get('game', 'Unknown')
        set_code = card_info.get('set', 'N/A')
        rarity = card_info.get('rarity', 'N/A')
        foil = card_info.get('foil_type', 'N/A')
        confidence = card_info.get('confidence', 0)
        price = card_info.get('market_price', 0)
        
        row = [timestamp, name, game, set_code, rarity, foil, f"{confidence:.1f}%", 
               f"${price:.2f}" if price else "N/A", bin_result, sorting_mode]
        
        self.scan_history.append(row)
        
        # Add to table
        self.scan_table.insert('', 0, values=(timestamp.split()[1], name[:30], game[:15], bin_result, f"${price:.2f}" if price else "N/A"))
        
        # Auto export
        if self.auto_export_var.get() and self.csv_file:
            try:
                with open(self.csv_file, 'a', newline='', encoding='utf-8') as f:
                    csv.writer(f).writerow(row)
            except Exception as e:
                self.log_status(f"Auto-export failed: {e}", error=True)
        
        self._update_stats()
    
    def _update_stats(self):
        """Update statistics display"""
        total = len(self.scan_history)
        rejected = sum(1 for row in self.scan_history if row[8] == "RejectCard")
        successful = total - rejected
        
        self.stats_labels["Total Scans"].configure(text=str(total))
        self.stats_labels["Successful"].configure(text=str(successful))
        self.stats_labels["Rejected"].configure(text=str(rejected))
        
        if hasattr(self, 'session_start'):
            elapsed = int(time.time() - self.session_start)
            hours = elapsed // 3600
            minutes = (elapsed % 3600) // 60
            seconds = elapsed % 60
            self.stats_labels["Session Time"].configure(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        
        # Schedule next update
        self.root.after(1000, self._update_stats)
    
    def clear_history(self):
        """Clear scan history"""
        if messagebox.askyesno("Clear History", "Clear all scan history?"):
            self.scan_history.clear()
            for item in self.scan_table.get_children():
                self.scan_table.delete(item)
            self.log_status("History cleared")
            self._update_stats()
    
    def start_camera(self):
        """Start camera"""
        if self.running:
            return
        self.camera = cv2.VideoCapture(0)
        if not self.camera.isOpened():
            self.log_status("✗ Cannot open camera", error=True)
            messagebox.showerror("Error", "Cannot open camera")
            return
        
        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.log_status("✓ Camera started")
        threading.Thread(target=self._video_loop, daemon=True).start()
    
    def stop_camera(self):
        """Stop camera"""
        self.running = False
        if self.camera:
            self.camera.release()
            self.camera = None
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.log_status("⏸ Camera stopped")
    
    def _video_loop(self):
        """Video processing loop"""
        while self.running:
            ret, frame = self.camera.read()
            if not ret:
                break
            
            self.current_frame = frame.copy()
            display_frame = frame.copy()
            
            card_approx = self.scanner._find_card_contour(frame)
            
            if card_approx is not None:
                cv2.drawContours(display_frame, [card_approx], -1, (0, 255, 0), 3)
                cv2.putText(display_frame, "CARD DETECTED", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                current_time = time.time()
                if self.auto_scan and (current_time - self.last_scan_time) > self.scan_cooldown:
                    self.last_scan_time = current_time
                    self._perform_scan(frame, card_approx)
            else:
                cv2.putText(display_frame, "NO CARD", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            
            if self.detection_info:
                self._draw_detection_info(display_frame, self.detection_info)
            
            self._update_canvas(display_frame)
            time.sleep(0.03)
    
    def manual_scan(self):
        """Manual scan"""
        if not self.running or self.current_frame is None:
            self.log_status("Start camera first", error=True)
            return
        
        card_approx = self.scanner._find_card_contour(self.current_frame)
        if card_approx is not None:
            self._perform_scan(self.current_frame, card_approx)
        else:
            self.log_status("✗ No card detected", error=True)
    
    def _perform_scan(self, frame, card_approx):
        """Scan card"""
        try:
            card_info = self.scanner._process_card_from_contour(frame, card_approx)
            
            if not card_info:
                self.detection_info = {'status': 'error', 'message': 'Not recognized', 'bin': 'RejectCard'}
                self.log_status("✗ Card not recognized")
                return
            
            if self.scanner.track_inventory:
                card_info = self.scanner.check_inventory(card_info)
                if card_info == "RejectCard":
                    self.detection_info = {'status': 'reject', 'message': 'In inventory', 'bin': 'RejectCard'}
                    self.log_status("⊗ Already in inventory")
                    if self.scanner.ser:
                        self.scanner.send_to_arduino("RejectCard")
                    return
            
            sorting_mode = self.sorting_var.get()
            try:
                threshold = float(self.threshold_var.get())
            except:
                threshold = 1000000
            
            bin_result = self.scanner.get_bin_number(card_info, sorting_mode, threshold)
            
            self.detection_info = {'status': 'success', 'card': card_info, 'bin': bin_result, 'sorting_mode': sorting_mode}
            
            name = card_info.get('name', 'Unknown')
            game = card_info.get('game', 'Unknown')
            confidence = card_info.get('confidence', 0)
            price = card_info.get('market_price')
            
            self.log_status(f"✓ {name} ({game}) {confidence:.1f}% → {bin_result}")
            
            # Add to history
            self._add_to_history(card_info, bin_result, sorting_mode)
            
            if self.scanner.ser:
                response = self.scanner.send_to_arduino(bin_result)
                if response:
                    self.log_status(f"  ← Arduino: {response}")
        
        except Exception as e:
            self.log_status(f"✗ Scan error: {e}", error=True)
    
    def _draw_detection_info(self, frame, info):
        """Draw info on frame"""
        if info['status'] == 'success':
            card = info['card']
            y = 70
            cv2.putText(frame, f"Card: {card.get('name', 'Unknown')}", (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            y += 30
            cv2.putText(frame, f"Game: {card.get('game', 'Unknown')}", (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            y += 30
            cv2.putText(frame, f"Confidence: {card.get('confidence', 0):.1f}%", (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            y += 30
            if card.get('market_price'):
                cv2.putText(frame, f"Price: ${card['market_price']:.2f}", (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                y += 30
            cv2.putText(frame, f"BIN: {info['bin']}", (10, y + 20), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        else:
            cv2.putText(frame, info.get('message', 'Error').upper(), (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(frame, f"BIN: {info['bin']}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    
    def _update_canvas(self, frame):
        """Update canvas"""
        h, w = frame.shape[:2]
        scale = min(self.canvas_width / w, self.canvas_height / h)
        resized = cv2.resize(frame, (int(w * scale), int(h * scale)))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        self.canvas.delete("all")
        x_offset = (self.canvas_width - int(w * scale)) // 2
        y_offset = (self.canvas_height - int(h * scale)) // 2
        self.canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=photo)
        self.canvas.image = photo
    
    def log_status(self, message, error=False):
        """Log to status window"""
        self.status_text.configure(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.status_text.tag_config("error", foreground="#ff4444")
        self.status_text.tag_config("normal", foreground="#00ff00")
        self.status_text.insert(tk.END, f"[{timestamp}] {message}\n", "error" if error else "normal")
        self.status_text.see(tk.END)
        self.status_text.configure(state=tk.DISABLED)
    
    def on_close(self):
        """Cleanup"""
        self.running = False
        self.arduino_monitoring = False
        if self.camera:
            self.camera.release()
        if self.scanner:
            self.scanner.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ScannerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
